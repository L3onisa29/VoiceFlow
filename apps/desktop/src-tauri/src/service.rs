use crate::{
    capture::{Capture, CaptureLevel, CaptureStop},
    history::{
        ChangeStep, CompletedDictation, CorrectionCategory, HistoryStore, ProcessingMetadata,
        Timing,
    },
    platform,
    settings::Settings,
};
use anyhow::{Context, Result, anyhow, ensure};
use crossbeam_channel::{Receiver, Sender, bounded, select_biased, tick};
use serde::{Deserialize, Serialize};
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
        Language, MODEL_FILES, NvidiaDecoder, StreamingDecoder, StreamingSession, Transcript,
        load_model,
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
    /// Sentence of the voice profile being recorded. Dictation waits while it is set.
    pub profile_recording: Option<String>,
    /// Increases at every dictation start, so a live correction never reaches another session.
    pub session: u64,
    /// The microphone failed but the session was kept: it can resume or be saved.
    pub recoverable: bool,
}

/// A sentence corrected in the HUD. `text` is the edited confirmed prefix after this
/// step and the earlier ones.
#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct LiveStep {
    pub segment: i64,
    pub text: String,
    pub category: Option<CorrectionCategory>,
}
/// The HUD's view of the confirmed text (`base`) and the user's version of it.
#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct LiveEdit {
    pub session: u64,
    pub base: String,
    pub text: String,
    pub steps: Vec<LiveStep>,
}

/// What happens to the finished text.
#[derive(Clone, Copy, Debug, PartialEq)]
enum Delivery {
    /// Copy, then paste into the field that was active.
    Paste,
    /// Copy only: the user chose the destination themselves.
    CopyOnly,
    /// Neither: the session is closed from an error and kept in history.
    Keep,
}

