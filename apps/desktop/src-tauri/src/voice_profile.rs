//! Guided recordings of known sentences: audio with an exact reference text, kept only on
//! this Mac. It is the single feature that writes audio to disk, and only for a sentence
//! the person chose to read on its page. Ordinary dictation audio is never stored.
//!
//! Layout under `<data>/voice-profile/`, all private:
//! `prompts.json` (read, never written here), `audio/<id>.wav` + `audio/<id>.json`,
//! `manifest.jsonl`, `manifest-train.jsonl`, `manifest-test.jsonl`, `state.json`.
use anyhow::{Context, Result, bail, ensure};
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeSet, HashMap, HashSet},
    fs,
    io::Write,
    os::unix::fs::{DirBuilderExt, OpenOptionsExt, PermissionsExt},
    path::{Path, PathBuf},
};
use voiceflow_core::audio::{SAMPLE_RATE, encode_wav};

pub mod evaluation;

/// Shorter than this is a slip of the finger, not a sentence.
pub const MIN_SAMPLES: usize = SAMPLE_RATE / 2;
/// A read sentence never needs more; it bounds memory and file size.
pub const MAX_SAMPLES: usize = SAMPLE_RATE * 60;

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Split {
    #[default]
    Train,
    Test,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Prompt {
    pub id: String,
    pub text: String,
    #[serde(default)]
    pub split: Split,
}
#[derive(Deserialize)]
struct PromptFile {
    #[serde(default = "italian")]
    language: String,
    prompts: Vec<Prompt>,
}
fn italian() -> String {
    "it-IT".into()
}

/// Everything known about one saved recording. Lives next to its WAV.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Recording {
    pub id: String,
    /// The reference text that was on screen when it was read.
    pub text: String,
    pub split: Split,
    pub duration: f64,
    pub target_lang: String,
    /// What the model in use made of it at that moment, unprocessed.
    pub hypothesis: String,
    pub hypothesis_language: String,
    pub model: String,
    pub decoder: String,
    pub device: String,
    pub recorded_at: i64,
}
/// One line of a manifest, in the field names speech toolkits expect.
#[derive(Serialize)]
struct ManifestLine<'a> {
    audio_filepath: &'a str,
    duration: f64,
    text: &'a str,
    target_lang: &'a str,
}
#[derive(Default, Serialize, Deserialize)]
struct State {
    #[serde(default)]
    skipped: BTreeSet<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PromptStatus {
    #[serde(flatten)]
    pub prompt: Prompt,
    pub skipped: bool,
    pub recording: Option<Recording>,
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ProfileStatus {
    /// False until `prompts.json` exists in the data folder.
    pub available: bool,
    pub folder: String,
    pub language: String,
    pub prompts: Vec<PromptStatus>,
    pub recorded: usize,
    pub recorded_test: usize,
    pub seconds: f64,
    /// Prompt being recorded right now, if any.
    pub recording: Option<String>,
}

#[derive(Clone)]
pub struct ProfileStore {
    root: PathBuf,
}
fn private_dir(path: &Path) -> Result<()> {
    fs::DirBuilder::new()
        .recursive(true)
        .mode(0o700)
        .create(path)
        .with_context(|| "Impossibile creare la cartella del profilo vocale")
}
/// Whole file or nothing, readable by the owner only, never through a symlink.
fn write_private(path: &Path, bytes: &[u8]) -> Result<()> {
    let temporary = path.with_extension("tmp");
    let mut file = fs::OpenOptions::new()
        .create(true)
        .write(true)
        .truncate(true)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW)
        .open(&temporary)?;
    file.set_permissions(fs::Permissions::from_mode(0o600))?;
    file.write_all(bytes)?;
    file.sync_all()?;
    fs::rename(&temporary, path)?;
    Ok(())
}
fn safe_id(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= 40
        && id
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
}

impl ProfileStore {
    pub fn new(root: PathBuf) -> Self {
        Self { root }
    }
    fn audio(&self) -> PathBuf {
        self.root.join("audio")
    }
    pub(crate) fn wav(&self, id: &str) -> PathBuf {
        self.audio().join(format!("{id}.wav"))
    }
    fn sidecar(&self, id: &str) -> PathBuf {
        self.audio().join(format!("{id}.json"))
    }
    /// Language and sentences, or None while no script has been placed in the folder.
    fn script(&self) -> Result<Option<(String, Vec<Prompt>)>> {
        let path = self.root.join("prompts.json");
        let raw = match fs::read(&path) {
            Ok(raw) => raw,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(error) => return Err(error).context("Frasi del profilo vocale non leggibili"),
        };
        let file: PromptFile =
            serde_json::from_slice(&raw).context("prompts.json non è nel formato atteso")?;
        ensure!(
            !file.prompts.is_empty() && file.prompts.len() <= 5000,
            "prompts.json deve contenere da 1 a 5000 frasi."
        );
        let mut seen = HashSet::new();
        for prompt in &file.prompts {
            ensure!(
                safe_id(&prompt.id) && seen.insert(prompt.id.as_str()),
                "Identificativo di frase non valido o ripetuto in prompts.json."
            );
            ensure!(
                !prompt.text.trim().is_empty()
                    && prompt.text.chars().count() <= 400
                    && !prompt.text.chars().any(char::is_control),
                "Una frase di prompts.json è vuota, troppo lunga o non valida."
            );
        }
        ensure!(
            !file.language.is_empty() && file.language.len() <= 16,
            "Lingua di prompts.json non valida."
        );
        Ok(Some((file.language, file.prompts)))
    }
    pub fn prompt(&self, id: &str) -> Result<Prompt> {
        self.script()?
            .context("Nessuna frase disponibile: manca voice-profile/prompts.json.")?
            .1
            .into_iter()
            .find(|prompt| prompt.id == id)
            .context("Frase non più presente in prompts.json.")
    }
    /// Recordings that still match their sentence. One whose sentence was edited or
    /// removed, or whose audio is missing, no longer counts and leaves the manifests.
    pub(crate) fn recordings(&self, prompts: &[Prompt]) -> HashMap<String, Recording> {
        prompts
            .iter()
            .filter_map(|prompt| {
                let recording: Recording =
                    serde_json::from_slice(&fs::read(self.sidecar(&prompt.id)).ok()?).ok()?;
                (recording.id == prompt.id
                    && recording.text == prompt.text
                    && self.wav(&prompt.id).is_file())
                .then(|| {
                    (
                        prompt.id.clone(),
                        Recording {
                            split: prompt.split,
                            ..recording
                        },
                    )
                })
            })
            .collect()
    }
    fn state(&self) -> State {
        fs::read(self.root.join("state.json"))
            .ok()
            .and_then(|raw| serde_json::from_slice(&raw).ok())
            .unwrap_or_default()
    }
    pub fn status(&self, recording: Option<String>) -> Result<ProfileStatus> {
        let folder = self.root.to_string_lossy().into_owned();
        let Some((language, prompts)) = self.script()? else {
            return Ok(ProfileStatus {
                available: false,
                folder,
                language: italian(),
                prompts: vec![],
                recorded: 0,
                recorded_test: 0,
                seconds: 0.0,
                recording,
            });
        };
        let mut recordings = self.recordings(&prompts);
        let state = self.state();
        let prompts: Vec<PromptStatus> = prompts
            .into_iter()
            .map(|prompt| {
                let recording = recordings.remove(&prompt.id);
                PromptStatus {
                    skipped: recording.is_none() && state.skipped.contains(&prompt.id),
                    recording,
                    prompt,
                }
            })
            .collect();
        let saved = || prompts.iter().filter_map(|p| p.recording.as_ref());
        Ok(ProfileStatus {
            available: true,
            folder,
            language,
            recorded: saved().count(),
            recorded_test: saved().filter(|r| r.split == Split::Test).count(),
            seconds: saved().map(|r| r.duration).sum(),
            prompts,
            recording,
        })
    }
    /// Keep one reading of one sentence: WAV, its description and the manifests.
    /// A second reading of the same sentence replaces the first.
    pub fn save(&self, samples: &[f32], mut recording: Recording) -> Result<Recording> {
        ensure!(
            samples.len() >= MIN_SAMPLES,
            "Registrazione troppo breve. Leggi la frase per intero e riprova."
        );
        ensure!(
            samples.len() <= MAX_SAMPLES && samples.iter().all(|sample| sample.is_finite()),
            "Registrazione non valida o troppo lunga."
        );
        let (language, prompts) = self
            .script()?
            .context("Nessuna frase disponibile: manca voice-profile/prompts.json.")?;
        let prompt = prompts
            .iter()
            .find(|prompt| prompt.id == recording.id)
            .context("Frase non più presente in prompts.json.")?;
        recording.text = prompt.text.clone();
        recording.split = prompt.split;
        recording.target_lang = language.clone();
        recording.duration = samples.len() as f64 / SAMPLE_RATE as f64;
        private_dir(&self.root)?;
        private_dir(&self.audio())?;
        write_private(&self.wav(&recording.id), &encode_wav(samples))?;
        write_private(
            &self.sidecar(&recording.id),
            &serde_json::to_vec_pretty(&recording)?,
        )?;
        let mut state = self.state();
        if state.skipped.remove(&recording.id) {
            write_private(&self.root.join("state.json"), &serde_json::to_vec(&state)?)?;
        }
        self.write_manifests(&language, &prompts)?;
        Ok(recording)
    }
    fn write_manifests(&self, language: &str, prompts: &[Prompt]) -> Result<()> {
        let recordings = self.recordings(prompts);
        for (name, split) in [
            ("manifest.jsonl", None),
            ("manifest-train.jsonl", Some(Split::Train)),
            ("manifest-test.jsonl", Some(Split::Test)),
        ] {
            let mut lines = Vec::new();
            for prompt in prompts {
                let Some(recording) = recordings.get(&prompt.id) else {
                    continue;
                };
                if split.is_some_and(|split| split != prompt.split) {
                    continue;
                }
                serde_json::to_writer(
                    &mut lines,
                    &ManifestLine {
                        audio_filepath: &self.wav(&prompt.id).to_string_lossy(),
                        duration: recording.duration,
                        text: &prompt.text,
                        target_lang: language,
                    },
                )?;
                lines.push(b'\n');
            }
            write_private(&self.root.join(name), &lines)?;
        }
        Ok(())
    }
    /// Set a sentence aside for now, or bring it back. Never touches a recording.
    pub fn skip(&self, id: &str, skipped: bool) -> Result<()> {
        self.prompt(id)?;
        let mut state = self.state();
        let changed = if skipped {
            state.skipped.insert(id.to_owned())
        } else {
            state.skipped.remove(id)
        };
        if changed {
            private_dir(&self.root)?;
            write_private(&self.root.join("state.json"), &serde_json::to_vec(&state)?)?;
        }
        Ok(())
    }
    /// Remove every recording, description, manifest and the skip list. The script of
    /// sentences is not voice data and stays, so the collection can start again.
    pub fn delete_all(&self) -> Result<()> {
        if let Ok(entries) = fs::read_dir(self.audio()) {
            for entry in entries {
                let path = entry?.path();
                // Only what this module writes; anything else is left and reported.
                if path
                    .extension()
                    .is_some_and(|ext| ext == "wav" || ext == "json" || ext == "tmp")
                {
                    fs::remove_file(&path)?;
                }
            }
            fs::remove_dir(self.audio())
                .context("La cartella audio contiene file non creati da VoiceFlow.")?;
        }
        for name in [
            "manifest.jsonl",
            "manifest-train.jsonl",
            "manifest-test.jsonl",
            "state.json",
            "manifest.tmp",
            "manifest-train.tmp",
            "manifest-test.tmp",
            "state.tmp",
        ] {
            match fs::remove_file(self.root.join(name)) {
                Ok(()) => (),
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => (),
                Err(error) => bail!("Impossibile cancellare {name}: {error}"),
            }
        }
        Ok(())
    }
}

use crate::service::{Command, ProfileCommand, Service};
async fn blocking<T: Send + 'static>(
    job: impl FnOnce() -> Result<T> + Send + 'static,
) -> Result<T, String> {
    tauri::async_runtime::spawn_blocking(job)
        .await
        .map_err(|error| error.to_string())?
        .map_err(|error| format!("{error:#}"))
}
#[tauri::command]
pub async fn voice_profile_status(
    store: tauri::State<'_, ProfileStore>,
    service: tauri::State<'_, Service>,
) -> Result<ProfileStatus, String> {
    let (store, recording) = (store.inner().clone(), service.snapshot().profile_recording);
    blocking(move || store.status(recording)).await
}
#[tauri::command]
pub fn voice_profile_start(service: tauri::State<'_, Service>, id: String) -> Result<(), String> {
    service
        .send(Command::Profile(ProfileCommand::Start(id)))
        .map_err(|error| error.to_string())
}
#[tauri::command]
pub fn voice_profile_stop(service: tauri::State<'_, Service>) -> Result<(), String> {
    service
        .send(Command::Profile(ProfileCommand::Stop))
        .map_err(|error| error.to_string())
}
#[tauri::command]
pub fn voice_profile_cancel(service: tauri::State<'_, Service>) -> Result<(), String> {
    service
        .send(Command::Profile(ProfileCommand::Cancel))
        .map_err(|error| error.to_string())
}
#[tauri::command]
pub async fn voice_profile_skip(
    store: tauri::State<'_, ProfileStore>,
    service: tauri::State<'_, Service>,
    id: String,
    skipped: bool,
) -> Result<ProfileStatus, String> {
    let (store, recording) = (store.inner().clone(), service.snapshot().profile_recording);
    blocking(move || {
        store.skip(&id, skipped)?;
        store.status(recording)
    })
    .await
}
#[tauri::command]
pub async fn voice_profile_delete(
    store: tauri::State<'_, ProfileStore>,
    service: tauri::State<'_, Service>,
) -> Result<ProfileStatus, String> {
    if service.snapshot().profile_recording.is_some() {
        return Err("Termina prima la registrazione in corso.".into());
    }
    let store = store.inner().clone();
    blocking(move || {
        store.delete_all()?;
        store.status(None)
    })
    .await
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};
    static NEXT: AtomicU64 = AtomicU64::new(0);
    pub(crate) struct Fixture {
        pub root: PathBuf,
        pub store: ProfileStore,
    }
    impl Fixture {
        pub fn new() -> Self {
            let root = std::env::temp_dir().join(format!(
                "voiceflow-profile-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            let store = ProfileStore::new(root.join("voice-profile"));
            Self { root, store }
        }
        pub fn script(&self, prompts: &[(&str, &str, &str)]) {
            fs::create_dir_all(&self.store.root).unwrap();
            let prompts: Vec<_> = prompts
                .iter()
                .map(|(id, text, split)| serde_json::json!({"id":id,"text":text,"split":split}))
                .collect();
            fs::write(
                self.store.root.join("prompts.json"),
                serde_json::to_vec(
                    &serde_json::json!({"version":1,"language":"it-IT","prompts":prompts}),
                )
                .unwrap(),
            )
            .unwrap();
        }
        /// A synthetic tone, never a microphone.
        pub fn tone(seconds: f64, level: f32) -> Vec<f32> {
            (0..(seconds * SAMPLE_RATE as f64) as usize)
                .map(|n| (n as f32 * 0.12).sin() * level)
                .collect()
        }
        pub fn record(&self, id: &str, seconds: f64, hypothesis: &str) -> Recording {
            self.store
                .save(&Self::tone(seconds, 0.3), take(id, hypothesis))
                .unwrap()
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.root);
        }
    }
    pub(crate) fn take(id: &str, hypothesis: &str) -> Recording {
        Recording {
            id: id.into(),
            text: String::new(),
            split: Split::Train,
            duration: 0.0,
            target_lang: String::new(),
            hypothesis: hypothesis.into(),
            hypothesis_language: "auto".into(),
            model: "fixture-model".into(),
            decoder: "fixture-decoder".into(),
            device: "Fixture".into(),
            recorded_at: 1_750_000_000_000,
        }
    }
    fn mode(path: &Path) -> u32 {
        fs::metadata(path).unwrap().permissions().mode() & 0o777
    }
    fn lines(path: PathBuf) -> Vec<serde_json::Value> {
        fs::read_to_string(path)
            .unwrap()
            .lines()
            .map(|line| serde_json::from_str(line).unwrap())
            .collect()
    }

    #[test]
    fn without_a_script_nothing_is_available_and_nothing_is_created() {
        let f = Fixture::new();
        let status = f.store.status(None).unwrap();
        assert!(!status.available && status.prompts.is_empty());
        assert!(
            f.store
                .save(&Fixture::tone(1.0, 0.3), take("a", ""))
                .is_err()
        );
        assert!(!f.root.exists());
    }
    #[test]
    fn a_reading_becomes_a_private_wav_a_description_and_manifest_lines() {
        let f = Fixture::new();
        f.script(&[
            ("p001", "Apri il bottom sheet.", "train"),
            ("p002", "Carica la build su TestFlight.", "test"),
            ("p003", "Fai il commit.", "train"),
        ]);
        let saved = f.record("p002", 2.5, "carica la build su tes flight");
        assert_eq!(
            (
                saved.text.as_str(),
                saved.split,
                saved.duration,
                saved.target_lang.as_str()
            ),
            ("Carica la build su TestFlight.", Split::Test, 2.5, "it-IT")
        );
        f.record("p001", 1.0, "apri il botton shit");
        let wav = f.store.wav("p002");
        let samples = voiceflow_core::audio::decode_wav(&fs::read(&wav).unwrap()).unwrap();
        assert_eq!(samples.len(), 40_000);
        for path in [
            wav.clone(),
            f.store.sidecar("p002"),
            f.store.root.join("manifest.jsonl"),
            f.store.root.join("manifest-test.jsonl"),
        ] {
            assert_eq!(mode(&path), 0o600, "{path:?}");
        }
        assert_eq!(mode(&f.store.audio()), 0o700);
        // Script order, exactly the four agreed fields.
        let all = lines(f.store.root.join("manifest.jsonl"));
        assert_eq!(
            all,
            [
                serde_json::json!({"audio_filepath":f.store.wav("p001").to_string_lossy(),"duration":1.0,"text":"Apri il bottom sheet.","target_lang":"it-IT"}),
                serde_json::json!({"audio_filepath":wav.to_string_lossy(),"duration":2.5,"text":"Carica la build su TestFlight.","target_lang":"it-IT"}),
            ]
        );
        assert_eq!(lines(f.store.root.join("manifest-train.jsonl")).len(), 1);
        assert_eq!(
            lines(f.store.root.join("manifest-test.jsonl"))[0]["text"],
            all[1]["text"]
        );
        let status = f.store.status(Some("p003".into())).unwrap();
        assert_eq!(
            (
                status.recorded,
                status.recorded_test,
                status.seconds,
                status.recording.as_deref()
            ),
            (2, 1, 3.5, Some("p003"))
        );
        assert_eq!(
            status.prompts[1].recording.as_ref().unwrap().hypothesis,
            "carica la build su tes flight"
        );
        // Reading it again replaces the first take.
        f.record("p002", 3.0, "carica la build su testflight");
        let all = lines(f.store.root.join("manifest.jsonl"));
        assert_eq!(
            (all.len(), &all[1]["duration"]),
            (2, &serde_json::json!(3.0))
        );
        assert_eq!(fs::read_dir(f.store.audio()).unwrap().count(), 4);
    }
    #[test]
    fn unusable_takes_and_unknown_sentences_are_refused() {
        let f = Fixture::new();
        f.script(&[("p001", "Apri il bottom sheet.", "train")]);
        assert!(
            f.store
                .save(&Fixture::tone(0.2, 0.3), take("p001", ""))
                .is_err()
        );
        assert!(f.store.save(&[f32::NAN; 16_000], take("p001", "")).is_err());
        assert!(
            f.store
                .save(&vec![0.0; MAX_SAMPLES + 1], take("p001", ""))
                .is_err()
        );
        assert!(
            f.store
                .save(&Fixture::tone(1.0, 0.3), take("missing", ""))
                .is_err()
        );
        assert!(!f.store.audio().exists());
        // An id is a file name: nothing that could leave the folder is accepted.
        for id in ["../x", "a/b", "A", "", "p 1"] {
            fs::write(
                f.store.root.join("prompts.json"),
                serde_json::json!({"prompts":[{"id":id,"text":"x"}]}).to_string(),
            )
            .unwrap();
            assert!(f.store.status(None).is_err(), "{id}");
        }
    }
    #[test]
    fn skipping_is_reversible_and_recording_clears_it() {
        let f = Fixture::new();
        f.script(&[("p001", "Uno.", "train"), ("p002", "Due.", "train")]);
        f.store.skip("p001", true).unwrap();
        assert!(f.store.status(None).unwrap().prompts[0].skipped);
        assert_eq!(mode(&f.store.root.join("state.json")), 0o600);
        f.store.skip("p001", false).unwrap();
        assert!(!f.store.status(None).unwrap().prompts[0].skipped);
        f.store.skip("p002", true).unwrap();
        f.record("p002", 1.0, "due");
        let status = f.store.status(None).unwrap();
        assert!(!status.prompts[1].skipped && status.prompts[1].recording.is_some());
        assert!(f.store.skip("missing", true).is_err());
    }
    #[test]
    fn an_edited_sentence_no_longer_counts_its_old_reading() {
        let f = Fixture::new();
        f.script(&[
            ("p001", "Apri il bottom sheet.", "train"),
            ("p002", "Due.", "test"),
        ]);
        f.record("p001", 1.0, "x");
        f.record("p002", 1.0, "y");
        f.script(&[
            ("p001", "Apri la bottom bar.", "train"),
            ("p002", "Due.", "test"),
        ]);
        let status = f.store.status(None).unwrap();
        assert_eq!(status.recorded, 1);
        assert!(status.prompts[0].recording.is_none());
        // The next save rewrites the manifests without the stale pair.
        f.record("p002", 1.0, "y");
        assert_eq!(lines(f.store.root.join("manifest.jsonl")).len(), 1);
    }
    #[test]
    fn deleting_the_profile_removes_every_recording_and_keeps_the_script() {
        let f = Fixture::new();
        f.script(&[("p001", "Uno.", "train"), ("p002", "Due.", "test")]);
        f.record("p001", 1.0, "uno");
        f.store.skip("p002", true).unwrap();
        f.store.delete_all().unwrap();
        let left: Vec<_> = fs::read_dir(&f.store.root)
            .unwrap()
            .map(|entry| entry.unwrap().file_name().to_string_lossy().into_owned())
            .collect();
        assert_eq!(left, ["prompts.json"]);
        let status = f.store.status(None).unwrap();
        assert_eq!((status.recorded, status.prompts.len()), (0, 2));
        assert!(!status.prompts[1].skipped);
        // Idempotent, and the collection can start again.
        f.store.delete_all().unwrap();
        f.record("p001", 1.0, "uno");
        // A file VoiceFlow did not write is never deleted.
        fs::write(f.store.audio().join("note.txt"), "mine").unwrap();
        assert!(f.store.delete_all().is_err());
        assert!(f.store.audio().join("note.txt").exists());
        assert!(!f.store.wav("p001").exists());
    }
}
