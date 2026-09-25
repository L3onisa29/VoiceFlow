use crate::audio::SAMPLE_RATE;
use crate::formatting::{FormatMode, TextProcessor};
use anyhow::{Result, ensure};
use parakeet_rs::{Nemotron, NemotronHandle, NemotronMode};
use serde::{Deserialize, Serialize};
use std::{collections::VecDeque, path::Path, time::Instant};

pub const MODEL_ID: &str = "nvidia/nemotron-3.5-asr-streaming-0.6b";
pub const MODEL_FILES: [&str; 3] = ["encoder.onnx", "decoder_joint.onnx", "tokenizer.model"];

#[derive(Debug, Clone, Copy, Default, Deserialize, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum Language {
    #[default]
    Auto,
    Italian,
    English,
}
impl Language {
    pub fn code(self) -> &'static str {
        match self {
            Self::Auto => "auto",
            Self::Italian => "it-IT",
            Self::English => "en-US",
        }
    }
}

pub fn load_model(path: &Path) -> Result<NemotronHandle> {
    for file in MODEL_FILES {
        ensure!(path.join(file).is_file(), "Missing model file: {file}");
    }
    let model = NemotronHandle::from_pretrained(path, None)?;
    ensure!(
        model.mode() == NemotronMode::Multilingual,
        "VoiceFlow requires NVIDIA Nemotron 3.5 multilingual"
    );
    Ok(model)
}

pub trait StreamingDecoder {
    fn chunk_samples(&self) -> usize;
    fn feed(&mut self, samples: &[f32]) -> Result<()>;
    fn text(&self) -> String;
    fn reset(&mut self);
}

pub struct NvidiaDecoder {
    model: Nemotron,
    needs_preroll: bool,
    preroll_chunks: usize,
}
impl NvidiaDecoder {
    pub fn new(handle: &NemotronHandle, language: Language) -> Result<Self> {
        let mut model = Nemotron::from_shared(handle);
        model.set_target_lang(language.code())?;
        Ok(Self {
            model,
            needs_preroll: true,
            preroll_chunks: if matches!(language, Language::Auto) {
                4
            } else {
                2
            },
        })
    }
}
impl StreamingDecoder for NvidiaDecoder {
    fn chunk_samples(&self) -> usize {
        self.model.chunk_samples()
    }
    fn feed(&mut self, samples: &[f32]) -> Result<()> {
        if self.needs_preroll {
            // With empty encoder caches, Nemotron 3.5 can lose an immediate
            // short word. Two silent chunks preserve the onset, including
            // "Ciao" where one is insufficient. Auto needs four for "Pronto".
            // Apply after every reset, outside the session's VAD, which skips silence.
            // This is decoder context, not recorded audio or a wall-clock wait.
            let silence = vec![0.0; self.model.chunk_samples()];
            for _ in 0..self.preroll_chunks {
                self.model.transcribe_chunk(&silence)?;
            }
            self.needs_preroll = false;
        }
        self.model.transcribe_chunk(samples)?;
        Ok(())
    }
    fn text(&self) -> String {
        self.model.get_transcript()
    }
    fn reset(&mut self) {
        self.model.reset();
        self.needs_preroll = true;
    }
}

#[derive(Clone, Default, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Transcript {
    pub confirmed: String,
    pub partial: String,
    pub audio_seconds: f64,
    pub inference_seconds: f64,
    pub chunks: usize,
    pub finished: bool,
}
impl Transcript {
    pub fn text(&self) -> String {
        format!("{}{}", self.confirmed, self.partial)
            .trim()
            .to_owned()
    }
}

