use crate::audio::SAMPLE_RATE;
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

pub struct NvidiaDecoder(Nemotron);
impl NvidiaDecoder {
    pub fn new(handle: &NemotronHandle, language: Language) -> Result<Self> {
        let mut model = Nemotron::from_shared(handle);
        model.set_target_lang(language.code())?;
        Ok(Self(model))
    }
}
impl StreamingDecoder for NvidiaDecoder {
    fn chunk_samples(&self) -> usize {
        self.0.chunk_samples()
    }
    fn feed(&mut self, samples: &[f32]) -> Result<()> {
        self.0.transcribe_chunk(samples)?;
        Ok(())
    }
    fn text(&self) -> String {
        self.0.get_transcript()
    }
    fn reset(&mut self) {
        self.0.reset();
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
        [self.confirmed.trim(), self.partial.trim()]
            .into_iter()
            .filter(|s| !s.is_empty())
            .collect::<Vec<_>>()
            .join(" ")
    }
}

/// The only audio retained here is a sub-chunk remainder. Encoder caches live in
/// the decoder. Silence commits an utterance, but never stops the dictation.
pub struct StreamingSession<D: StreamingDecoder> {
    decoder: D,
    pending: VecDeque<f32>,
    transcript: Transcript,
    silence_samples: usize,
    speech_seen: bool,
}
impl<D: StreamingDecoder> StreamingSession<D> {
    pub fn new(decoder: D) -> Self {
        Self {
            decoder,
            pending: VecDeque::new(),
            transcript: Transcript::default(),
            silence_samples: 0,
            speech_seen: false,
        }
    }
    pub fn snapshot(&self) -> Transcript {
        self.transcript.clone()
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
        self.transcript.partial = self.decoder.text();
        if self.silence_samples >= SAMPLE_RATE * 2 {
            self.commit();
            self.decoder.reset();
            self.speech_seen = false;
            self.silence_samples = 0;
        }
        Ok(())
    }
    fn commit(&mut self) {
        self.transcript.confirmed = self.transcript.text();
        self.transcript.partial.clear();
    }
    pub fn finish(&mut self) -> Result<Transcript> {
        if self.transcript.finished {
            return Ok(self.snapshot());
        }
        let size = self.decoder.chunk_samples();
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
        self.commit();
        self.decoder.reset();
        self.transcript.finished = true;
        Ok(self.snapshot())
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