/// Sent between text changes. `revision` is the state it belongs to.
#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct LevelUpdate {
    revision: u64,
    level: f32,
}
/// What the HUD shows every few frames, kept outside the worker so a decode in
/// progress cannot freeze the counter or the bars.
#[derive(Default)]
struct Meter {
    level: Arc<CaptureLevel>,
    // Written by the worker before the phase turns to recording.
    clock: Mutex<Option<RecordingClock>>,
}
const METER_STEP: Duration = Duration::from_millis(80);
enum MeterStep {
    Level(LevelUpdate),
    /// The counter advanced: a whole state, as for any other change.
    State(Box<Snapshot>),
}
/// One reading of the microphone for the interface. Nothing outside a recording.
fn meter_step(snapshot: &Mutex<Snapshot>, meter: &Meter, now: Instant) -> Option<MeterStep> {
    let mut s = snapshot.lock().unwrap();
    if s.phase != "recording" {
        return None;
    }
    let level = meter.level.take();
    s.level = level;
    let elapsed = meter
        .clock
        .lock()
        .unwrap()
        .map_or(s.elapsed_seconds, |clock| clock.elapsed(now).as_secs());
    if elapsed == s.elapsed_seconds {
        return Some(MeterStep::Level(LevelUpdate {
            revision: s.revision,
            level,
        }));
    }
    s.elapsed_seconds = elapsed;
    s.revision += 1;
    Some(MeterStep::State(Box::new(s.clone())))
}
/// Runs beside the worker until it ends. Inference can occupy the worker for longer
/// than a chunk of audio; the counter and the bars follow the microphone regardless.
fn run_meter(
    app: AppHandle,
    snapshot: Arc<Mutex<Snapshot>>,
    meter: Arc<Meter>,
    stopped: Receiver<()>,
) {
    while stopped
        .recv_timeout(METER_STEP)
        .is_err_and(|error| error.is_timeout())
    {
        match meter_step(&snapshot, &meter, Instant::now()) {
            Some(MeterStep::Level(update)) => {
                crate::hud::level(&app, update.revision, update.level);
                let _ = app.emit("dictation-level", update);
            }
            Some(MeterStep::State(state)) => {
                crate::hud::update(&app, (*state).clone());
                crate::hud::level(&app, state.revision, state.level);
                let _ = app.emit("dictation-state", *state);
            }
            None => {}
        }
    }
}
/// When the user asked for a start or a stop, taken at submission so queueing and
/// worker work are part of the measured latency.
#[derive(Default)]
struct Marks {
    start: Mutex<Option<Instant>>,
    stop: Mutex<Option<Instant>>,
}
/// A decoder primed while the worker is idle, so the next dictation does not run the
/// silence pre-roll between the key press and the microphone. Language bound.
struct Standby<D> {
    ready: Option<(Language, D)>,
    // A failed preparation is not retried on every idle tick.
    blocked: bool,
}
impl<D> Default for Standby<D> {
    fn default() -> Self {
        Self {
            ready: None,
            blocked: false,
        }
    }
}
impl<D> Standby<D> {
    fn wanted(&self, language: Language) -> bool {
        !self.blocked
            && self
                .ready
                .as_ref()
                .is_none_or(|(ready, _)| *ready != language)
    }
    fn store(&mut self, language: Language, decoder: D) {
        self.ready = Some((language, decoder));
    }
    /// A decoder primed for another language is discarded, never used.
    fn take(&mut self, language: Language) -> Option<D> {
        self.blocked = false;
        self.ready
            .take()
            .filter(|(ready, _)| *ready == language)
            .map(|(_, decoder)| decoder)
    }
    fn invalidate(&mut self) {
        self.ready = None;
        self.blocked = false;
    }
}
/// Guided reading for the voice profile, on the dictation worker so microphone,
/// resampling and model are the same and the two can never overlap.
pub enum ProfileCommand {
    Start(String),
    Stop,
    Cancel,
}
/// Progress and outcome of a guided reading, for its page only.
#[derive(Clone, Serialize)]
#[serde(tag = "kind", rename_all = "camelCase")]
enum ProfileEvent {
    Progress {
        id: String,
        seconds: f64,
        level: f32,
    },
    Saved {
        id: String,
    },
    Cancelled {
        id: String,
    },
    Error {
        id: String,
        message: String,
    },
}
struct ProfileRecording {
    id: String,
    capture: Capture,
    resampler: StreamingResampler,
    // The only audio VoiceFlow ever keeps: this sentence, at 16 kHz, until it is saved.
    samples: Vec<f32>,
    emitted: Instant,
    _analysis_permit: Option<crate::scheduler::CapturePermit>,
}
pub enum Command {
    Profile(ProfileCommand),
    Load,
    Toggle,
    Stop,
    /// Stop and copy without pasting.
    StopCopy,
    /// Close a recoverable error, saving what was transcribed.
    Dismiss,
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
    capture_stop: Arc<CaptureStop>,
    marks: Arc<Marks>,
    live: Arc<Mutex<Option<LiveEdit>>>,
}
impl Service {
    pub fn new(
        app: AppHandle,
        model_path: PathBuf,
        settings_path: PathBuf,
        history: HistoryStore,
        profile_store: crate::voice_profile::ProfileStore,
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
            profile_recording: None,
            session: 0,
            recoverable: false,
        }));
        let (sender, receiver) = bounded(16);
        let (cancel_sender, cancel_receiver) = bounded(1);
        let capture_stop = Arc::new(CaptureStop::default());
        let marks = Arc::new(Marks::default());
        let live = Arc::new(Mutex::new(None));
        let meter = Arc::new(Meter::default());
        // Dropped with the worker, which ends the meter thread.
        let (meter_stop, meter_stopped) = bounded(0);
        {
            let (app, snapshot, meter) = (app.clone(), Arc::clone(&snapshot), Arc::clone(&meter));
            std::thread::Builder::new()
                .name("voiceflow-meter".into())
                .spawn(move || run_meter(app, snapshot, meter, meter_stopped))?;
        }
        let service = Self {
            sender,
            cancel_sender,
            snapshot: Arc::clone(&snapshot),
            capture_stop: Arc::clone(&capture_stop),
            marks: Arc::clone(&marks),
            live: Arc::clone(&live),
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
                    capture_stop,
                    marks,
                    live,
                    meter,
                    _meter_stop: meter_stop,
                    standby: Standby::default(),
                    profile: None,
                    profile_store,
                };
                worker.run();
            })?;
        Ok(service)
    }
    pub fn send(&self, command: Command) -> Result<()> {
        let requested_at = Instant::now();
        // Cancellation is not ordinary work: a full command queue must never
        // delay or reject it. Multiple requests cancel the same pending session.
        if matches!(command, Command::Cancel) {
            return match self.cancel_sender.try_send(()) {
                Ok(()) | Err(crossbeam_channel::TrySendError::Full(())) => Ok(()),
                Err(_) => Err(anyhow!("VoiceFlow non è disponibile")),
            };
        }
        // Reserve transitions at submission, before the worker can block in ONNX,
        // SQLite or finalization. Repeated shortcuts must not be replayed later.
        let mut snapshot = self.snapshot.lock().unwrap();
        if let Command::Profile(request) = &command {
            let reserve = match request {
                ProfileCommand::Start(id) => {
                    ensure!(
                        snapshot.profile_recording.is_none(),
                        "Una frase è già in registrazione."
                    );
                    ensure!(
                        matches!(snapshot.phase.as_str(), "idle" | "complete" | "error"),
                        "Termina prima la dettatura in corso."
                    );
                    ensure!(
                        snapshot.model_ready,
                        "Il modello non è pronto. Caricalo prima di registrare."
                    );
                    Some(id.clone())
                }
                ProfileCommand::Stop | ProfileCommand::Cancel => {
                    ensure!(
                        snapshot.profile_recording.is_some(),
                        "Nessuna registrazione in corso."
                    );
                    None
                }
            };
            self.sender
                .try_send(command)
                .map_err(|_| anyhow!("VoiceFlow è occupato, riprova tra un istante"))?;
            if reserve.is_some() {
                snapshot.profile_recording = reserve;
                snapshot.revision += 1;
            }
            return Ok(());
        }
        // The microphone is taken: nothing of dictation starts, loads or reconfigures.
        ensure!(
            snapshot.profile_recording.is_none() || matches!(command, Command::Quit),
            "Termina prima la registrazione del profilo vocale."
        );
        if matches!(
            snapshot.phase.as_str(),
            "loading" | "stopping" | "finalizing"
        ) && matches!(
            command,
            Command::Load
                | Command::Toggle
                | Command::Stop
                | Command::StopCopy
                | Command::Dismiss
                | Command::Pause
        ) {
            return Ok(());
        }
        // Only a kept session can be dismissed; anything else has nothing to save.
        if matches!(command, Command::Dismiss) && !snapshot.recoverable {
            return Ok(());
        }
        let pending = match command {
            Command::Load => {
                ensure!(
                    !matches!(snapshot.phase.as_str(), "recording" | "paused"),
                    "Termina prima la registrazione"
                );
                if snapshot.model_ready {
                    return Ok(());
                }
                Some((
                    "loading",
                    "Caricamento del modello. Attendi prima di dettare.",
                ))
            }
            Command::Toggle
                if !matches!(snapshot.phase.as_str(), "recording" | "paused")
                    && !snapshot.recoverable =>
            {
                ensure!(
                    snapshot.model_ready,
                    "Il modello non è pronto. Caricalo prima di dettare."
                );
                Some((
                    "loading",
                    "Preparazione della dettatura. Attendi prima di parlare.",
                ))
            }
            Command::Toggle | Command::Stop | Command::StopCopy
                if snapshot.phase == "recording" =>
            {
                Some((
                    "stopping",
                    "Chiusura registrazione… Acquisisco le ultime parole.",
                ))
            }
            Command::Toggle | Command::Stop | Command::StopCopy if snapshot.phase == "paused" => {
                Some(("finalizing", "Elaborazione della trascrizione in corso"))
            }
            Command::Toggle | Command::Stop | Command::StopCopy | Command::Dismiss
                if snapshot.recoverable =>
            {
                Some(("finalizing", "Elaborazione della trascrizione in corso"))
            }
            _ => None,
        };
        self.sender
            .try_send(command)
            .map_err(|_| anyhow!("VoiceFlow è occupato, riprova tra un istante"))?;
        if let Some((phase, message)) = pending {
            if phase == "stopping" {
                self.capture_stop.request_stop(requested_at);
            }
            if phase == "loading" && !snapshot.model_ready {
                // Loading the model is not a dictation start.
            } else if phase == "loading" {
                *self.marks.start.lock().unwrap() = Some(requested_at);
            } else {
                *self.marks.stop.lock().unwrap() = Some(requested_at);
            }
            snapshot.phase = phase.into();
            snapshot.message = Some(message.into());
            snapshot.level = 0.0;
            snapshot.revision += 1;
        }
        Ok(())
    }
    pub fn snapshot(&self) -> Snapshot {
        self.snapshot.lock().unwrap().clone()
    }
    /// Keep the HUD's corrections for the session they were made in. The base must be
    /// what the decoder confirmed: it only grows, so a stale base is still a prefix.
    pub fn live_edit(&self, edit: LiveEdit) -> Result<()> {
        ensure!(
            edit.base.len() <= 1_000_000 && edit.text.len() <= 1_000_000 && edit.steps.len() <= 500,
            "Correzione troppo lunga."
        );
        let snapshot = self.snapshot.lock().unwrap();
        ensure!(
            edit.session == snapshot.session
                && (matches!(snapshot.phase.as_str(), "recording" | "paused" | "stopping")
                    || snapshot.recoverable),
            "La dettatura è già terminata: la correzione non è stata applicata."
        );
        ensure!(
            snapshot.transcript.confirmed.starts_with(&edit.base),
            "Il testo confermato non corrisponde. Riprova la correzione."
        );
        *self.live.lock().unwrap() = (edit.text != edit.base).then_some(edit);
        Ok(())
    }
    pub fn publish(&self, app: &AppHandle) {
        let snapshot = self.snapshot();
        crate::hud::update(app, snapshot.clone());
        let _ = app.emit("dictation-state", snapshot);
    }
}
struct Active {
    session_key: String,
    created_at: i64,
    capture: Option<Capture>,
    resampler: StreamingResampler,
    session: StreamingSession<NvidiaDecoder>,
    clock: RecordingClock,
    language: String,
    processing: ProcessingMetadata,
    // Chunk count of the last emitted text. The text cannot change in between.
    emitted_chunks: usize,
    key_to_microphone_ms: Option<u32>,
    prepared_decoder: bool,
    // The microphone failed during this session.
    interrupted: bool,
    // Drop this last, after capture/decoder teardown, including cancellation and errors.
    _analysis_permit: Option<crate::scheduler::CapturePermit>,
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
    capture_stop: Arc<CaptureStop>,
    marks: Arc<Marks>,
    live: Arc<Mutex<Option<LiveEdit>>>,
    meter: Arc<Meter>,
    _meter_stop: Sender<()>,
    standby: Standby<NvidiaDecoder>,
    profile: Option<ProfileRecording>,
    profile_store: crate::voice_profile::ProfileStore,
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
    /// Prime the next decoder in the worker's dead time: after the model loads, after a
    /// session ends and after a language change. Commands are served first.
    fn prepare_standby(&mut self) {
        let (phase, language) = {
            let s = self.snapshot.lock().unwrap();
            (s.phase.clone(), s.settings.language)
        };
        // "loading" means a start is already queued: it must not wait behind this.
        if self.active.is_some()
            || !matches!(phase.as_str(), "idle" | "complete" | "error")
            || !self.standby.wanted(language)
        {
            return;
        }
        let Some(model) = self.model.as_ref() else {
            return;
        };
        match NvidiaDecoder::new(model, language) {
            Ok(decoder) => self.standby.store(language, decoder),
            Err(_) => self.standby.blocked = true,
        }
    }
    fn run(&mut self) {
        let heartbeat = tick(Duration::from_millis(100));
        loop {
            let audio = self
                .active
                .as_ref()
                .and_then(|a| a.capture.as_ref().map(|c| c.frames.clone()))
                .or_else(|| self.profile.as_ref().map(|p| p.capture.frames.clone()))
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
                            self.profile = None;
                            self.app.exit(0);
                            break;
                        }
                        Err(_) => break,
                        Ok(Command::Profile(request)) => self.profile_command(request),
                        Ok(command) => if let Err(error) = self.command(command) {
                            if self.active.is_some() { self.update(|s| s.message = Some(error.to_string())); }
                            else { self.fail(error); }
                        },
                    }
                }
                recv(audio) -> frame => {
                    if let Ok(frame) = frame {
                        if self.profile.is_some() {
                            self.profile_audio(&frame);
                        } else if self.active.as_ref().is_some_and(|a| a.capture.as_ref().is_some_and(|c| c.failed.load(Ordering::Acquire))) {
                            self.interrupt(anyhow!("Microfono non disponibile: audio incompleto. Riprova o salva il testo."));
                        } else if let Err(error) = self.audio(&frame) { self.fail(error); }
                    }
                }
                recv(heartbeat) -> _ => {
                    if self.profile.as_ref().is_some_and(|p| p.capture.failed.load(Ordering::Acquire)) {
                        self.profile_end(Err(anyhow!("Acquisizione interrotta: microfono non disponibile. Rileggi la frase.")));
                    } else if self.profile.is_some() {
                        // Nothing to prepare while the microphone is in use.
                    } else if self.active.as_ref().is_some_and(|a| a.capture.as_ref().is_some_and(|c| c.failed.load(Ordering::Acquire))) {
                        self.interrupt(anyhow!("Microfono non disponibile o elaborazione troppo lenta. Riprova o salva il testo."));
                    } else if self.active.is_some() {
                        // The counter and the bars belong to the meter thread.
                    } else {
                        self.prepare_standby();
                    }
                }
            }
        }
        self.active = None;
        self.profile = None;
    }
    fn profile_command(&mut self, request: ProfileCommand) {
        match request {
            ProfileCommand::Start(id) => {
                if let Err(error) = self.profile_start(&id) {
                    self.profile = None;
                    self.profile_finish(ProfileEvent::Error {
                        id,
                        message: format!("{error:#}"),
                    });
                }
            }
            // A second press while the first is being saved has nothing left to stop.
            ProfileCommand::Stop if self.profile.is_none() => {}
            ProfileCommand::Stop => {
                let saved = self.profile_save();
                self.profile_end(saved);
            }
            ProfileCommand::Cancel => {
                if let Some(profile) = self.profile.take() {
                    self.profile_finish(ProfileEvent::Cancelled { id: profile.id });
                }
            }
        }
    }
    fn profile_start(&mut self, id: &str) -> Result<()> {
        ensure!(
            self.active.is_none(),
            "Termina prima la dettatura in corso."
        );
        ensure!(self.model.is_some(), "Carica prima il modello NVIDIA");
        ensure!(
            platform::permissions().microphone,
            "Consenti a VoiceFlow di usare il microfono nelle impostazioni Privacy di macOS"
        );
        self.profile_store.prompt(id)?;
        // The analyser gives way to the microphone, exactly as for a dictation.
        let permit = crate::scheduler::Coordination::new(
            self.settings_path
                .parent()
                .context("Directory impostazioni mancante")?,
        )
        .capture()
        .ok();
        // A profile sentence has its own progress events; the HUD meter stays out of it.
        let capture = Capture::start(Arc::clone(&self.capture_stop), Arc::default())?;
        let resampler = StreamingResampler::new(capture.sample_rate)?;
        self.profile = Some(ProfileRecording {
            id: id.to_owned(),
            capture,
            resampler,
            samples: Vec::new(),
            emitted: Instant::now(),
            _analysis_permit: permit,
        });
        self.profile_emit(ProfileEvent::Progress {
            id: id.to_owned(),
            seconds: 0.0,
            level: 0.0,
        });
        Ok(())
    }
    fn profile_audio(&mut self, frame: &[f32]) {
        let Some(profile) = self.profile.as_mut() else {
            return;
        };
        let level = (frame.iter().map(|v| v * v).sum::<f32>() / frame.len().max(1) as f32).sqrt();
        match profile.resampler.push(frame) {
            Ok(samples) => profile.samples.extend(samples),
            Err(error) => return self.profile_end(Err(error)),
        }
        if profile.samples.len() >= crate::voice_profile::MAX_SAMPLES {
            // Long enough for any sentence: keep what was read instead of growing forever.
            let saved = self.profile_save();
            return self.profile_end(saved);
        }
        if profile.emitted.elapsed() >= Duration::from_millis(80) {
            profile.emitted = Instant::now();
            let event = ProfileEvent::Progress {
                id: profile.id.clone(),
                seconds: profile.samples.len() as f64 / voiceflow_core::audio::SAMPLE_RATE as f64,
                level,
            };
            self.profile_emit(event);
        }
    }
    /// Stop the microphone, ask the current model what it hears and keep the pair.
    fn profile_save(&mut self) -> Result<String> {
        let mut profile = self
            .profile
            .take()
            .context("Nessuna registrazione in corso.")?;
        let device = profile.capture.device_name.clone();
        for frame in profile.capture.finish()?.try_iter() {
            profile.samples.extend(profile.resampler.push(&frame)?);
        }
        profile.samples.extend(profile.resampler.finish()?);
        profile.samples.truncate(crate::voice_profile::MAX_SAMPLES);
        ensure!(
            profile.samples.len() >= crate::voice_profile::MIN_SAMPLES,
            "Registrazione troppo breve. Leggi la frase per intero e riprova."
        );
        let settings = self.snapshot.lock().unwrap().settings.clone();
        let model = self
            .model
            .as_ref()
            .context("Carica prima il modello NVIDIA")?;
        let decoder = match self.standby.take(settings.language) {
            Some(decoder) => decoder,
            None => NvidiaDecoder::new(model, settings.language)?,
        };
        // Unprocessed, so the starting error of the model itself can be measured later.
        let (_, originals) = StreamingSession::new(decoder).transcribe(&profile.samples)?;
        let metadata = processing_metadata(&settings)?;
        self.profile_store.save(
            &profile.samples,
            crate::voice_profile::Recording {
                id: profile.id.clone(),
                text: String::new(),
                split: crate::voice_profile::Split::Train,
                duration: 0.0,
                target_lang: String::new(),
                hypothesis: originals.join(" ").trim().to_owned(),
                hypothesis_language: settings.language.code().into(),
                model: format!("{}@{}", metadata.model_id, metadata.model_revision),
                decoder: metadata.decoder_version,
                device,
                recorded_at: SystemTime::now().duration_since(UNIX_EPOCH)?.as_millis() as i64,
            },
        )?;
        Ok(profile.id)
    }
    fn profile_end(&mut self, saved: Result<String>) {
        let id = self
            .profile
            .take()
            .map(|profile| profile.id)
            .or_else(|| self.snapshot.lock().unwrap().profile_recording.clone())
            .unwrap_or_default();
        self.profile_finish(match saved {
            Ok(id) => ProfileEvent::Saved { id },
            Err(error) => ProfileEvent::Error {
                id,
                message: format!("{error:#}"),
            },
        });
    }
    /// Release the reservation made at submission, then tell the page.
    fn profile_finish(&mut self, event: ProfileEvent) {
        self.update(|s| s.profile_recording = None);
        self.profile_emit(event);
    }
    fn profile_emit(&self, event: ProfileEvent) {
        let _ = self.app.emit("voice-profile", event);
    }
    fn fail(&mut self, error: anyhow::Error) {
        if self.take_cancellation() {
            return;
        }
        // A recording error can win the race just before Ctrl+Space arrives.
        // Keep cancellation available until that recoverable session is dismissed.
        let phase = self.snapshot.lock().unwrap().phase.clone();
        if self.active.is_none()
            && !matches!(
                phase.as_str(),
                "recording" | "paused" | "stopping" | "finalizing"
            )
        {
            self.unregister_session_shortcuts();
        }
        if let Some(active) = self.active.take() {
            let transcript = active.session.snapshot();
            let text = transcript.text();
            self.save_interrupted(&active, &text);
            self.update(|s| {
                s.transcript = transcript;
                s.final_text = text;
            });
        }
        *self.live.lock().unwrap() = None;
        self.update(|s| {
            s.phase = "error".into();
            s.recoverable = false;
            s.level = 0.0;
            s.message = Some(error.to_string());
        });
    }
    /// The microphone stopped but the decoded text is intact: keep the session so it
    /// can resume on a new stream (Pause) or be saved without pasting (Dismiss).
    fn interrupt(&mut self, error: anyhow::Error) {
        if self.take_cancellation() {
            return;
        }
        let Some(active) = self.active.as_mut() else {
            return self.fail(error);
        };
        active.clock.pause(Instant::now());
        active.interrupted = true;
        // The failed stream cannot be trusted for its last frames; closing it restores
        // the speakers. Whatever was decoded already stays.
        let _ = active.drain(&self.cancel_receiver);
        if let Err(flush) = active.session.flush_utterance() {
            return self.fail(flush);
        }
        let transcript = active.session.snapshot();
        let elapsed = active.clock.elapsed(Instant::now()).as_secs();
        self.update(|s| {
            s.phase = "error".into();
            s.recoverable = true;
            s.level = 0.0;
            s.elapsed_seconds = elapsed;
            s.message = Some(error.to_string());
            s.final_text = transcript.text();
            s.transcript = transcript;
        });
    }
    /// A session that cannot be finished normally still reaches the recent list.
    fn save_interrupted(&mut self, active: &Active, text: &str) {
        let mut processing = active.processing.clone();
        processing.interrupted = Some(true);
        let saved = self.history.record(&CompletedDictation {
            session_key: &active.session_key,
            created_at: active.created_at,
            text,
            duration_seconds: active.clock.elapsed(Instant::now()).as_secs_f64(),
            language: &active.language,
            original_asr: None,
            processing: Some(&processing),
        });
        let history_error = saved.err().map(|error| {
            format!("Trascrizione non salvata nello storico: {error:#}. Puoi recuperarla con Copia testo.")
        });
        self.update(|s| s.history_error = history_error);
        let _ = self.app.emit("history-changed", ());
    }
    fn register_session_shortcuts(&self) -> Result<()> {
        let shortcuts = self.app.global_shortcut();
        if !shortcuts.is_registered("Control+Space") {
            shortcuts.on_shortcut("Control+Space", |app, _, event| {
                if event.state == ShortcutState::Pressed {
                    let _ = app.state::<Service>().send(Command::Cancel);
                }
            })?;
        }
        // Optional: another application may own it, dictation still works without it.
        // ⌃⌥T shows or hides the text above the HUD. Optional as well.
        if !shortcuts.is_registered("Control+Alt+KeyT") {
            let _ = shortcuts.on_shortcut("Control+Alt+KeyT", |app, _, event| {
                if event.state == ShortcutState::Pressed {
                    let _ = app.emit_to(crate::overlay::LIVE, "live-toggle", ());
                }
            });
        }
        if !shortcuts.is_registered("Shift+Alt+Space") {
            let _ = shortcuts.on_shortcut("Shift+Alt+Space", |app, _, event| {
                if event.state == ShortcutState::Pressed {
                    let _ = crate::send_from_controls(app, Command::StopCopy);
                }
            });
        }
        Ok(())
    }
    fn unregister_session_shortcuts(&self) {
        for shortcut in ["Control+Space", "Shift+Alt+Space", "Control+Alt+KeyT"] {
            let _ = self.app.global_shortcut().unregister(shortcut);
        }
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
                    let model = load_model(&self.model_path)?;
                    let language = self.snapshot.lock().unwrap().settings.language;
                    // Execute real inference while the loading spinner is visible.
                    // ONNX can lazily initialize kernels on the first decode. The primed
                    // decoder is kept for the first dictation.
                    let decoder = NvidiaDecoder::new(&model, language)?;
                    self.standby.store(language, decoder);
                    self.model = Some(model);
                }
                self.update(|s| {
                    s.phase = "idle".into();
                    s.model_ready = true;
                    s.model_present = true;
                    s.message = None;
                });
            }
            Command::Toggle if self.active.is_some() => return self.stop(Delivery::Paste),
            Command::Toggle => {
                // Cancellation may have overtaken this queued start command.
                if self.snapshot.lock().unwrap().phase != "loading" {
                    return Ok(());
                }
                ensure!(
                    platform::permissions().microphone,
                    "Consenti a VoiceFlow di usare il microfono nelle impostazioni Privacy di macOS"
                );
                ensure!(self.model.is_some(), "Carica prima il modello NVIDIA");
                if self.take_cancellation() {
                    return Ok(());
                }
                // The microphone opens first. Whatever is said from here waits in the
                // capture queue while the session is prepared, instead of being lost
                // behind the dictionary, the analyser and the focus lookup.
                let capture = Capture::start(
                    Arc::clone(&self.capture_stop),
                    Arc::clone(&self.meter.level),
                )?;
                let key_to_microphone_ms =
                    self.marks.start.lock().unwrap().take().map(|requested| {
                        requested.elapsed().as_millis().min(u128::from(u32::MAX)) as u32
                    });
                let clock = RecordingClock::new(Instant::now());
                *self.meter.clock.lock().unwrap() = Some(clock);
                *self.live.lock().unwrap() = None;
                // Recording is shown as soon as it is true, not when the decoder is ready.
                self.update(|s| {
                    s.phase = "recording".into();
                    s.session += 1;
                    s.recoverable = false;
                    s.transcript = Transcript::default();
                    s.final_text.clear();
                    s.message = None;
                    s.device = capture.device_name.clone();
                    s.elapsed_seconds = 0;
                    s.level = 0.0;
                });
                self.register_session_shortcuts()?;
                let settings = self.snapshot.lock().unwrap().settings.clone();
                let (mut processing, dictionary, cleanup_catalog, dictionary_warning) =
                    reviewed_processing_snapshot(
                        &settings,
                        &self.app.state::<crate::suggestions::SuggestionStore>(),
                    )?;
                // Independent from scheduler SQLite/launchctl. The intent is held across
                // pause and finalization; process exit releases stale capture state.
                let coordination = crate::scheduler::Coordination::new(
                    self.settings_path
                        .parent()
                        .context("Directory impostazioni mancante")?,
                );
                let (analysis_permit, scheduler_warning) = match coordination.capture() {
                    Ok(permit) => {
                        let warning = permit.degraded().then(|| "Analizzatore non arrestato entro 1,5 secondi. Dettatura disponibile; disattiva la pianificazione.".into());
                        (Some(permit), warning)
                    }
                    Err(error) => (
                        None,
                        Some(format!("Coordinamento analisi non disponibile: {error}")),
                    ),
                };
                if let Some(warning) = scheduler_warning.or(dictionary_warning) {
                    self.update(|s| s.message = Some(warning));
                }
                // Already primed in idle time when available; primed here otherwise, while
                // the audio captured meanwhile is kept for it.
                let standby = self.standby.take(settings.language);
                let prepared_decoder = standby.is_some();
                let decoder = match standby {
                    Some(decoder) => decoder,
                    None => NvidiaDecoder::new(
                        self.model
                            .as_ref()
                            .ok_or_else(|| anyhow!("Carica prima il modello NVIDIA"))?,
                        settings.language,
                    )?,
                };
                // Ask Chromium/Electron to expose its editor before the stop shortcut.
                let _ = platform::focused_target();
                if self.take_cancellation() {
                    return Ok(());
                }
                let resampler = StreamingResampler::new(capture.sample_rate)?;
                let started = SystemTime::now().duration_since(UNIX_EPOCH)?;
                let session = StreamingSession::with_rules(
                    decoder,
                    settings.format,
                    settings.cleanup,
                    dictionary,
                    cleanup_catalog,
                );
                processing.cleanup_version = session.cleanup_version().into();
                self.active = Some(Active {
                    _analysis_permit: analysis_permit,
                    session_key: format!("{}-{}", std::process::id(), started.as_nanos()),
                    created_at: started.as_millis() as i64,
                    capture: Some(capture),
                    resampler,
                    session,
                    clock,
                    language: settings.language.code().into(),
                    processing,
                    emitted_chunks: 0,
                    key_to_microphone_ms,
                    prepared_decoder,
                    interrupted: false,
                });
            }
            Command::Stop => return self.stop(Delivery::Paste),
            Command::StopCopy => return self.stop(Delivery::CopyOnly),
            Command::Dismiss => return self.stop(Delivery::Keep),
            Command::Pause => {
                if let Err(error) = self.pause() {
                    // A stream that cannot reopen leaves the session kept, as after a failure.
                    if self.active.as_ref().is_some_and(|a| a.capture.is_none()) {
                        self.interrupt(error);
                    } else {
                        self.fail(error);
                    }
                }
            }
            Command::Cancel => self.cancel(),
            Command::Settings(settings) => {
                ensure!(self.active.is_none(), "Termina prima la registrazione");
                settings.save(&self.settings_path)?;
                if settings.language != self.snapshot.lock().unwrap().settings.language {
                    // Primed for the previous language. The idle tick prepares the new one.
                    self.standby.invalidate();
                }
                self.update(|s| {
                    s.settings = settings;
                });
            }
            Command::Quit | Command::Profile(_) => {}
        }
        Ok(())
    }
    fn cancel(&mut self) {
        let had_active = self.active.take().is_some();
        *self.live.lock().unwrap() = None;
        self.unregister_session_shortcuts();
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
        active.session.push(&active.resampler.push(frame)?)?;
        // The text can change only when a chunk was decoded, and is shown at once.
        let chunks = active.session.chunks();
        if chunks != active.emitted_chunks {
            active.emitted_chunks = chunks;
            let transcript = active.session.snapshot();
            self.update(|s| s.transcript = transcript);
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
            let capture = Capture::start(
                Arc::clone(&self.capture_stop),
                Arc::clone(&self.meter.level),
            )?;
            active.resampler = StreamingResampler::new(capture.sample_rate)?;
            let device = capture.device_name.clone();
            active.capture = Some(capture);
            active.clock.resume(Instant::now());
            *self.meter.clock.lock().unwrap() = Some(active.clock);
            self.update(|s| {
                s.phase = "recording".into();
                s.recoverable = false;
                s.message = None;
                s.device = device;
            });
        }
        Ok(())
    }
    fn stop(&mut self, delivery: Delivery) -> Result<()> {
        let stop_requested = self.marks.stop.lock().unwrap().take();
        let Some(mut active) = self.active.take() else {
            return Ok(());
        };
        let capturing = active.capture.is_some();
        // Synchronize with the reserved transition before reading its deadline.
        self.update(|s| {
            if capturing {
                s.phase = "stopping".into();
            }
        });
        // Choose the destination before the tail rather than after an extra wait.
        // The callback cutoff keeps accessibility retries from extending the audio.
        let target = (delivery == Delivery::Paste)
            .then(|| platform::destination_at_stop(|| !self.cancel_receiver.is_empty()))
            .flatten();
        let destination_app = target
            .as_ref()
            .map(platform::FocusTarget::pid)
            .or_else(platform::frontmost_foreign_pid)
            .and_then(platform::app_name);
        if capturing && !self.capture_stop.wait_for_tail(&self.cancel_receiver) {
            drop(active);
            self.cancel();
            return Ok(());
        }
        let stopped_at = if capturing {
            self.capture_stop
                .deadline()
                .unwrap_or_else(Instant::now)
                .min(Instant::now())
        } else {
            Instant::now()
        };
        active.clock.pause(stopped_at);
        let frames = active.capture.take().map(Capture::finish).transpose();
        self.update(|s| {
            s.phase = "finalizing".into();
            s.message = Some("Elaborazione della trascrizione in corso".into());
            s.level = 0.0;
            s.elapsed_seconds = active.clock.elapsed(Instant::now()).as_secs();
        });
        let finalized = (|| -> Result<Transcript> {
            if let Some(frames) = frames? {
                drain_audio(
                    frames,
                    &mut active.resampler,
                    &mut active.session,
                    &self.cancel_receiver,
                )?;
            }
            active.session.finish()
        })();
        if self.take_cancellation() {
            return Ok(());
        }
        let transcript = match finalized {
            Ok(transcript) => transcript,
            Err(error) => {
                let transcript = active.session.snapshot();
                let text = transcript.text();
                self.save_interrupted(&active, &text);
                *self.live.lock().unwrap() = None;
                self.update(|s| {
                    s.transcript = transcript;
                    s.final_text = text;
                });
                return Err(error);
            }
        };
        let final_text = transcript.text();
        // The HUD's corrections apply to the confirmed prefix they were made on.
        let session = self.snapshot.lock().unwrap().session;
        let edit = self
            .live
            .lock()
            .unwrap()
            .take()
            .filter(|edit| edit.session == session);
        let finished = format!("{}{}", transcript.confirmed, transcript.partial);
        let corrected = edit
            .as_ref()
            .and_then(|edit| apply_live_edit(edit, &finished));
        let delivered = corrected
            .as_ref()
            .map_or_else(|| final_text.clone(), |(text, _)| text.clone());
        let mut timing = Timing {
            key_to_microphone_ms: active.key_to_microphone_ms,
            inference_ms: (transcript.inference_seconds * 1000.0).round() as u32,
            chunks: transcript.chunks as u32,
            silence_closures: active.session.silence_closures() as u32,
            prepared_decoder: active.prepared_decoder,
            ..Timing::default()
        };
        active.processing.recognition = active.session.recognition();
        active.processing.rule_hits = Some(active.session.rule_hits().clone());
        // Keep the finalized text available even if delivery fails.
        self.update(|s| {
            s.transcript = transcript;
            s.final_text = delivered.clone();
        });
        // Keystrokes must reach the destination, not a HUD panel the user typed into.
        crate::overlay::release_focus(&self.app);
        let delivery = match delivery {
            Delivery::Paste => deliver_transcript(
                &delivered,
                platform::copy_text,
                |text| {
                    let target = target
                        .ok_or_else(|| anyhow!("Nessun campo di destinazione disponibile"))?;
                    platform::paste_text(&target, text, || !self.cancel_receiver.is_empty())
                },
                || !self.cancel_receiver.is_empty(),
            ),
            Delivery::CopyOnly => copy_transcript(&delivered, platform::copy_text),
            Delivery::Keep => Ok(if delivered.is_empty() {
                "Nessun parlato rilevato"
            } else {
                "Trascrizione salvata nei recenti"
            }),
        };
        // A cancellation during finalization or delivery must not enter history.
        if self.take_cancellation() {
            return Ok(());
        }
        if !final_text.is_empty() {
            timing.stop_to_paste_ms = stop_requested
                .map(|requested| requested.elapsed().as_millis().min(u128::from(u32::MAX)) as u32);
            timing.pasted = matches!(delivery, Ok(PASTED));
        }
        timing.destination_app = destination_app;
        active.processing.timing = Some(timing);
        if active.interrupted {
            active.processing.interrupted = Some(true);
        }
        let history_error = self.history.record(&CompletedDictation {
            session_key: &active.session_key,
            created_at: active.created_at,
            text: &final_text,
            duration_seconds: active.clock.elapsed(Instant::now()).as_secs_f64(),
            language: &active.language,
            original_asr: active.session.original_utterances(),
            processing: Some(&active.processing),
        })
        .and_then(|()| match corrected {
            // The produced text stays immutable; the corrections become its revisions.
            Some((_, steps)) => self.history.record_live_changes(&active.session_key, steps),
            None => Ok(()),
        })
        .err().map(|error| format!("Trascrizione non salvata nello storico: {error:#}. Puoi recuperarla con Copia testo."));
        self.update(|s| s.history_error = history_error);
        let _ = self.app.emit("history-changed", ());
        let message = delivery?;
        self.unregister_session_shortcuts();
        self.update(|s| {
            s.phase = "complete".into();
            s.message = Some(message.into());
        });
        Ok(())
    }
}