/// The only audio retained here is a sub-chunk remainder. Encoder caches live in
/// the decoder. Silence commits an utterance, but never stops the dictation.
pub struct StreamingSession<D: StreamingDecoder> {
    decoder: D,
    pending: VecDeque<f32>,
    transcript: Transcript,
    processor: TextProcessor,
    silence_samples: usize,
    speech_seen: bool,
    original_utterances: Vec<String>,
}
impl<D: StreamingDecoder> StreamingSession<D> {
    pub fn new(decoder: D) -> Self {
        Self::with_processing(decoder, FormatMode::Faithful, false)
    }
    pub fn with_processing(decoder: D, mode: FormatMode, cleanup: bool) -> Self {
        Self {
            decoder,
            pending: VecDeque::new(),
            transcript: Transcript::default(),
            processor: TextProcessor::new(mode, cleanup),
            silence_samples: 0,
            speech_seen: false,
            original_utterances: Vec::new(),
        }
    }
    pub fn with_dictionary(
        decoder: D,
        mode: FormatMode,
        cleanup: bool,
        dictionary: crate::dictionary::Dictionary,
    ) -> Self {
        Self {
            processor: TextProcessor::with_dictionary(mode, cleanup, dictionary),
            ..Self::with_processing(decoder, mode, cleanup)
        }
    }
    pub fn snapshot(&self) -> Transcript {
        let (confirmed, partial) = self.processor.parts();
        Transcript {
            confirmed: confirmed.to_owned(),
            partial,
            ..self.transcript.clone()
        }
    }
    pub fn push(&mut self, samples: &[f32]) -> Result<()> {
        ensure!(!self.transcript.finished, "Session already finished");
        ensure!(
            samples.iter().all(|s| s.is_finite()),
            "Non-finite audio sample"
        );
        self.transcript.audio_seconds += samples.len() as f64 / SAMPLE_RATE as f64;
        self.pending.extend(samples);
        let size = self.decoder.chunk_samples();
        ensure!(size > 0, "Invalid decoder chunk size");
        while self.pending.len() >= size {
            let chunk: Vec<f32> = self.pending.drain(..size).collect();
            self.decode(&chunk)?;
        }
        Ok(())
    }
    fn decode(&mut self, samples: &[f32]) -> Result<()> {
        let rms = (samples.iter().map(|v| v * v).sum::<f32>() / samples.len() as f32).sqrt();
        if rms > 0.001 {
            self.speech_seen = true;
            self.silence_samples = 0;
        } else {
            self.silence_samples += samples.len();
        }
        if !self.speech_seen {
            return Ok(());
        }
        let started = Instant::now();
        self.decoder.feed(samples)?;
        self.transcript.inference_seconds += started.elapsed().as_secs_f64();
        self.transcript.chunks += 1;
        self.processor.update(&self.decoder.text(), false)?;
        if self.silence_samples >= SAMPLE_RATE * 2 {
            self.commit()?;
            self.decoder.reset();
            self.speech_seen = false;
            self.silence_samples = 0;
        }
        Ok(())
    }
    fn commit(&mut self) -> Result<()> {
        let original = self.decoder.text();
        self.processor.update(&original, true)?;
        // A decoder snapshot replaces its own revisable utterance. Retain only
        // the final snapshot before reset, never intermediate hypotheses.
        if self.speech_seen {
            self.original_utterances.push(original);
        }
        Ok(())
    }
    /// Exact ASR snapshots at utterance boundaries, including markers/commands.
    /// Available for persistence only after successful finalization.
    pub fn original_utterances(&self) -> Option<&[String]> {
        self.transcript
            .finished
            .then_some(&self.original_utterances)
    }
    pub fn finish(&mut self) -> Result<Transcript> {
        if self.transcript.finished {
            return Ok(self.snapshot());
        }
        self.flush_utterance()?;
        self.transcript.finished = true;
        Ok(self.snapshot())
    }
    /// Commit the audio tail at pause, retaining text and allowing a fresh stream.
    pub fn flush_utterance(&mut self) -> Result<()> {
        ensure!(!self.transcript.finished, "Session already finished");
        let size = self.decoder.chunk_samples();
        ensure!(size > 0, "Invalid decoder chunk size");
        if !self.pending.is_empty() {
            let mut tail: Vec<f32> = self.pending.drain(..).collect();
            tail.resize(size, 0.0);
            self.decode(&tail)?;
        }
        // Supply lookahead for the final word. These samples are not counted as
        // recorded duration and the original audio is never transcribed again.
        for _ in 0..3 {
            self.decode(&vec![0.0; size])?;
        }
        self.commit()?;
        self.decoder.reset();
        self.speech_seen = false;
        self.silence_samples = 0;
        Ok(())
    }
}

pub type ModelHandle = NemotronHandle;

