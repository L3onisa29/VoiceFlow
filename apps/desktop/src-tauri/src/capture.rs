use crate::output_audio::OutputProtection;
use anyhow::{Result, anyhow, bail};
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use crossbeam_channel::{Receiver, bounded};
use std::sync::{
    Arc,
    atomic::{AtomicBool, Ordering},
};

pub struct Capture {
    _stream: cpal::Stream,
    // Fields drop in declaration order: stop capture before restoring playback.
    output: OutputProtection,
    pub frames: Receiver<Vec<f32>>,
    pub failed: Arc<AtomicBool>,
    pub sample_rate: usize,
    pub device_name: String,
}
impl Capture {
    pub fn start() -> Result<Self> {
        let host = cpal::default_host();
        let device = host
            .default_input_device()
            .ok_or_else(|| anyhow!("Nessun microfono disponibile"))?;
        let config = device.default_input_config()?;
        let channels = config.channels() as usize;
        let sample_rate = config.sample_rate().0 as usize;
        let (sender, frames) = bounded(128);
        let failed = Arc::new(AtomicBool::new(false));
        let on_error = Arc::clone(&failed);
        let callback_failed = Arc::clone(&failed);
        let output = OutputProtection::start(Arc::clone(&failed))?;
        let send = move |data: &[f32]| {
            // Never block the CoreAudio thread. An overrun fails visibly rather
            // than silently losing words or growing an unbounded audio queue.
            let mono: Vec<_> = data
                .chunks_exact(channels)
                .map(|frame| frame.iter().sum::<f32>() / channels as f32)
                .collect();
            if sender.try_send(mono).is_err() {
                callback_failed.store(true, Ordering::Release);
            }
        };
        let error = move |_error| {
            on_error.store(true, Ordering::Release);
        };
        let stream = match config.sample_format() {
            cpal::SampleFormat::F32 => device.build_input_stream(
                &config.into(),
                move |d: &[f32], _| send(d),
                error,
                None,
            )?,
            cpal::SampleFormat::I16 => device.build_input_stream(
                &config.into(),
                move |d: &[i16], _| {
                    send(&d.iter().map(|&v| v as f32 / 32768.0).collect::<Vec<_>>());
                },
                error,
                None,
            )?,
            cpal::SampleFormat::U16 => device.build_input_stream(
                &config.into(),
                move |d: &[u16], _| {
                    send(
                        &d.iter()
                            .map(|&v| (v as f32 - 32768.0) / 32768.0)
                            .collect::<Vec<_>>(),
                    );
                },
                error,
                None,
            )?,
            _ => bail!("Formato del microfono non supportato"),
        };
        let device_name = device
            .name()
            .unwrap_or_else(|_| "Microfono di sistema".into());
        stream.play()?;
        Ok(Self {
            _stream: stream,
            output,
            frames,
            failed,
            sample_rate,
            device_name,
        })
    }
    pub fn finish(self) -> Result<Receiver<Vec<f32>>> {
        self._stream.pause()?;
        drop(self._stream);
        self.output.finish()?;
        anyhow::ensure!(
            !self.failed.load(Ordering::Acquire),
            "Acquisizione audio incompleta. Recupera il testo parziale."
        );
        Ok(self.frames)
    }
}
