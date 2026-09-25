use crate::{
    capture::Capture,
    history::{CompletedDictation, HistoryStore, ProcessingMetadata},
    platform,
    settings::Settings,
};
use anyhow::{Context, Result, anyhow, ensure};
use crossbeam_channel::{Receiver, Sender, bounded, select_biased, tick};
use serde::Serialize;
use std::{
    path::PathBuf,
    sync::{Arc, Mutex, atomic::Ordering},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, ShortcutState};
use voiceflow_core::{
    audio::StreamingResampler,
    streaming::{
        MODEL_FILES, NvidiaDecoder, StreamingDecoder, StreamingSession, Transcript, load_model,
    },
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
    pub history_error: Option<String>,
    pub level: f32,
    pub elapsed_seconds: u64,
    pub device: String,
    pub settings: Settings,
}

pub enum Command {
    Load,
    Toggle,
    Stop,
    Pause,
    Cancel,
    Settings(Settings),
    Quit,
}
#[derive(Clone)]
pub struct Service {
    sender: Sender<Command>,
    cancel_sender: Sender<()>,
    snapshot: Arc<Mutex<Snapshot>>,
}
impl Service {
    pub fn new(
        app: AppHandle,
        model_path: PathBuf,
        settings_path: PathBuf,
        history: HistoryStore,
    ) -> Result<Self> {
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
            history_error: None,
            level: 0.0,
            elapsed_seconds: 0,
            device: "Microfono di sistema".into(),
            settings: loaded.unwrap_or_default(),
        }));
        let (sender, receiver) = bounded(16);
        let (cancel_sender, cancel_receiver) = bounded(1);
        let service = Self {
            sender,
            cancel_sender,
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
                    history,
                    receiver,
                    cancel_receiver,
                    model: None,
                    active: None,
                };
                worker.run();
            })?;
        Ok(service)
    }
    pub fn send(&self, command: Command) -> Result<()> {
        // Cancellation is not ordinary work: a full command queue must never
        // delay or reject it. Multiple requests cancel the same pending session.
        if matches!(command, Command::Cancel) {
            return match self.cancel_sender.try_send(()) {
                Ok(()) | Err(crossbeam_channel::TrySendError::Full(())) => Ok(()),
                Err(_) => Err(anyhow!("VoiceFlow non è disponibile")),
            };
        }
        self.sender
            .try_send(command)
            .map_err(|_| anyhow!("VoiceFlow è occupato, riprova tra un istante"))
    }
    pub fn snapshot(&self) -> Snapshot {
        self.snapshot.lock().unwrap().clone()
    }
}
struct Active {
    session_key: String,
    created_at: i64,
    capture: Option<Capture>,
    resampler: StreamingResampler,
    session: StreamingSession<NvidiaDecoder>,
    emitted: Instant,
    clock: RecordingClock,
    language: String,
    processing: ProcessingMetadata,
}
struct Worker {
    app: AppHandle,
    snapshot: Arc<Mutex<Snapshot>>,
    model_path: PathBuf,
    settings_path: PathBuf,
    history: HistoryStore,
    receiver: Receiver<Command>,
    cancel_receiver: Receiver<()>,
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
        crate::hud::update(&self.app, snapshot.clone());
        let _ = self.app.emit("dictation-state", snapshot);
    }
    fn run(&mut self) {
        let heartbeat = tick(Duration::from_millis(100));
        loop {
            let audio = self
                .active
                .as_ref()
                .and_then(|a| a.capture.as_ref().map(|c| c.frames.clone()))
                .unwrap_or_else(crossbeam_channel::never);
            select_biased! {
                recv(self.cancel_receiver) -> cancelled => {
                    if cancelled.is_err() { break; }
                    self.cancel();
                }
                recv(self.receiver) -> cmd => {
                    match cmd {
                        Ok(Command::Quit) => {
                            // Restore the speaker state before Tauri terminates.
                            self.active = None;
                            self.app.exit(0);
                            break;
                        }
                        Err(_) => break,
                        Ok(command) => if let Err(error) = self.command(command) {
                            if self.active.is_some() { self.update(|s| s.message = Some(error.to_string())); }
                            else { self.fail(error); }
                        },
                    }
                }
                recv(audio) -> frame => {
                    if let Ok(frame) = frame {
                        if self.active.as_ref().is_some_and(|a| a.capture.as_ref().is_some_and(|c| c.failed.load(Ordering::Acquire))) {
                            self.fail(anyhow!("Acquisizione interrotta: audio incompleto. Il testo parziale è recuperabile."));
                        } else if let Err(error) = self.audio(&frame) { self.fail(error); }
                    }
                }
                recv(heartbeat) -> _ => {
                    if self.active.as_ref().is_some_and(|a| a.capture.as_ref().is_some_and(|c| c.failed.load(Ordering::Acquire))) {
                        self.fail(anyhow!("Acquisizione interrotta: microfono non disponibile o elaborazione troppo lenta. Il testo parziale è recuperabile."));
                    } else if let Some(active) = &self.active {
                        let elapsed = active.clock.elapsed(Instant::now()).as_secs();
                        let previous = self.snapshot.lock().unwrap().elapsed_seconds;
                        if elapsed != previous { self.update(|s| s.elapsed_seconds = elapsed); }
                    }
                }
            }
        }
        self.active = None;
    }
    fn fail(&mut self, error: anyhow::Error) {
        if self.take_cancellation() {
            return;
        }
        // A recording error can win the race just before Ctrl+Space arrives.
        // Keep cancellation available until that recoverable session is dismissed.
        let phase = self.snapshot.lock().unwrap().phase.clone();
        if self.active.is_none() && !matches!(phase.as_str(), "recording" | "paused" | "finalizing")
        {
            let _ = self.app.global_shortcut().unregister("Control+Space");
        }
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
                let (mut processing, dictionary, dictionary_warning) = processing_snapshot(
                    &settings,
                    &self.app.state::<crate::dictionary::DictionaryStore>(),
                )?;
                self.update(|s| {
                    s.phase = "loading".into();
                    s.message = None;
                    s.level = 0.0;
                    s.elapsed_seconds = 0;
                });
                let decoder = NvidiaDecoder::new(model, settings.language)?;
                let capture = Capture::start()?;
                let resampler = StreamingResampler::new(capture.sample_rate)?;
                if !self.app.global_shortcut().is_registered("Control+Space") {
                    self.app
                        .global_shortcut()
                        .on_shortcut("Control+Space", |app, _, event| {
                            if event.state == ShortcutState::Pressed {
                                let _ = app.state::<Service>().send(Command::Cancel);
                            }
                        })?;
                }
                self.update(|s| {
                    s.phase = "recording".into();
                    s.transcript = Transcript::default();
                    s.final_text.clear();
                    s.message = dictionary_warning;
                    s.device = capture.device_name.clone();
                    s.elapsed_seconds = 0;
                    s.level = 0.0;
                });
                let started = SystemTime::now().duration_since(UNIX_EPOCH)?;
                let session = StreamingSession::with_dictionary(
                    decoder,
                    settings.format,
                    settings.cleanup,
                    dictionary,
                );
                processing.cleanup_version = session.cleanup_version().into();
                self.active = Some(Active {
                    session_key: format!("{}-{}", std::process::id(), started.as_nanos()),
                    created_at: started.as_millis() as i64,
                    capture: Some(capture),
                    resampler,
                    session,
                    emitted: Instant::now(),
                    clock: RecordingClock::new(Instant::now()),
                    language: settings.language.code().into(),
                    processing,
                });
            }
            Command::Stop => return self.stop(),
            Command::Pause => {
                if let Err(error) = self.pause() {
                    self.fail(error);
                }
            }
            Command::Cancel => self.cancel(),
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
    fn cancel(&mut self) {
        let had_active = self.active.take().is_some();
        let _ = self.app.global_shortcut().unregister("Control+Space");
        self.update(|s| apply_cancellation(s, had_active));
    }
    fn take_cancellation(&mut self) -> bool {
        if self.cancel_receiver.try_recv().is_ok() {
            self.cancel();
            true
        } else {
            false
        }
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
    fn pause(&mut self) -> Result<()> {
        let Some(active) = self.active.as_mut() else {
            return Ok(());
        };
        if active.capture.is_some() {
            active.clock.pause(Instant::now());
            // Drop the CoreAudio stream before draining its finite remaining queue.
            active.drain(&self.cancel_receiver)?;
            active.session.flush_utterance()?;
            let transcript = active.session.snapshot();
            let elapsed = active.clock.elapsed(Instant::now()).as_secs();
            self.update(|s| {
                s.phase = "paused".into();
                s.level = 0.0;
                s.elapsed_seconds = elapsed;
                s.transcript = transcript;
            });
        } else {
            let capture = Capture::start()?;
            active.resampler = StreamingResampler::new(capture.sample_rate)?;
            let device = capture.device_name.clone();
            active.capture = Some(capture);
            active.clock.resume(Instant::now());
            self.update(|s| {
                s.phase = "recording".into();
                s.device = device;
            });
        }
        Ok(())
    }
    fn stop(&mut self) -> Result<()> {
        let Some(mut active) = self.active.take() else {
            return Ok(());
        };
        // Choose the user's current field at stop, not a stale field from start.
        let target = platform::destination_at_stop();
        active.clock.pause(Instant::now());
        self.update(|s| {
            s.phase = "finalizing".into();
            s.level = 0.0;
            s.elapsed_seconds = active.clock.elapsed(Instant::now()).as_secs();
        });
        let finalized = (|| -> Result<Transcript> {
            active.drain(&self.cancel_receiver)?;
            active.session.finish()
        })();
        if self.take_cancellation() {
            return Ok(());
        }
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
        let final_text = transcript.text();
        // Keep the finalized text available even if delivery fails.
        self.update(|s| {
            s.transcript = transcript;
            s.final_text = final_text.clone();
        });
        let delivery = (|| -> Result<String> {
            Ok(if final_text.is_empty() {
                "Nessun parlato rilevato".into()
            } else {
                let target = target.ok_or_else(|| anyhow!(
                "Nessun campo esterno accessibile. Consenti Accessibilità a VoiceFlow e posiziona il cursore nell’app di destinazione. Il testo è recuperabile con Copia testo."
            ))?;
                platform::paste_text(&target, &final_text, || !self.cancel_receiver.is_empty())?;
                "Incolla inviato al campo attivo".into()
            })
        })();
        // A cancellation during finalization or delivery must not enter history.
        if self.take_cancellation() {
            return Ok(());
        }
        let history_error = self.history.record(&CompletedDictation {
            session_key: &active.session_key,
            created_at: active.created_at,
            text: &final_text,
            duration_seconds: active.clock.elapsed(Instant::now()).as_secs_f64(),
            language: &active.language,
            original_asr: active.session.original_utterances(),
            processing: Some(&active.processing),
        }).err().map(|error| format!("Trascrizione non salvata nello storico: {error:#}. Puoi recuperarla con Copia testo."));
        self.update(|s| s.history_error = history_error);
        let _ = self.app.emit("history-changed", ());
        let message = delivery?;
        let _ = self.app.global_shortcut().unregister("Control+Space");
        self.update(|s| {
            s.phase = "complete".into();
            s.message = Some(message);
        });
        Ok(())
    }
}