#[cfg(test)]
mod tests {
    use super::*;
    struct Decoder {
        samples: Vec<f32>,
        text: String,
    }
    impl StreamingDecoder for Decoder {
        fn chunk_samples(&self) -> usize {
            8_960
        }
        fn feed(&mut self, samples: &[f32]) -> Result<()> {
            self.samples.extend(samples);
            self.text = "Una prova.".into();
            Ok(())
        }
        fn text(&self) -> String {
            self.text.clone()
        }
        fn reset(&mut self) {
            self.text.clear();
        }
    }
    #[test]
    fn long_sessions_stream_before_stop_and_have_no_120_second_limit() {
        let mut session = StreamingSession::new(Decoder {
            samples: Vec::new(),
            text: String::new(),
        });
        for _ in 0..16_000 {
            session.push(&[0.1; 320]).unwrap();
        }
        assert_eq!(session.snapshot().text(), "Una prova.");
        assert!(session.snapshot().audio_seconds > 300.0);
        assert!(session.pending.len() < 8_960);
        assert!(!session.snapshot().finished);
        let final_text = session.finish().unwrap();
        assert_eq!(final_text.text(), "Una prova.");
        let fed = session.decoder.samples.len();
        assert_eq!(session.finish().unwrap().text(), "Una prova.");
        assert_eq!(session.decoder.samples.len(), fed);
        assert!(session.push(&[0.1]).is_err());
    }
    #[test]
    fn silence_does_not_create_a_transcript() {
        let mut session = StreamingSession::new(Decoder {
            samples: Vec::new(),
            text: String::new(),
        });
        session.push(&[0.0; 16_000]).unwrap();
        assert!(session.finish().unwrap().text().is_empty());
    }
    #[test]
    fn pauses_commit_text_and_allow_more_speech_without_ending_the_session() {
        let mut session = StreamingSession::new(Decoder {
            samples: Vec::new(),
            text: String::new(),
        });
        session.push(&[0.1; 8_960]).unwrap();
        session.push(&[0.0; 8_960 * 5]).unwrap();
        let paused = session.snapshot();
        assert_eq!(paused.confirmed, "Una prova.");
        assert!(paused.partial.is_empty());
        assert!(!paused.finished);
        session.push(&[0.1; 8_960]).unwrap();
        assert_eq!(session.finish().unwrap().text(), "Una prova. Una prova.");
    }
    #[test]
    fn explicit_pause_flushes_tail_once_and_resume_keeps_previous_words() {
        let mut session = StreamingSession::new(Decoder {
            samples: Vec::new(),
            text: String::new(),
        });
        session.push(&[0.1; 320]).unwrap();
        session.flush_utterance().unwrap();
        assert_eq!(session.snapshot().confirmed, "Una prova.");
        assert!(!session.snapshot().finished);
        let fed = session.decoder.samples.len();
        session.flush_utterance().unwrap();
        assert_eq!(session.decoder.samples.len(), fed);
        session.push(&[0.1; 640]).unwrap();
        assert_eq!(session.finish().unwrap().text(), "Una prova. Una prova.");
        assert_eq!(session.snapshot().audio_seconds, 960.0 / 16_000.0);
    }
    #[test]
    fn invalid_audio_is_rejected_before_duration_or_pending_audio_changes() {
        let mut session = StreamingSession::new(Decoder {
            samples: Vec::new(),
            text: String::new(),
        });
        assert!(session.push(&[f32::NAN]).is_err());
        assert_eq!(session.snapshot().audio_seconds, 0.0);
        assert!(session.pending.is_empty());
    }
}

#[cfg(test)]
mod processing_tests {
    use super::*;
    use crate::formatting::MUTABLE_WORDS;

