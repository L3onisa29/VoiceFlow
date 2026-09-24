use crate::{capture::Capture, platform, settings::Settings};
use anyhow::{Result, anyhow, ensure};
use crossbeam_channel::{Receiver, Sender, bounded, select_biased, tick};
use serde::Serialize;
use std::{
    path::PathBuf,
    sync::{Arc, Mutex, atomic::Ordering},
    time::{Duration, Instant},
};
use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, ShortcutState};
use voiceflow_core::{
    audio::StreamingResampler,
    formatting::format_transcript,
    streaming::{MODEL_FILES, NvidiaDecoder, StreamingSession, Transcript, load_model},
};

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Snapshot {
    pub revision: u64,
    pub phase: String,
    pub model_ready: bool,
    pub model_present: bool,
    pub transcript: Transcript,
    pub final_text: String,
    pub message: Option<String>,
    pub level: f32,
    pub device: String,
    pub settings: Settings,
}

pub enum Command {
    Load,
    Toggle,
    Stop,
    Cancel,
    Settings(Settings),
    Quit,
}
#[derive(Clone)]
pub struct Service {
    sender: Sender<Command>,
    snapshot: Arc<Mutex<Snapshot>>,
}
impl Service {
    pub fn new(app: AppHandle, model_path: PathBuf, settings_path: PathBuf) -> Result<Self> {
        let loaded = Settings::load(&settings_path);
        let message = loaded
            .as_ref()
            .err()
            .map(|e| format!("Impostazioni non leggibili: {e}"));
        let snapshot = Arc::new(Mutex::new(Snapshot {
            revision: 0,
            phase: "idle".into(),
            model_ready: false,
            model_present: MODEL_FILES
                .iter()
                .all(|file| model_path.join(file).is_file()),
            transcript: Transcript::default(),
            final_text: String::new(),
            message,
            level: 0.0,
            device: "Microfono di sistema".into(),
            settings: loaded.unwrap_or_default(),
        }));
        let (sender, receiver) = bounded(16);
        let service = Self {
            sender,
            snapshot: Arc::clone(&snapshot),
        };
        std::thread::Builder::new()
            .name("voiceflow-dictation".into())
            .spawn(move || {
                let mut worker = Worker {
                    app,
                    snapshot,
                    model_path,
                    settings_path,
                    receiver,
                    model: None,
                    active: None,
                };
                worker.run();
            })?;
        Ok(service)
    }
    pub fn send(&self, command: Command) -> Result<()> {
        self.sender
            .try_send(command)
            .map_err(|_| anyhow!("VoiceFlow è occupato, riprova tra un istante"))
    }
    pub fn snapshot(&self) -> Snapshot {
        self.snapshot.lock().unwrap().clone()
    }
}
struct Active {
    capture: Capture,
    resampler: StreamingResampler,
    session: StreamingSession<NvidiaDecoder>,
    emitted: Instant,
    target: Option<platform::FocusTarget>,
}
struct Worker {
    app: AppHandle,
    snapshot: Arc<Mutex<Snapshot>>,
    model_path: PathBuf,
    settings_path: PathBuf,
    receiver: Receiver<Command>,
    model: Option<parakeet_handle::Handle>,
    active: Option<Active>,
}
// The type alias keeps the model implementation out of the application protocol.
mod parakeet_handle {
    pub type Handle = voiceflow_core::streaming::ModelHandle;
}
impl Worker {
    fn update(&self, f: impl FnOnce(&mut Snapshot)) {
        let snapshot = {
            let mut s = self.snapshot.lock().unwrap();
            f(&mut s);
            s.revision += 1;
            s.clone()
        };
        let _ = self.app.emit("dictation-state", snapshot);
    }
    fn run(&mut self) {
        let heartbeat = tick(Duration::from_millis(100));
        loop {
            let audio = self
                .active
                .as_ref()
                .map(|a| a.capture.frames.clone())
                .unwrap_or_else(crossbeam_channel::never);
            select_biased! {
                recv(self.receiver) -> cmd => {
                    match cmd {
                        Ok(Command::Quit) | Err(_) => break,
                        Ok(command) => if let Err(error) = self.command(command) {
                            if self.active.is_some() { self.update(|s| s.message = Some(error.to_string())); }
                            else { self.fail(error); }
                        },
                    }
                }
                recv(audio) -> frame => {
                    if let Ok(frame) = frame {
                        if self.active.as_ref().is_some_and(|a| a.capture.failed.load(Ordering::Acquire)) {
                            self.fail(anyhow!("Acquisizione interrotta: audio incompleto. Il testo parziale è recuperabile."));
                        } else if let Err(error) = self.audio(&frame) { self.fail(error); }
                    }
                }
                recv(heartbeat) -> _ => {
                    if self.active.as_ref().is_some_and(|a| a.capture.failed.load(Ordering::Acquire)) {
                        self.fail(anyhow!("Acquisizione interrotta: microfono non disponibile o elaborazione troppo lenta. Il testo parziale è recuperabile."));
                    }
                }
            }
        }
        self.active = None;
    }
    fn fail(&mut self, error: anyhow::Error) {
        let _ = self.app.global_shortcut().unregister("Control+Space");
        if let Some(active) = self.active.take() {
            self.update(|s| {
                s.transcript = active.session.snapshot();
                s.final_text = s.transcript.text();
            });
        }
        self.update(|s| {
            s.phase = "error".into();
            s.level = 0.0;
            s.message = Some(error.to_string());
        });
    }
    fn command(&mut self, command: Command) -> Result<()> {
        match command {
            Command::Load => {
                ensure!(self.active.is_none(), "Termina prima la registrazione");
                self.update(|s| {
                    s.phase = "loading".into();
                    s.message = None;
                });
                if self.model.is_none() {
                    self.model = Some(load_model(&self.model_path)?);
                }
                self.update(|s| {
                    s.phase = "idle".into();
                    s.model_ready = true;
                    s.model_present = true;
                });
            }
            Command::Toggle if self.active.is_some() => return self.stop(),
            Command::Toggle => {
                ensure!(
                    platform::permissions().microphone,
                    "Consenti a VoiceFlow di usare il microfono nelle impostazioni Privacy di macOS"
                );
                let model = self
                    .model
                    .as_ref()
                    .ok_or_else(|| anyhow!("Carica prima il modello NVIDIA"))?;
                let settings = self.snapshot.lock().unwrap().settings.clone();
                let decoder = NvidiaDecoder::new(model, settings.language)?;
                let target = platform::focused_target();
                let capture = Capture::start()?;
                let resampler = StreamingResampler::new(capture.sample_rate)?;
                self.app
                    .global_shortcut()
                    .on_shortcut("Control+Space", |app, _, event| {
                        if event.state == ShortcutState::Pressed {
                            let _ = app.state::<Service>().send(Command::Cancel);
                        }
                    })?;
                self.update(|s| {
                    s.phase = "recording".into();
                    s.transcript = Transcript::default();
                    s.final_text.clear();
                    s.message = None;
                    s.device = capture.device_name.clone();
                });
                self.active = Some(Active {
                    capture,
                    resampler,
                    session: StreamingSession::new(decoder),
                    emitted: Instant::now(),
                    target,
                });
            }
            Command::Stop => return self.stop(),
            Command::Cancel => {
                if self.active.take().is_some() {
                    let _ = self.app.global_shortcut().unregister("Control+Space");
                    self.update(|s| {
                        s.phase = "idle".into();
                        s.transcript = Transcript::default();
                        s.final_text.clear();
                        s.level = 0.0;
                        s.message = Some("Dettatura annullata".into());
                    });
                }
            }
            Command::Settings(settings) => {
                ensure!(self.active.is_none(), "Termina prima la registrazione");
                settings.save(&self.settings_path)?;
                self.update(|s| {
                    s.settings = settings;
                });
            }
            Command::Quit => {}
        }
        Ok(())
    }
    fn audio(&mut self, frame: &[f32]) -> Result<()> {
        let Some(active) = self.active.as_mut() else {
            return Ok(());
        };
        let level = (frame.iter().map(|v| v * v).sum::<f32>() / frame.len().max(1) as f32).sqrt();
        active.session.push(&active.resampler.push(frame)?)?;
        if active.emitted.elapsed() >= Duration::from_millis(80) {
            active.emitted = Instant::now();
            let transcript = active.session.snapshot();
            self.update(|s| {
                s.transcript = transcript;
                s.level = level;
            });
        }
        Ok(())
    }
    fn stop(&mut self) -> Result<()> {
        let Some(mut active) = self.active.take() else {
            return Ok(());
        };
        let _ = self.app.global_shortcut().unregister("Control+Space");
        // Pause before draining. No callback can extend the tail indefinitely.
        self.update(|s| {
            s.phase = "finalizing".into();
            s.level = 0.0;
        });
        let finalized = (|| -> Result<Transcript> {
            active.capture.stop()?;
            ensure!(
                !active.capture.failed.load(Ordering::Acquire),
                "Audio incompleto: il microfono si è interrotto o l’elaborazione è troppo lenta. Recupera il testo parziale."
            );
            for frame in active.capture.frames.try_iter() {
                active.session.push(&active.resampler.push(&frame)?)?;
            }
            active.session.push(&active.resampler.finish()?)?;
            active.session.finish()
        })();
        let transcript = match finalized {
            Ok(transcript) => transcript,
            Err(error) => {
                self.update(|s| {
                    s.transcript = active.session.snapshot();
                    s.final_text = s.transcript.text();
                });
                return Err(error);
            }
        };
        let settings = self.snapshot.lock().unwrap().settings.clone();
        let final_text = format_transcript(&transcript.text(), settings.format);
        let message = if final_text.is_empty() {
            "Nessun parlato rilevato".into()
        } else if let Some(target) = active.target {
            match platform::paste_text(&target, &final_text) {
                Ok(()) => "Testo inviato al campo attivo".into(),
                Err(error) => format!("Testo pronto. {error}"),
            }
        } else {
            "Testo pronto da copiare. Usa ⌥ Spazio da un’altra app per incollare alla fine.".into()
        };
        self.update(|s| {
            s.phase = "complete".into();
            s.transcript = transcript;
            s.final_text = final_text;
            s.message = Some(message);
        });
        Ok(())
    }
}
