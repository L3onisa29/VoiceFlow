use crate::output_audio::OutputProtection;
use anyhow::{Result, anyhow, bail};
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use crossbeam_channel::{Receiver, Sender, unbounded};
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

pub(crate) fn capture_frames() -> (Sender<Vec<f32>>, Receiver<Vec<f32>>) {
    // Retain outstanding audio when inference falls behind. The consumer frees
    // each frame after decoding; stopping capture makes the backlog finite.
    unbounded()
}

impl Capture {
    pub fn start() -> Result<Self> {
        let host = cpal::default_host();
        let device = select_input_device(&host)?;
        let config = device.default_input_config()?;
        let channels = config.channels() as usize;
        let sample_rate = config.sample_rate().0 as usize;
        let (sender, frames) = capture_frames();
        let failed = Arc::new(AtomicBool::new(false));
        let on_error = Arc::clone(&failed);
        let callback_failed = Arc::clone(&failed);
        let output = OutputProtection::start(Arc::clone(&failed))?;
        let send = move |data: &[f32]| {
            // Never wait for inference on the CoreAudio thread or discard audio
            // because the decoder is still processing an earlier chunk.
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

fn select_input_device(host: &cpal::Host) -> Result<cpal::Device> {
    if let Some(name) = crate::output_audio::capture_input_name()? {
        // CPAL 0.16 does not expose native device IDs. Resolve the exact name
        // obtained from CoreAudio, rejecting ambiguity instead of guessing.
        let mut matches = host
            .devices()?
            .filter(|device| device.name().ok().as_ref() == Some(&name));
        let device = matches
            .next()
            .ok_or_else(|| anyhow!("Microfono {name} non disponibile"))?;
        anyhow::ensure!(
            matches.next().is_none(),
            "Più microfoni hanno il nome {name}"
        );
        return Ok(device);
    }
    host.default_input_device()
        .ok_or_else(|| anyhow!("Nessun microfono disponibile"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn busy_decoder_retains_every_callback_until_capture_stops() {
        let (sender, frames) = capture_frames();
        // No receiver runs while inference is busy. Stop must retain every
        // callback in order, including the last incomplete audio frame.
        for index in 0..512 {
            assert!(
                sender.try_send(vec![index as f32; 317]).is_ok(),
                "Lost microphone callback {index} while the decoder was busy"
            );
        }
        drop(sender);
        let captured: Vec<_> = frames.try_iter().collect();
        assert_eq!(captured.len(), 512);
        for (index, frame) in captured.iter().enumerate() {
            assert_eq!(frame, &vec![index as f32; 317]);
        }
    }

    #[test]
    #[ignore = "Requires Bluetooth as the system input and the MacBook microphone"]
    fn bluetooth_default_does_not_select_the_headset_microphone() {
        let host = cpal::default_host();
        let selected = select_input_device(&host).unwrap();
        let default = host.default_input_device().unwrap();
        let expected = crate::output_audio::capture_input_name()
            .unwrap()
            .expect("Select a Bluetooth input for this test");
        assert_eq!(selected.name().unwrap(), expected);
        assert_ne!(
            selected.name().unwrap(),
            default.name().unwrap(),
            "Opening the default Bluetooth microphone degrades headphone playback"
        );
    }
}