const PASTED: &str = "Incolla inviato al campo attivo";
fn deliver_transcript(
    text: &str,
    copy: impl FnOnce(&str) -> Result<()>,
    paste: impl FnOnce(&str) -> Result<()>,
    cancelled: impl Fn() -> bool,
) -> Result<&'static str> {
    if text.is_empty() {
        return Ok("Nessun parlato rilevato");
    }
    ensure!(!cancelled(), "Dettatura annullata");
    // Preserve the result before any destination or keyboard checks can fail.
    copy(text).context(
        "Impossibile copiare la trascrizione negli appunti. Il testo resta disponibile in VoiceFlow.",
    )?;
    ensure!(!cancelled(), "Dettatura annullata");
    Ok(if paste(text).is_ok() {
        PASTED
    } else {
        "Trascrizione copiata negli appunti. Premi ⌘V per incollarla dove vuoi."
    })
}

fn copy_transcript(text: &str, copy: impl FnOnce(&str) -> Result<()>) -> Result<&'static str> {
    if text.is_empty() {
        return Ok("Nessun parlato rilevato");
    }
    copy(text).context(
        "Impossibile copiare la trascrizione negli appunti. Il testo resta disponibile in VoiceFlow.",
    )?;
    Ok("Trascrizione copiata negli appunti")
}