    struct Snapshots {
        updates: VecDeque<&'static str>,
        current: String,
        fail: bool,
    }
    impl StreamingDecoder for Snapshots {
        fn chunk_samples(&self) -> usize {
            8_960
        }
        fn feed(&mut self, _: &[f32]) -> Result<()> {
            ensure!(!self.fail, "test decoder failure");
            if let Some(text) = self.updates.pop_front() {
                self.current = text.into();
            }
            Ok(())
        }
        fn text(&self) -> String {
            self.current.clone()
        }
        fn reset(&mut self) {
            self.current.clear();
        }
    }
    fn session(
        updates: &[&'static str],
        mode: FormatMode,
        cleanup: bool,
    ) -> StreamingSession<Snapshots> {
        StreamingSession::with_processing(
            Snapshots {
                updates: updates.iter().copied().collect(),
                current: String::new(),
                fail: false,
            },
            mode,
            cleanup,
        )
    }
    fn step(session: &mut StreamingSession<Snapshots>) -> Transcript {
        session.push(&[0.1; 8_960]).unwrap();
        assert!(session.processor.buffered_words() <= MUTABLE_WORDS);
        session.snapshot()
    }

    #[test]
    fn dictionary_snapshot_survives_pause_resume_and_original_remains_exact() {
        use crate::dictionary::{Context, Dictionary, MatchMode, Rule};
        let dictionary = Dictionary::compile(vec![Rule {
            id: 1,
            variant: "voice flow".into(),
            preferred: "VoiceFlow".into(),
            mode: MatchMode::Exact,
            context: Context::default(),
            scope: 0,
        }])
        .unwrap();
        let mut active = StreamingSession::with_dictionary(
            Snapshots {
                updates: ["voice fl", "voice flow "].into_iter().collect(),
                current: String::new(),
                fail: false,
            },
            FormatMode::Faithful,
            false,
            dictionary,
        );
        assert_eq!(step(&mut active).text(), "voice fl");
        assert_eq!(step(&mut active).text(), "VoiceFlow");
        active.flush_utterance().unwrap();
        active.flush_utterance().unwrap();
        active.decoder.updates.push_back("voice flow non 42");
        step(&mut active);
        assert_eq!(
            active.finish().unwrap().text(),
            "VoiceFlow VoiceFlow non 42"
        );
        assert_eq!(
            active.original_utterances().unwrap(),
            ["voice flow ", "voice flow non 42"]
        );
        assert_eq!(
            active.finish().unwrap().text(),
            "VoiceFlow VoiceFlow non 42"
        );
    }

    #[test]
    fn original_asr_keeps_final_revised_utterances_before_processing() {
        let mut session = session(
            &["<unk> la sch", "<unk> il la scheda a capo fine"],
            FormatMode::SpokenCommands,
            true,
        );
        step(&mut session);
        assert!(session.original_utterances().is_none());
        step(&mut session);
        session.flush_utterance().unwrap();
        session.flush_utterance().unwrap();
        session
            .decoder
            .updates
            .push_back("molto molto importante non 42");
        step(&mut session);
        let final_text = session.finish().unwrap().text();
        assert!(!final_text.contains("<unk>"));
        assert!(final_text.contains("la scheda\nfine"));
        let expected = [
            "<unk> il la scheda a capo fine",
            "molto molto importante non 42",
        ];
        assert_eq!(session.original_utterances().unwrap(), expected);
        session.finish().unwrap();
        assert_eq!(session.original_utterances().unwrap(), expected);
    }

    #[test]
    fn silence_reset_and_empty_pause_never_duplicate_original_asr() {
        let mut session = session(&["prima"], FormatMode::Faithful, false);
        session.flush_utterance().unwrap();
        step(&mut session);
        session.push(&[0.0; 8_960 * 5]).unwrap();
        session.flush_utterance().unwrap();
        session.decoder.updates.push_back("seconda");
        step(&mut session);
        session.finish().unwrap();
        assert_eq!(session.original_utterances().unwrap(), ["prima", "seconda"]);
    }

    #[test]
    fn failure_has_no_completed_original_and_new_session_has_no_old_original() {
        let mut failed = session(&["errore"], FormatMode::Faithful, false);
        step(&mut failed);
        failed.decoder.fail = true;
        assert!(failed.finish().is_err());
        assert!(failed.original_utterances().is_none());
        let mut fresh = session(&[], FormatMode::Faithful, false);
        fresh.finish().unwrap();
        assert!(fresh.original_utterances().unwrap().is_empty());
    }

    #[test]
    fn decoder_markers_are_filtered_through_preview_pause_resume_and_stop() {
        let mut session = session(
            &["<un", "<unk> Ciao <noi", "<unk> Ciao <noise> mondo"],
            FormatMode::Faithful,
            false,
        );
        assert_eq!(step(&mut session).text(), "");
        assert_eq!(step(&mut session).text(), "Ciao");
        assert_eq!(step(&mut session).text(), "Ciao mondo");
        session.flush_utterance().unwrap();
        assert_eq!(session.snapshot().confirmed, "Ciao mondo");
        session.decoder.updates.push_back("<unk> ancora <eos>");
        assert_eq!(step(&mut session).text(), "Ciao mondo ancora");
        let final_text = session.finish().unwrap();
        assert_eq!(final_text.text(), "Ciao mondo ancora");
        assert!(final_text.partial.is_empty());
    }

    #[test]
    fn real_session_processes_fragmented_words_commands_and_cleanup_before_stop() {
        let mut session = session(
            &[
                "il la sch",
                "il la sch",
                "il la scheda a",
                "il la scheda a cap",
                "il la scheda a capo ",
                "il la scheda a capo della dell’acquisizione fine",
            ],
            FormatMode::SpokenCommands,
            true,
        );
        assert_eq!(step(&mut session).text(), "la sch");
        assert_eq!(step(&mut session).text(), "la sch");
        assert_eq!(step(&mut session).text(), "la scheda a");
        assert_eq!(step(&mut session).text(), "la scheda a cap");
        let newline = step(&mut session);
        assert_eq!(
            format!("{}{}", newline.confirmed, newline.partial),
            "la scheda\n"
        );
        let live = step(&mut session);
        assert_eq!(live.text(), "la scheda\ndell’acquisizione fine");
        let final_text = session.finish().unwrap();
        assert_eq!(live.text(), final_text.text());
        assert!(final_text.partial.is_empty());
        assert_eq!(session.finish().unwrap().text(), final_text.text());
    }

    #[test]
    fn real_session_revises_cleanup_dependency_before_finalization() {
        for (before, after) in [
            (
                "di di uno due tre quattro cinque sei ",
                "di domani uno due tre quattro cinque sei ",
            ),
            (
                "il la uno due tre quattro cinque sei ",
                "il lo uno due tre quattro cinque sei ",
            ),
        ] {
            let mut session = session(&[before, after], FormatMode::Faithful, true);
            step(&mut session);
            assert_eq!(step(&mut session).text(), after.trim());
            assert_eq!(session.finish().unwrap().text(), after.trim());
        }
    }

    #[test]
    fn opt_off_is_literal_through_pause_resume_silence_and_finalization() {
        let text = "il la scheda a capo molto molto importante.  Non cambiare 12 12.";
        let mut session = session(&[text], FormatMode::Faithful, false);
        assert_eq!(step(&mut session).text(), text);
        session.flush_utterance().unwrap();
        assert_eq!(session.snapshot().confirmed, text);
        session.flush_utterance().unwrap();
        assert_eq!(session.snapshot().confirmed, text);
        session
            .decoder
            .updates
            .push_back("quel ragionamento di prima non va bene");
        step(&mut session);
        session.push(&[0.0; 8_960 * 4]).unwrap();
        assert!(session.snapshot().partial.is_empty());
        let expected = format!("{text} quel ragionamento di prima non va bene");
        assert_eq!(session.finish().unwrap().text(), expected);
    }

    #[test]
    fn commands_remain_atomic_at_release_boundary_and_newlines_survive_pause() {
        let mut session = session(
            &[
                "Prima a",
                "Prima a capo uno due tre quattro cinque sei sette otto ",
            ],
            FormatMode::SpokenCommands,
            false,
        );
        step(&mut session);
        let live = step(&mut session);
        assert_eq!(live.confirmed, "Prima\n");
        assert_eq!(live.partial, "uno due tre quattro cinque sei sette otto");
        session
            .decoder
            .updates
            .push_back("Prima a capo uno due tre quattro cinque sei sette otto nuovo paragrafo");
        step(&mut session);
        session.flush_utterance().unwrap();
        assert!(session.snapshot().confirmed.ends_with("\n\n"));
        session.decoder.updates.push_back("punto elenco latte");
        step(&mut session);
        assert_eq!(
            session.finish().unwrap().text(),
            "Prima\nuno due tre quattro cinque sei sette otto\n\n• latte"
        );
    }

    #[test]
    fn incomplete_commands_flush_literally_and_do_not_join_across_pauses() {
        let mut session = session(&["testo a"], FormatMode::SpokenCommands, true);
        step(&mut session);
        session.flush_utterance().unwrap();
        session.decoder.updates.push_back("capo");
        step(&mut session);
        assert_eq!(session.finish().unwrap().text(), "testo a capo");
    }

    #[test]
    fn failure_preserves_processed_preview_and_a_new_session_has_no_old_buffer() {
        let mut failed = session(
            &["il la scheda a capo fine"],
            FormatMode::SpokenCommands,
            true,
        );
        let before = step(&mut failed).text();
        failed.decoder.fail = true;
        assert!(failed.push(&[0.1; 8_960]).is_err());
        assert_eq!(failed.snapshot().text(), before);
        drop(failed);
        let mut fresh = session(&["nuova sessione"], FormatMode::Faithful, false);
        step(&mut fresh);
        assert_eq!(fresh.finish().unwrap().text(), "nuova sessione");
    }
}
