use anyhow::{Result, ensure};
use rubato::{FftFixedIn, Resampler};
use std::collections::VecDeque;

pub const SAMPLE_RATE: usize = 16_000;

/// Retains only an incomplete resampling frame, never an entire recording.
pub struct StreamingResampler {
    resampler: Option<FftFixedIn<f32>>,
    pending: VecDeque<f32>,
    delay_remaining: usize,
    input_samples: usize,
    output_samples: usize,
    input_rate: usize,
}

impl StreamingResampler {
    pub fn new(input_rate: usize) -> Result<Self> {
        ensure!(
            (8_000..=192_000).contains(&input_rate),
            "Unsupported sample rate"
        );
        let resampler = if input_rate == SAMPLE_RATE {
            None
        } else {
            Some(FftFixedIn::new(input_rate, SAMPLE_RATE, 1024, 2, 1)?)
        };
        let delay_remaining = resampler.as_ref().map_or(0, Resampler::output_delay);
        Ok(Self {
            resampler,
            pending: VecDeque::new(),
            delay_remaining,
            input_samples: 0,
            output_samples: 0,
            input_rate,
        })
    }

    pub fn push(&mut self, samples: &[f32]) -> Result<Vec<f32>> {
        ensure!(
            samples.iter().all(|s| s.is_finite()),
            "Non-finite audio sample"
        );
        self.input_samples += samples.len();
        if self.resampler.is_none() {
            self.output_samples += samples.len();
            return Ok(samples.to_vec());
        }
        self.pending.extend(samples);
        let mut output = Vec::new();
        while self.pending.len() >= self.resampler.as_ref().unwrap().input_frames_next() {
            let count = self.resampler.as_ref().unwrap().input_frames_next();
            let input: Vec<f32> = self.pending.drain(..count).collect();
            let channels = self.resampler.as_mut().unwrap().process(&[input], None)?;
            self.append(&channels[0], &mut output);
        }
        Ok(output)
    }

    fn append(&mut self, samples: &[f32], output: &mut Vec<f32>) {
        let skip = self.delay_remaining.min(samples.len());
        self.delay_remaining -= skip;
        output.extend_from_slice(&samples[skip..]);
        self.output_samples += samples.len() - skip;
    }

    pub fn finish(&mut self) -> Result<Vec<f32>> {
        if self.resampler.is_none() {
            return Ok(Vec::new());
        }
        let target = self.input_samples * SAMPLE_RATE / self.input_rate;
        let remaining = target.saturating_sub(self.output_samples);
        let mut output = Vec::new();
        while output.len() < remaining {
            let count = self.resampler.as_ref().unwrap().input_frames_next();
            let mut input: Vec<f32> = self.pending.drain(..).collect();
            input.resize(count, 0.0);
            let channels = self.resampler.as_mut().unwrap().process(&[input], None)?;
            self.append(&channels[0], &mut output);
        }
        output.truncate(remaining);
        Ok(output)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn resampling_preserves_duration_across_arbitrary_callback_boundaries() {
        for rate in [16_000, 44_100, 48_000] {
            let samples: Vec<_> = (0..rate * 3)
                .map(|i| (i as f32 * 0.02).sin() * 0.3)
                .collect();
            let mut resampler = StreamingResampler::new(rate).unwrap();
            let mut output = Vec::new();
            for chunk in samples.chunks(317) {
                output.extend(resampler.push(chunk).unwrap());
            }
            output.extend(resampler.finish().unwrap());
            assert_eq!(output.len(), 48_000);
            assert!(resampler.pending.len() < 1024);
            assert!(output.iter().all(|v| v.is_finite()));
        }
    }
}