/// Apply the HUD's corrections of the confirmed prefix to the finished text. Confirmed
/// text only grows, so what followed the edited prefix is appended unchanged. Returns
/// the text to deliver and one history step per corrected sentence.
fn apply_live_edit(edit: &LiveEdit, finished: &str) -> Option<(String, Vec<ChangeStep>)> {
    let tail = finished.strip_prefix(edit.base.as_str())?;
    let complete = |text: &str| format!("{text}{tail}").trim().to_owned();
    let produced = finished.trim();
    let delivered = complete(&edit.text);
    if delivered.is_empty() || delivered == produced {
        return None;
    }
    let mut steps: Vec<ChangeStep> = Vec::new();
    let mut previous = produced.to_owned();
    for step in &edit.steps {
        let text = complete(&step.text);
        if text.is_empty() || text == previous {
            continue;
        }
        previous.clone_from(&text);
        steps.push(ChangeStep {
            segment: step.segment,
            text,
            category: step.category,
            restore: false,
        });
    }
    // The chain always ends on what was delivered, even if the steps lagged behind.
    if previous != delivered {
        steps.push(ChangeStep {
            segment: steps.last().map_or(0, |step| step.segment),
            text: delivered.clone(),
            category: None,
            restore: false,
        });
    }
    Some((delivered, steps))
}