fn processing_snapshot(
    settings: &Settings,
    store: &crate::dictionary::DictionaryStore,
) -> Result<(
    ProcessingMetadata,
    voiceflow_core::dictionary::Dictionary,
    Option<String>,
)> {
    let mut processing = processing_metadata(settings)?;
    let (dictionary, warning) = match store.session_snapshot(settings.language.code()) {
        Ok((dictionary, groups, version)) => {
            processing.groups = groups;
            processing.dictionary_version = Some(version);
            (dictionary, None)
        }
        Err(error) => (
            voiceflow_core::dictionary::Dictionary::default(),
            Some(format!(
                "Dizionario non applicato a questa sessione: {error}"
            )),
        ),
    };
    Ok((processing, dictionary, warning))
}

pub(crate) fn processing_metadata(settings: &Settings) -> Result<ProcessingMetadata> {
    let manifest: serde_json::Value =
        serde_json::from_str(include_str!("../../../../models/nemotron-manifest.json"))?;
    Ok(ProcessingMetadata {
        model_id: voiceflow_core::streaming::MODEL_ID.into(),
        model_revision: manifest["revision"]
            .as_str()
            .context("Revisione modello mancante")?
            .into(),
        model_provenance: "download-manifest; runtime files not rehashed".into(),
        decoder_version: "parakeet-rs/0.3.8; preroll-v1".into(),
        processor_version: "voiceflow-text-v3".into(),
        format: match settings.format {
            voiceflow_core::formatting::FormatMode::Faithful => "faithful",
            voiceflow_core::formatting::FormatMode::SpokenCommands => "spoken-commands",
        }
        .into(),
        cleanup: settings.cleanup,
        groups: Vec::new(),
        dictionary_version: None,
        cleanup_version: voiceflow_core::cleanup::BUILTIN_VERSION.into(),
    })
}
impl Active {
    fn drain(&mut self, cancellation: &Receiver<()>) -> Result<()> {
        if let Some(capture) = self.capture.take() {
            drain_audio(
                capture.finish()?,
                &mut self.resampler,
                &mut self.session,
                cancellation,
            )?;
        }
        Ok(())
    }
}

