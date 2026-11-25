use crate::output_audio::OutputProtection;
use anyhow::{Result, anyhow, bail};
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use crossbeam_channel::{Receiver, Sender, unbounded};
use std::{
    sync::{
        Arc,
        atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering},
    },
    time::{Duration, Instant},
};

pub const STOP_TAIL: Duration = Duration::from_millis(350);

/// Shared with the controls so an occupied decoder cannot extend the audio tail.
pub struct CaptureStop {
    origin: Instant,
    deadline_nanos: AtomicU64,
}

impl Default for CaptureStop {
    fn default() -> Self {
        Self {
            origin: Instant::now(),
            deadline_nanos: AtomicU64::new(u64::MAX),
        }
    }
}

impl CaptureStop {
    pub fn request_stop(&self, requested_at: Instant) {
        let nanos = (requested_at + STOP_TAIL)
            .duration_since(self.origin)
            .as_nanos() as u64;
        self.deadline_nanos.store(nanos, Ordering::Release);
    }

    fn reset(&self) {
        self.deadline_nanos.store(u64::MAX, Ordering::Release);
    }

    pub fn deadline(&self) -> Option<Instant> {
        let nanos = self.deadline_nanos.load(Ordering::Acquire);
        (nanos != u64::MAX).then(|| self.origin + Duration::from_nanos(nanos))
    }

    pub(crate) fn retained_samples(
        &self,
        count: usize,
        sample_rate: usize,
        captured_at: Instant,
    ) -> usize {
        match self.deadline() {
            None => count,
            Some(deadline) => {
                let remaining = deadline.saturating_duration_since(captured_at);
                count.min((remaining.as_nanos() * sample_rate as u128 / 1_000_000_000) as usize)
            }
        }
    }

    pub fn wait_for_tail(&self, cancellation: &Receiver<()>) -> bool {
        let Some(deadline) = self.deadline() else {
            return true;
        };
        if !cancellation.is_empty() {
            return false;
        }
        matches!(
            cancellation.recv_deadline(deadline),
            Err(crossbeam_channel::RecvTimeoutError::Timeout)
        )
    }
}

/// Loudest callback since the last reading, written on the CoreAudio thread. The
/// meter reads it without waiting for the decoder, so it moves whenever sound arrives.
#[derive(Default)]
pub struct CaptureLevel(AtomicU32);

impl CaptureLevel {
    fn record(&self, samples: &[f32]) {
        let level =
            (samples.iter().map(|v| v * v).sum::<f32>() / samples.len().max(1) as f32).sqrt();
        if level.is_finite() {
            // Non-negative floats order like their bit patterns.
            self.0.fetch_max(level.to_bits(), Ordering::Relaxed);
        }
    }

    /// The peak since the previous call. Silence when no callback ran in between.
    pub fn take(&self) -> f32 {
        f32::from_bits(self.0.swap(0, Ordering::Relaxed))
    }
}

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
    pub fn start(stop: Arc<CaptureStop>, level: Arc<CaptureLevel>) -> Result<Self> {
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
        stop.reset();
        level.take();
        let send = move |data: &[f32], info: &cpal::InputCallbackInfo| {
            let now = Instant::now();
            let timestamp = info.timestamp();
            let captured_at = timestamp
                .callback
                .duration_since(&timestamp.capture)
                .and_then(|delay| now.checked_sub(delay))
                .unwrap_or(now);
            let count = stop.retained_samples(data.len() / channels, sample_rate, captured_at);
            if count == 0 {
                return;
            }
            // Never wait for inference on the CoreAudio thread or discard audio
            // because the decoder is still processing an earlier chunk.
            let mono: Vec<_> = data
                .chunks_exact(channels)
                .take(count)
                .map(|frame| frame.iter().sum::<f32>() / channels as f32)
                .collect();
            level.record(&mono);
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
                move |d: &[f32], info| send(d, info),
                error,
                None,
            )?,
            cpal::SampleFormat::I16 => device.build_input_stream(
                &config.into(),
                move |d: &[i16], info| {
                    send(
                        &d.iter().map(|&v| v as f32 / 32768.0).collect::<Vec<_>>(),
                        info,
                    );
                },
                error,
                None,
            )?,
            cpal::SampleFormat::U16 => device.build_input_stream(
                &config.into(),
                move |d: &[u16], info| {
                    send(
                        &d.iter()
                            .map(|&v| (v as f32 - 32768.0) / 32768.0)
                            .collect::<Vec<_>>(),
                        info,
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
    fn stop_tail_cuts_inside_a_callback_at_350ms_and_resets_on_resume() {
        let stop = CaptureStop::default();
        let requested = Instant::now();
        assert_eq!(stop.retained_samples(320, 16_000, requested), 320);
        stop.request_stop(requested);
        assert_eq!(
            stop.deadline(),
            Some(requested + Duration::from_millis(350))
        );
        for rate in [16_000, 44_100, 48_000] {
            let count = rate / 50;
            assert_eq!(
                stop.retained_samples(count, rate, requested + Duration::from_millis(320)),
                count
            );
            assert_eq!(
                stop.retained_samples(count, rate, requested + Duration::from_millis(340)),
                rate / 100
            );
            assert_eq!(
                stop.retained_samples(count, rate, requested + Duration::from_millis(350)),
                0
            );
            assert_eq!(
                stop.retained_samples(count, rate, requested + Duration::from_secs(2)),
                0
            );
        }
        stop.reset();
        assert_eq!(
            stop.retained_samples(320, 16_000, requested + Duration::from_secs(2)),
            320
        );
    }

    #[test]
    fn level_keeps_the_loudest_callback_until_it_is_read() {
        let level = CaptureLevel::default();
        assert_eq!(level.take(), 0.0);
        level.record(&[0.5, -0.5]);
        level.record(&[0.1, 0.1]);
        level.record(&[f32::NAN]);
        assert_eq!(level.take(), 0.5);
        // Read once: a decoder that is busy never replays an old peak.
        assert_eq!(level.take(), 0.0);
    }

    #[test]
    fn stop_tail_cancellation_wakes_the_wait_without_waiting_for_the_deadline() {
        let stop = CaptureStop::default();
        stop.request_stop(Instant::now());
        let (sender, cancellation) = crossbeam_channel::bounded(1);
        let deadline = stop.deadline().unwrap();
        std::thread::scope(|scope| {
            scope.spawn(move || {
                std::thread::sleep(Duration::from_millis(20));
                sender.send(()).unwrap();
            });
            assert!(!stop.wait_for_tail(&cancellation));
            assert!(Instant::now() < deadline);
        });
    }

    #[test]
    fn stop_tail_uses_the_original_deadline_when_the_worker_is_late() {
        let stop = CaptureStop::default();
        stop.request_stop(Instant::now());
        let (_, cancellation) = crossbeam_channel::bounded::<()>(1);
        assert!(
            !stop.wait_for_tail(&cancellation),
            "A disconnected service must stop capture"
        );
        let (sender, cancellation) = crossbeam_channel::bounded(1);
        let deadline = stop.deadline().unwrap();
        std::thread::sleep(deadline.saturating_duration_since(Instant::now()));
        assert!(stop.wait_for_tail(&cancellation));
        assert_eq!(stop.deadline(), Some(deadline));
        drop(sender);
    }

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