fn reviewed_processing_snapshot(
    settings: &Settings,
    store: &crate::suggestions::SuggestionStore,
) -> Result<(
    ProcessingMetadata,
    voiceflow_core::dictionary::Dictionary,
    voiceflow_core::cleanup::CompiledCatalog,
    Option<String>,
)> {
    let mut processing = processing_metadata(settings)?;
    match store.session_snapshot(settings.language.code()) {
        Ok(snapshot) => {
            processing.groups = snapshot.groups;
            processing.dictionary_version = Some(snapshot.dictionary_version);
            processing.cleanup_version = snapshot.cleanup.version().into();
            Ok((processing, snapshot.dictionary, snapshot.cleanup, None))
        }
        Err(error) => Ok((
            processing,
            voiceflow_core::dictionary::Dictionary::default(),
            voiceflow_core::cleanup::CompiledCatalog::builtin(),
            Some(format!(
                "Regole personali non applicate a questa sessione: {error}"
            )),
        )),
    }
}

#[cfg(test)]
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
        processor_version: "voiceflow-text-v5".into(),
        format: match settings.format {
            voiceflow_core::formatting::FormatMode::Faithful => "faithful",
            voiceflow_core::formatting::FormatMode::SpokenCommands => "spoken-commands",
        }
        .into(),
        cleanup: settings.cleanup,
        groups: Vec::new(),
        dictionary_version: None,
        cleanup_version: voiceflow_core::cleanup::BUILTIN_VERSION.into(),
        recognition: None,
        rule_hits: None,
        timing: None,
        interrupted: None,
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
            "loading" | "recording" | "paused" | "stopping" | "finalizing" | "error"
        )
    {
        snapshot.phase = "idle".into();
        snapshot.recoverable = false;
        snapshot.transcript = Transcript::default();
        snapshot.final_text.clear();
        snapshot.level = 0.0;
        snapshot.elapsed_seconds = 0;
        snapshot.message = Some("Dettatura annullata".into());
    }
}
/// Wall clock excludes paused intervals and includes capture time awaiting decode.
#[derive(Clone, Copy)]
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
    use std::cell::RefCell;
    use std::sync::atomic::AtomicUsize;
    use voiceflow_core::formatting::FormatMode;

    #[test]
    fn delivery_copies_the_final_transcript_when_the_destination_is_unavailable() {
        for error in [
            "missing destination",
            "accessibility denied",
            "focus changed",
            "modifier timeout",
            "cannot create paste event",
        ] {
            let clipboard = RefCell::new(String::from("previous clipboard"));
            let text = "Trascrizione completa.\nSecondo paragrafo.";
            let result = deliver_transcript(
                text,
                |text| {
                    *clipboard.borrow_mut() = text.into();
                    Ok(())
                },
                |_| Err(anyhow!("{error}")),
                || false,
            );
            assert_eq!(clipboard.borrow().as_str(), text, "{error}");
            assert_eq!(
                result.unwrap(),
                "Trascrizione copiata negli appunti. Premi ⌘V per incollarla dove vuoi."
            );
        }
    }

    #[test]
    fn delivery_keeps_the_final_transcript_in_the_clipboard_after_successful_paste() {
        let clipboard = RefCell::new(String::from("previous clipboard"));
        let text = "Testo finale corretto";
        let result = deliver_transcript(
            text,
            |text| {
                *clipboard.borrow_mut() = text.into();
                Ok(())
            },
            |text| {
                assert_eq!(clipboard.borrow().as_str(), text);
                Ok(())
            },
            || false,
        );
        assert_eq!(result.unwrap(), "Incolla inviato al campo attivo");
        assert_eq!(clipboard.borrow().as_str(), text);
    }

    #[test]
    fn delivery_does_not_overwrite_the_clipboard_when_no_speech_was_detected() {
        assert_eq!(
            deliver_transcript(
                "",
                |_| panic!("No transcript to copy"),
                |_| panic!("No transcript to paste"),
                || false,
            )
            .unwrap(),
            "Nessun parlato rilevato"
        );
    }

    #[test]
    fn delivery_reports_clipboard_failure_without_pasting_previous_contents() {
        let result = deliver_transcript(
            "Trascrizione recuperabile",
            |_| Err(anyhow!("clipboard unavailable")),
            |_| panic!("Must not paste stale clipboard contents"),
            || false,
        );
        let error = result.unwrap_err();
        assert!(
            error
                .to_string()
                .contains("Il testo resta disponibile in VoiceFlow")
        );
        assert!(format!("{error:#}").contains("clipboard unavailable"));
    }

    #[test]
    fn delivery_does_not_copy_or_paste_a_cancelled_dictation() {
        let result = deliver_transcript(
            "Testo annullato",
            |_| panic!("Must not copy cancelled dictation"),
            |_| panic!("Must not paste cancelled dictation"),
            || true,
        );
        assert_eq!(result.unwrap_err().to_string(), "Dettatura annullata");
    }

    #[test]
    fn delivery_honors_cancellation_between_copy_and_paste() {
        let (sender, cancellation) = bounded(1);
        let clipboard = RefCell::new(String::from("previous clipboard"));
        let result = deliver_transcript(
            "Testo già copiato",
            |text| {
                *clipboard.borrow_mut() = text.into();
                sender.send(()).unwrap();
                Ok(())
            },
            |_| panic!("Must not paste after cancellation"),
            || !cancellation.is_empty(),
        );
        assert_eq!(result.unwrap_err().to_string(), "Dettatura annullata");
        assert_eq!(clipboard.borrow().as_str(), "Testo già copiato");
    }

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
    fn stop_tail_retains_the_last_word_but_never_later_audio_in_the_decoder_backlog() {
        for rate in [16_000, 44_100, 48_000] {
            let decoder = DelayedDecoder::default();
            let voiced_samples = Arc::clone(&decoder.voiced_samples);
            let mut session = StreamingSession::new(decoder);
            session.push(&[0.25; 8_960]).unwrap();
            session.flush_utterance().unwrap();
            let stop = CaptureStop::default();
            let requested = Instant::now();
            stop.request_stop(requested);
            let (sender, frames) = crate::capture::capture_frames();
            let mut captured = 0;
            // Capture keeps running while inference is busy, including a callback
            // crossing the deadline and later speech that must be excluded.
            for millis in (0..1_000).step_by(20) {
                let count = stop.retained_samples(
                    rate / 50,
                    rate,
                    requested + Duration::from_millis(millis),
                );
                if count > 0 {
                    sender.send(vec![0.25; count]).unwrap();
                    captured += count;
                }
            }
            assert_eq!(captured, rate * 350 / 1_000);
            drop(sender);
            let (_cancel_sender, cancellation) = bounded(1);
            let mut resampler = StreamingResampler::new(rate).unwrap();
            drain_audio(frames, &mut resampler, &mut session, &cancellation).unwrap();
            let transcript = session.finish().unwrap();
            assert_eq!(transcript.text(), "testo precedente coda completa");
            assert!((transcript.audio_seconds * 16_000.0 - (8_960 + 5_600) as f64).abs() < 1e-6);
            if rate == 16_000 {
                assert_eq!(voiced_samples.load(Ordering::Relaxed), 8_960 + 5_600);
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
            profile_recording: None,
            session: 1,
            recoverable: false,
        }
    }
    #[test]
    fn recording_shortcuts_during_model_loading_are_not_replayed_after_loading() {
        let (sender, receiver) = bounded(16);
        let (cancel_sender, _) = bounded(1);
        let mut snapshot = failed_snapshot();
        snapshot.phase = "loading".into();
        snapshot.model_ready = false;
        let service = Service {
            capture_stop: Arc::new(CaptureStop::default()),
            marks: Arc::new(Marks::default()),
            live: Arc::default(),
            sender,
            cancel_sender,
            snapshot: Arc::new(Mutex::new(snapshot)),
        };
        for _ in 0..4 {
            let _ = service.send(Command::Toggle);
        }
        assert!(
            receiver.is_empty(),
            "Loading queued shortcuts that will start/stop capture later"
        );
        assert_eq!(service.snapshot().phase, "loading");
    }
    #[test]
    fn start_reserves_loading_before_the_worker_runs_and_blocks_repeated_controls() {
        let (sender, receiver) = bounded(16);
        let (cancel_sender, _) = bounded(1);
        let mut snapshot = failed_snapshot();
        snapshot.phase = "idle".into();
        let service = Service {
            capture_stop: Arc::new(CaptureStop::default()),
            marks: Arc::new(Marks::default()),
            live: Arc::default(),
            sender,
            cancel_sender,
            snapshot: Arc::new(Mutex::new(snapshot)),
        };
        service.send(Command::Toggle).unwrap();
        assert_eq!(service.snapshot().phase, "loading");
        for command in [
            Command::Toggle,
            Command::Stop,
            Command::Pause,
            Command::Load,
        ] {
            service.send(command).unwrap();
        }
        assert_eq!(receiver.len(), 1);
        assert!(matches!(receiver.recv().unwrap(), Command::Toggle));
        let mut snapshot = service.snapshot.lock().unwrap();
        apply_cancellation(&mut snapshot, false);
        assert_eq!(snapshot.phase, "idle");
    }
    #[test]
    fn stop_reserves_the_tail_and_an_unloaded_model_cannot_start_capture() {
        let (sender, receiver) = bounded(16);
        let (cancel_sender, _) = bounded(1);
        let mut snapshot = failed_snapshot();
        snapshot.phase = "recording".into();
        let service = Service {
            capture_stop: Arc::new(CaptureStop::default()),
            marks: Arc::new(Marks::default()),
            live: Arc::default(),
            sender,
            cancel_sender,
            snapshot: Arc::new(Mutex::new(snapshot)),
        };
        let before = Instant::now();
        service.send(Command::Toggle).unwrap();
        let deadline = service.capture_stop.deadline().unwrap();
        assert!(deadline >= before + crate::capture::STOP_TAIL);
        for command in [
            Command::Toggle,
            Command::Stop,
            Command::Pause,
            Command::Load,
        ] {
            service.send(command).unwrap();
        }
        assert_eq!(service.capture_stop.deadline(), Some(deadline));
        assert_eq!(service.snapshot().phase, "stopping");
        assert_eq!(receiver.len(), 1);
        receiver.recv().unwrap();
        {
            let mut snapshot = service.snapshot.lock().unwrap();
            snapshot.phase = "idle".into();
            snapshot.model_ready = false;
        }
        assert!(service.send(Command::Toggle).is_err());
        assert!(receiver.is_empty());
    }
    #[test]
    fn stop_from_pause_finalizes_without_another_capture_tail() {
        let (sender, receiver) = bounded(16);
        let (cancel_sender, _) = bounded(1);
        let mut snapshot = failed_snapshot();
        snapshot.phase = "paused".into();
        let service = Service {
            capture_stop: Arc::new(CaptureStop::default()),
            marks: Arc::new(Marks::default()),
            live: Arc::default(),
            sender,
            cancel_sender,
            snapshot: Arc::new(Mutex::new(snapshot)),
        };
        service.send(Command::Stop).unwrap();
        assert_eq!(service.snapshot().phase, "finalizing");
        assert!(service.capture_stop.deadline().is_none());
        assert!(matches!(receiver.recv().unwrap(), Command::Stop));
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
            capture_stop: Arc::new(CaptureStop::default()),
            marks: Arc::new(Marks::default()),
            live: Arc::default(),
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
        snapshot.phase = "stopping".into();
        apply_cancellation(&mut snapshot, false);
        assert_eq!(snapshot.phase, "idle");
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

    #[test]
    fn standby_decoder_is_used_once_and_never_across_languages() {
        let mut standby = Standby::<&str>::default();
        assert!(standby.wanted(Language::Auto));
        assert_eq!(standby.take(Language::Auto), None);
        standby.store(Language::Auto, "primed for auto");
        assert!(!standby.wanted(Language::Auto));
        // A language change makes the idle worker prepare again.
        assert!(standby.wanted(Language::Italian));
        assert_eq!(standby.take(Language::Auto), Some("primed for auto"));
        // Consumed by that session: the next one needs a fresh decoder.
        assert_eq!(standby.take(Language::Auto), None);
        assert!(standby.wanted(Language::Auto));
        standby.store(Language::Auto, "primed for auto");
        assert_eq!(standby.take(Language::Italian), None);
        assert!(
            standby.ready.is_none(),
            "A mismatched decoder must be dropped"
        );
        standby.store(Language::English, "primed for english");
        standby.invalidate();
        assert!(standby.ready.is_none() && standby.wanted(Language::English));
        // A failed preparation waits for the next start instead of retrying every tick.
        standby.blocked = true;
        assert!(!standby.wanted(Language::English));
        assert_eq!(standby.take(Language::English), None);
        assert!(standby.wanted(Language::English));
    }
    #[test]
    fn start_and_stop_requests_are_timed_from_submission() {
        let (sender, _receiver) = bounded(16);
        let (cancel_sender, _) = bounded(1);
        let mut snapshot = failed_snapshot();
        snapshot.phase = "idle".into();
        let service = Service {
            capture_stop: Arc::new(CaptureStop::default()),
            marks: Arc::new(Marks::default()),
            live: Arc::default(),
            sender,
            cancel_sender,
            snapshot: Arc::new(Mutex::new(snapshot)),
        };
        let before = Instant::now();
        service.send(Command::Toggle).unwrap();
        let started = service.marks.start.lock().unwrap().unwrap();
        assert!(started >= before && started <= Instant::now());
        assert!(service.marks.stop.lock().unwrap().is_none());
        // Repeated controls while the start is pending do not move the mark.
        service.send(Command::Toggle).unwrap();
        assert_eq!(*service.marks.start.lock().unwrap(), Some(started));
        service.snapshot.lock().unwrap().phase = "recording".into();
        service.send(Command::Stop).unwrap();
        assert!(service.marks.stop.lock().unwrap().unwrap() >= started);
        // Loading the model is not a dictation start.
        let (sender, _receiver) = bounded(16);
        let (cancel_sender, _) = bounded(1);
        let mut snapshot = failed_snapshot();
        snapshot.phase = "idle".into();
        snapshot.model_ready = false;
        let service = Service {
            capture_stop: Arc::new(CaptureStop::default()),
            marks: Arc::new(Marks::default()),
            live: Arc::default(),
            sender,
            cancel_sender,
            snapshot: Arc::new(Mutex::new(snapshot)),
        };
        service.send(Command::Load).unwrap();
        assert!(service.marks.start.lock().unwrap().is_none());
    }
    #[test]
    fn meter_follows_the_microphone_without_the_worker_and_only_while_recording() {
        let mut state = failed_snapshot();
        state.phase = "recording".into();
        state.elapsed_seconds = 0;
        let revision = state.revision;
        let snapshot = Mutex::new(state);
        let meter = Meter::default();
        let start = Instant::now();
        *meter.clock.lock().unwrap() = Some(RecordingClock::new(start));
        // Within the same second only the level moves: no state, no transcript.
        let Some(MeterStep::Level(update)) =
            meter_step(&snapshot, &meter, start + Duration::from_millis(400))
        else {
            panic!("A level step was expected");
        };
        assert_eq!((update.revision, update.level), (revision, 0.0));
        // The worker never ran in between, as during a decode longer than a chunk.
        let Some(MeterStep::State(next)) =
            meter_step(&snapshot, &meter, start + Duration::from_millis(3_200))
        else {
            panic!("The counter must advance on its own");
        };
        assert_eq!((next.elapsed_seconds, next.revision), (3, revision + 1));
        // A stop reserved at submission ends the meter before the worker handles it.
        snapshot.lock().unwrap().phase = "stopping".into();
        assert!(meter_step(&snapshot, &meter, start + Duration::from_secs(9)).is_none());
        assert_eq!(snapshot.lock().unwrap().elapsed_seconds, 3);
    }
    #[test]
    fn level_updates_carry_no_transcript() {
        let update = serde_json::to_value(LevelUpdate {
            revision: 7,
            level: 0.25,
        })
        .unwrap();
        assert_eq!(update, serde_json::json!({"revision":7,"level":0.25}));
    }

    #[test]
    fn a_profile_reading_and_a_dictation_can_never_overlap() {
        let service = |phase: &str, model_ready: bool| {
            let (sender, receiver) = bounded(16);
            let (cancel_sender, _) = bounded(1);
            let mut snapshot = failed_snapshot();
            snapshot.phase = phase.into();
            snapshot.model_ready = model_ready;
            (
                Service {
                    capture_stop: Arc::new(CaptureStop::default()),
                    marks: Arc::new(Marks::default()),
                    live: Arc::default(),
                    sender,
                    cancel_sender,
                    snapshot: Arc::new(Mutex::new(snapshot)),
                },
                receiver,
            )
        };
        let start = || Command::Profile(ProfileCommand::Start("p001".into()));
        // Not while dictating, starting, closing, or before the model is loaded.
        for phase in ["recording", "paused", "loading", "stopping", "finalizing"] {
            let (service, receiver) = service(phase, true);
            assert!(service.send(start()).is_err(), "{phase}");
            assert!(receiver.is_empty() && service.snapshot().profile_recording.is_none());
        }
        let (unloaded, receiver) = service("idle", false);
        assert!(unloaded.send(start()).is_err());
        assert!(receiver.is_empty());
        let (service, receiver) = service("idle", true);
        assert!(
            service
                .send(Command::Profile(ProfileCommand::Stop))
                .is_err()
        );
        // Reserved at submission, before the worker opens the microphone.
        service.send(start()).unwrap();
        assert_eq!(
            service.snapshot().profile_recording.as_deref(),
            Some("p001")
        );
        assert!(service.send(start()).is_err());
        for command in [
            Command::Toggle,
            Command::Stop,
            Command::Pause,
            Command::Load,
            Command::Settings(Settings::default()),
        ] {
            assert_eq!(
                service.send(command).unwrap_err().to_string(),
                "Termina prima la registrazione del profilo vocale."
            );
        }
        assert_eq!(service.snapshot().phase, "idle");
        assert!(service.marks.start.lock().unwrap().is_none());
        service
            .send(Command::Profile(ProfileCommand::Stop))
            .unwrap();
        service.send(Command::Quit).unwrap();
        assert!(matches!(
            receiver.recv().unwrap(),
            Command::Profile(ProfileCommand::Start(id)) if id == "p001"
        ));
        assert!(matches!(
            receiver.recv().unwrap(),
            Command::Profile(ProfileCommand::Stop)
        ));
        assert!(matches!(receiver.recv().unwrap(), Command::Quit));
    }
    #[test]
    fn profile_events_name_the_sentence_and_carry_no_audio() {
        assert_eq!(
            serde_json::to_value(ProfileEvent::Progress {
                id: "p001".into(),
                seconds: 1.5,
                level: 0.25
            })
            .unwrap(),
            serde_json::json!({"kind":"progress","id":"p001","seconds":1.5,"level":0.25})
        );
        assert_eq!(
            serde_json::to_value(ProfileEvent::Error {
                id: "p001".into(),
                message: "x".into()
            })
            .unwrap(),
            serde_json::json!({"kind":"error","id":"p001","message":"x"})
        );
    }

    fn live(
        session: u64,
        base: &str,
        text: &str,
        steps: &[(i64, &str, Option<CorrectionCategory>)],
    ) -> LiveEdit {
        LiveEdit {
            session,
            base: base.into(),
            text: text.into(),
            steps: steps
                .iter()
                .map(|(segment, text, category)| LiveStep {
                    segment: *segment,
                    text: (*text).into(),
                    category: *category,
                })
                .collect(),
        }
    }

    #[test]
    fn live_corrections_apply_to_the_prefix_and_keep_later_speech() {
        let edit = live(
            1,
            "Ciao voice flow. Va",
            "Ciao VoiceFlow. Va",
            &[(
                0,
                "Ciao VoiceFlow. Va",
                Some(CorrectionCategory::Recognition),
            )],
        );
        let (delivered, steps) =
            apply_live_edit(&edit, "Ciao voice flow. Va tutto bene. ").unwrap();
        assert_eq!(delivered, "Ciao VoiceFlow. Va tutto bene.");
        assert_eq!(steps.len(), 1);
        assert_eq!(steps[0].text, delivered);
        assert_eq!(steps[0].category, Some(CorrectionCategory::Recognition));
        // A finished text that does not continue the edited base is never patched.
        assert!(apply_live_edit(&edit, "Testo diverso").is_none());
        // No net change: nothing to deliver differently or to save.
        assert!(apply_live_edit(&live(1, "a", "a ", &[(0, "a ", None)]), "a").is_none());
    }

    #[test]
    fn live_steps_skip_repeats_and_always_end_on_the_delivered_text() {
        let edit = live(
            1,
            "Uno tauri. Due flo.",
            "Uno Tauri. Due Flow. Tre",
            &[
                (0, "Uno Tauri. Due flo.", Some(CorrectionCategory::Style)),
                (0, "Uno Tauri. Due flo.", Some(CorrectionCategory::Style)),
                (
                    1,
                    "Uno Tauri. Due Flow.",
                    Some(CorrectionCategory::Recognition),
                ),
            ],
        );
        let (delivered, steps) = apply_live_edit(&edit, "Uno tauri. Due flo.").unwrap();
        assert_eq!(delivered, "Uno Tauri. Due Flow. Tre");
        let texts: Vec<_> = steps.iter().map(|step| step.text.as_str()).collect();
        assert_eq!(
            texts,
            [
                "Uno Tauri. Due flo.",
                "Uno Tauri. Due Flow.",
                "Uno Tauri. Due Flow. Tre"
            ]
        );
        assert_eq!(steps[2].category, None);
        assert_eq!(steps[2].segment, 1);
    }

    #[test]
    fn live_edits_are_accepted_only_for_the_running_session_and_its_confirmed_text() {
        let (sender, _receiver) = bounded(16);
        let (cancel_sender, _) = bounded(1);
        let mut snapshot = failed_snapshot();
        snapshot.phase = "recording".into();
        snapshot.transcript.confirmed = "Ciao voice flow. Va".into();
        let service = Service {
            capture_stop: Arc::new(CaptureStop::default()),
            marks: Arc::new(Marks::default()),
            live: Arc::default(),
            sender,
            cancel_sender,
            snapshot: Arc::new(Mutex::new(snapshot)),
        };
        assert!(
            service.live_edit(live(2, "Ciao", "Ciao!", &[])).is_err(),
            "other session"
        );
        assert!(
            service.live_edit(live(1, "Salve", "Salve!", &[])).is_err(),
            "base not confirmed"
        );
        service
            .live_edit(live(1, "Ciao voice", "Ciao Voice", &[]))
            .unwrap();
        assert!(service.live.lock().unwrap().is_some());
        // Undoing every change in the HUD clears the pending correction.
        service
            .live_edit(live(1, "Ciao voice", "Ciao voice", &[]))
            .unwrap();
        assert!(service.live.lock().unwrap().is_none());
        service.snapshot.lock().unwrap().phase = "complete".into();
        assert!(
            service.live_edit(live(1, "Ciao", "Ciao!", &[])).is_err(),
            "too late"
        );
    }

    #[test]
    fn copy_only_and_kept_errors_reserve_finalization_like_a_stop() {
        let service = |phase: &str, recoverable: bool| {
            let (sender, receiver) = bounded(16);
            let (cancel_sender, _) = bounded(1);
            let mut snapshot = failed_snapshot();
            snapshot.phase = phase.into();
            snapshot.recoverable = recoverable;
            (
                Service {
                    capture_stop: Arc::new(CaptureStop::default()),
                    marks: Arc::new(Marks::default()),
                    live: Arc::default(),
                    sender,
                    cancel_sender,
                    snapshot: Arc::new(Mutex::new(snapshot)),
                },
                receiver,
            )
        };
        let (recording, queue) = service("recording", false);
        recording.send(Command::StopCopy).unwrap();
        assert_eq!(recording.snapshot().phase, "stopping");
        assert!(matches!(queue.recv().unwrap(), Command::StopCopy));
        // An error without a kept session has nothing to dismiss.
        let (lost, queue) = service("error", false);
        lost.send(Command::Dismiss).unwrap();
        assert!(queue.is_empty());
        assert_eq!(lost.snapshot().phase, "error");
        // A kept session finishes instead of starting a new dictation.
        for command in [Command::Toggle, Command::Dismiss] {
            let (kept, queue) = service("error", true);
            kept.send(command).unwrap();
            assert_eq!(kept.snapshot().phase, "finalizing");
            assert_eq!(queue.len(), 1);
        }
        let mut snapshot = failed_snapshot();
        snapshot.recoverable = true;
        apply_cancellation(&mut snapshot, false);
        assert!(!snapshot.recoverable);
    }

    #[test]
    fn copy_only_never_pastes_and_reports_an_empty_dictation() {
        let copied = RefCell::new(String::new());
        assert_eq!(
            copy_transcript("Testo", |text| {
                copied.replace(text.into());
                Ok(())
            })
            .unwrap(),
            "Trascrizione copiata negli appunti"
        );
        assert_eq!(*copied.borrow(), "Testo");
        assert_eq!(
            copy_transcript("", |_| panic!("Nothing to copy")).unwrap(),
            "Nessun parlato rilevato"
        );
        assert!(copy_transcript("x", |_| Err(anyhow!("clipboard"))).is_err());
    }
}