/// Capture must be stopped before draining so no producer can extend the queue.
fn drain_audio<D: StreamingDecoder>(
    frames: Receiver<Vec<f32>>,
    resampler: &mut StreamingResampler,
    session: &mut StreamingSession<D>,
    cancellation: &Receiver<()>,
) -> Result<()> {
    for frame in frames.try_iter() {
        ensure!(cancellation.is_empty(), "Dettatura annullata");
        session.push(&resampler.push(&frame)?)?;
    }
    ensure!(cancellation.is_empty(), "Dettatura annullata");
    session.push(&resampler.finish()?)?;
    Ok(())
}
fn apply_cancellation(snapshot: &mut Snapshot, had_active: bool) {
    if had_active
        || matches!(
            snapshot.phase.as_str(),
            "recording" | "paused" | "finalizing" | "error"
        )
    {
        snapshot.phase = "idle".into();
        snapshot.transcript = Transcript::default();
        snapshot.final_text.clear();
        snapshot.level = 0.0;
        snapshot.elapsed_seconds = 0;
        snapshot.message = Some("Dettatura annullata".into());
    }
}
/// Wall clock excludes paused intervals and includes capture time awaiting decode.
struct RecordingClock {
    accumulated: Duration,
    since: Option<Instant>,
}
impl RecordingClock {
    fn new(now: Instant) -> Self {
        Self {
            accumulated: Duration::ZERO,
            since: Some(now),
        }
    }
    fn elapsed(&self, now: Instant) -> Duration {
        self.accumulated
            + self
                .since
                .map_or(Duration::ZERO, |start| now.duration_since(start))
    }
    fn pause(&mut self, now: Instant) {
        self.accumulated = self.elapsed(now);
        self.since = None;
    }
    fn resume(&mut self, now: Instant) {
        if self.since.is_none() {
            self.since = Some(now);
        }
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::AtomicUsize;
    use voiceflow_core::formatting::FormatMode;

    #[test]
    fn dictionary_session_metadata_records_actual_snapshot_and_failure_is_visible() {
        let directory = std::env::temp_dir().join(format!(
            "voiceflow-session-dictionary-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let path = directory.join("dictionary.sqlite3");
        let store = crate::dictionary::DictionaryStore::new(path.clone());
        store
            .save_group(
                serde_json::from_value(serde_json::json!({"id":null,"name":"Project"})).unwrap(),
            )
            .unwrap();
        store.select_groups(vec![1]).unwrap();
        store.save_term(serde_json::from_value(serde_json::json!({"id":null,"text":"VoiceFlow","groupIds":[1],"variants":["voice flow"],"language":"it"})).unwrap()).unwrap();
        let settings = Settings {
            language: voiceflow_core::streaming::Language::Italian,
            ..Settings::default()
        };
        let (metadata, dictionary, warning) = processing_snapshot(&settings, &store).unwrap();
        assert_eq!(metadata.groups, ["1:Project"]);
        assert_eq!(
            metadata.dictionary_version.as_deref(),
            Some("dictionary-v2:4")
        );
        assert!(!dictionary.is_empty());
        assert!(warning.is_none());
        std::fs::write(&path, "broken").unwrap();
        let (metadata, dictionary, warning) = processing_snapshot(&settings, &store).unwrap();
        assert!(dictionary.is_empty());
        assert!(metadata.groups.is_empty());
        assert!(metadata.dictionary_version.is_none());
        assert!(
            warning
                .unwrap()
                .starts_with("Dizionario non applicato a questa sessione:")
        );
        std::fs::remove_dir_all(directory).unwrap();
    }

    #[derive(Default)]
    struct DelayedDecoder {
        text: String,
        utterance: usize,
        voiced_samples: Arc<AtomicUsize>,
    }
    impl StreamingDecoder for DelayedDecoder {
        fn chunk_samples(&self) -> usize {
            8_960
        }
        fn feed(&mut self, samples: &[f32]) -> Result<()> {
            let voiced = samples.iter().filter(|&&sample| sample != 0.0).count();
            self.voiced_samples.fetch_add(voiced, Ordering::Relaxed);
            self.text = if self.utterance == 0 {
                "testo precedente"
            } else if voiced > 0 {
                "coda"
            } else {
                // The last token is only available after decoder lookahead.
                "coda completa"
            }
            .into();
            Ok(())
        }
        fn text(&self) -> String {
            self.text.clone()
        }
        fn reset(&mut self) {
            self.utterance += 1;
            self.text.clear();
        }
    }

    #[test]
    fn decoder_markers_stay_filtered_in_preview_final_and_new_history_only() {
        struct SnapshotDecoder(Arc<Mutex<String>>);
        impl StreamingDecoder for SnapshotDecoder {
            fn chunk_samples(&self) -> usize {
                8_960
            }
            fn feed(&mut self, _: &[f32]) -> Result<()> {
                Ok(())
            }
            fn text(&self) -> String {
                self.0.lock().unwrap().clone()
            }
            fn reset(&mut self) {
                self.0.lock().unwrap().clear();
            }
        }

        for mode in [FormatMode::Faithful, FormatMode::SpokenCommands] {
            for cleanup in [false, true] {
                let raw = Arc::new(Mutex::new(String::new()));
                let mut session = StreamingSession::with_processing(
                    SnapshotDecoder(Arc::clone(&raw)),
                    mode,
                    cleanup,
                );
                for (snapshot, expected) in [
                    ("<un", ""),
                    ("<unk> Ciao<noise>, mondo <un", "Ciao, mondo"),
                    ("<unk> Ciao<noise>, Davide <unk>", "Ciao, Davide"),
                ] {
                    *raw.lock().unwrap() = snapshot.into();
                    session.push(&[0.25; 8_960]).unwrap();
                    let preview = session.snapshot();
                    assert_eq!(preview.text(), expected);
                    assert_eq!(
                        format!("{}{}", preview.confirmed, preview.partial).trim(),
                        expected
                    );
                }
                session.flush_utterance().unwrap();
                *raw.lock().unwrap() =
                    "molto molto importante <literal content> <incompleto".into();
                session.push(&[0.25; 8_960]).unwrap();
                let final_text = session.finish().unwrap().text();
                assert_eq!(
                    final_text,
                    "Ciao, Davide molto molto importante <incompleto"
                );
                assert_eq!(session.finish().unwrap().text(), final_text);

                let path = std::env::temp_dir().join(format!(
                    "voiceflow-markers-{}-{}.sqlite3",
                    std::process::id(),
                    SystemTime::now()
                        .duration_since(UNIX_EPOCH)
                        .unwrap()
                        .as_nanos()
                ));
                let store = HistoryStore::new(path.clone());
                for (session_key, text) in [
                    ("legacy", "vecchio <unk> testo"),
                    ("new", final_text.as_str()),
                ] {
                    store
                        .record(&CompletedDictation {
                            session_key,
                            created_at: 1,
                            text,
                            duration_seconds: 1.0,
                            language: "it",
                            original_asr: if session_key == "new" {
                                session.original_utterances()
                            } else {
                                None
                            },
                            processing: None,
                        })
                        .unwrap();
                }
                // History copy reads this exact stored value; it must not clean old rows.
                let reopened = HistoryStore::new(path.clone());
                assert_eq!(reopened.text(1).unwrap(), "vecchio <unk> testo");
                assert_eq!(reopened.text(2).unwrap(), final_text);
                assert_eq!(
                    reopened.detail(2).unwrap().original_asr.as_deref(),
                    session.original_utterances()
                );
                std::fs::remove_file(path).unwrap();
            }
        }
    }

    #[test]
    fn stop_drains_backlog_and_delayed_tokens_into_one_history_entry() {
        for rate in [16_000, 44_100, 48_000] {
            let decoder = DelayedDecoder::default();
            let voiced_samples = Arc::clone(&decoder.voiced_samples);
            let mut session = StreamingSession::new(decoder);
            session.push(&[0.25; 8_960]).unwrap();
            session.flush_utterance().unwrap();
            assert_eq!(session.snapshot().confirmed, "testo precedente");

            let (sender, frames) = crate::capture::capture_frames();
            for _ in 0..512 {
                sender.try_send(vec![0.25; 317]).unwrap();
            }
            sender.try_send(vec![0.25; 11]).unwrap();
            drop(sender); // Stop capture while the consumer still has a backlog.
            let observer = frames.clone();
            let (_cancel_sender, cancellation) = bounded(1);
            let mut resampler = StreamingResampler::new(rate).unwrap();
            drain_audio(frames, &mut resampler, &mut session, &cancellation).unwrap();
            assert!(observer.is_empty());
            let transcript = session.finish().unwrap();
            assert!(transcript.finished);
            assert!(transcript.partial.is_empty());
            assert_eq!(transcript.text(), "testo precedente coda completa");
            let expected_samples = 8_960 + (512 * 317 + 11) * 16_000 / rate;
            assert!((transcript.audio_seconds * 16_000.0 - expected_samples as f64).abs() < 1e-6);
            if rate == 16_000 {
                assert_eq!(voiced_samples.load(Ordering::Relaxed), expected_samples);
            }
            let chunks = transcript.chunks;
            assert_eq!(session.finish().unwrap().chunks, chunks);

            let path = std::env::temp_dir().join(format!(
                "voiceflow-stop-{}-{}-{rate}.sqlite3",
                std::process::id(),
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .unwrap()
                    .as_nanos()
            ));
            let store = HistoryStore::new(path.clone());
            let text = transcript.text();
            let entry = CompletedDictation {
                session_key: "stopped-session",
                created_at: 1,
                text: &text,
                duration_seconds: transcript.audio_seconds,
                language: "it-IT",
                original_asr: session.original_utterances(),
                processing: None,
            };
            store.record(&entry).unwrap();
            store.record(&entry).unwrap();
            assert_eq!(store.text(1).unwrap(), "testo precedente coda completa");
            assert!(store.text(2).is_err(), "Stop duplicated the stored session");
            assert_eq!(
                store.detail(1).unwrap().original_asr.unwrap(),
                ["testo precedente", "coda completa"]
            );
            std::fs::remove_file(path).unwrap();
        }
    }

    #[test]
    fn cancellation_during_drain_does_not_finalize_the_backlog() {
        let (sender, frames) = crate::capture::capture_frames();
        sender.try_send(vec![0.25; 8_960]).unwrap();
        drop(sender);
        let (cancel_sender, cancellation) = bounded(1);
        cancel_sender.send(()).unwrap();
        let mut session = StreamingSession::new(DelayedDecoder::default());
        let mut resampler = StreamingResampler::new(16_000).unwrap();
        assert!(drain_audio(frames, &mut resampler, &mut session, &cancellation).is_err());
        assert_eq!(session.snapshot().audio_seconds, 0.0);
        assert!(session.snapshot().text().is_empty());
        assert!(!session.snapshot().finished);
    }

    fn failed_snapshot() -> Snapshot {
        Snapshot {
            revision: 1,
            phase: "error".into(),
            model_ready: true,
            model_present: true,
            transcript: Transcript {
                partial: "testo da scartare".into(),
                ..Transcript::default()
            },
            final_text: "testo da scartare".into(),
            message: Some(
                "Acquisizione interrotta: audio incompleto. Il testo parziale è recuperabile."
                    .into(),
            ),
            history_error: None,
            level: 0.0,
            elapsed_seconds: 5,
            device: "test".into(),
            settings: Settings::default(),
        }
    }
    #[test]
    fn cancellation_after_capture_failure_must_clear_error_instead_of_being_ignored() {
        let mut snapshot = failed_snapshot();
        apply_cancellation(&mut snapshot, false);
        assert_eq!(
            snapshot.phase, "idle",
            "Cancellation left the capture error visible"
        );
        assert!(snapshot.final_text.is_empty());
        assert!(snapshot.transcript.text().is_empty());
        assert_eq!(snapshot.message.as_deref(), Some("Dettatura annullata"));
    }
    #[test]
    fn cancellation_bypasses_a_full_command_queue_and_coalesces_repeated_key_presses() {
        let (sender, receiver) = bounded(1);
        sender.send(Command::Stop).unwrap();
        let (cancel_sender, cancellation) = bounded(1);
        let service = Service {
            sender,
            cancel_sender,
            snapshot: Arc::new(Mutex::new(failed_snapshot())),
        };
        for _ in 0..10 {
            service.send(Command::Cancel).unwrap();
        }
        assert_eq!(cancellation.len(), 1);
        assert!(matches!(receiver.recv().unwrap(), Command::Stop));
        cancellation.recv().unwrap();
        assert!(cancellation.is_empty());
    }
    #[test]
    fn cancellation_clears_finalization_and_is_idempotent_but_cannot_undo_delivery() {
        let mut snapshot = failed_snapshot();
        snapshot.phase = "finalizing".into();
        apply_cancellation(&mut snapshot, false);
        apply_cancellation(&mut snapshot, false);
        assert_eq!(snapshot.phase, "idle");
        assert_eq!(snapshot.elapsed_seconds, 0);
        assert!(snapshot.final_text.is_empty());
        snapshot.phase = "complete".into();
        snapshot.final_text = "already delivered".into();
        apply_cancellation(&mut snapshot, false);
        assert_eq!(snapshot.final_text, "already delivered");
        assert_eq!(snapshot.phase, "complete");
    }
    #[test]
    fn elapsed_clock_freezes_during_pause_and_resumes_without_a_duration_cap() {
        let start = Instant::now();
        let mut clock = RecordingClock::new(start);
        clock.pause(start + Duration::from_secs(125));
        assert_eq!(
            clock.elapsed(start + Duration::from_secs(400)).as_secs(),
            125
        );
        clock.pause(start + Duration::from_secs(450));
        clock.resume(start + Duration::from_secs(500));
        clock.resume(start + Duration::from_secs(501));
        assert_eq!(
            clock.elapsed(start + Duration::from_secs(600)).as_secs(),
            225
        );
    }
}
