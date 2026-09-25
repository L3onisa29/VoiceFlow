use anyhow::{Context, Result, ensure};
use serde_json::json;
use std::{env, time::Instant};
use voiceflow_core::{
    audio::StreamingResampler,
    formatting::FormatMode,
    streaming::{Language, NvidiaDecoder, StreamingSession, load_model},
};

fn main() -> Result<()> {
    let args: Vec<_> = env::args().collect();
    ensure!(
        args.len() >= 3,
        "Usage: streaming-benchmark MODEL_DIRECTORY AUDIO.wav [it|en|auto] [--realtime] [--cleanup] [--spoken-commands]"
    );
    let language = match args.get(3).map(String::as_str).unwrap_or("auto") {
        "it" => Language::Italian,
        "en" => Language::English,
        "auto" => Language::Auto,
        _ => anyhow::bail!("Language must be it, en or auto"),
    };
    let realtime = args.iter().any(|a| a == "--realtime");
    let cleanup = args.iter().any(|a| a == "--cleanup");
    let mode = if args.iter().any(|a| a == "--spoken-commands") {
        FormatMode::SpokenCommands
    } else {
        FormatMode::Faithful
    };
    let started = Instant::now();
    let model = load_model(std::path::Path::new(&args[1]))?;
    let load_seconds = started.elapsed().as_secs_f64();
    let mut wav = hound::WavReader::open(&args[2]).context("Cannot read WAV")?;
    let spec = wav.spec();
    ensure!(
        spec.channels == 1
            && spec.sample_format == hound::SampleFormat::Int
            && spec.bits_per_sample == 16,
        "Benchmark input must be mono PCM16 WAV"
    );
    let mut resampler = StreamingResampler::new(spec.sample_rate as usize)?;
    let mut session =
        StreamingSession::with_processing(NvidiaDecoder::new(&model, language)?, mode, cleanup);
    let mut first_partial = None;
    let mut first_partial_wall = None;
    let mut last_text = String::new();
    let mut frames = Vec::with_capacity(320);
    let mut chunk_ms = Vec::new();
    let mut count = 0usize;
    let started = Instant::now();
    for sample in wav.samples::<i16>() {
        frames.push(sample? as f32 / 32768.0);
        count += 1;
        if frames.len() < 320 {
            continue;
        }
        if realtime {
            let due = std::time::Duration::from_secs_f64(count as f64 / spec.sample_rate as f64);
            if let Some(delay) = due.checked_sub(started.elapsed()) {
                std::thread::sleep(delay);
            }
        }
        let begin = Instant::now();
        let before = session.snapshot().chunks;
        session.push(&resampler.push(&frames)?)?;
        if session.snapshot().chunks > before {
            chunk_ms.push(begin.elapsed().as_secs_f64() * 1000.0);
        }
        frames.clear();
        let snapshot = session.snapshot();
        let text = snapshot.text();
        if !text.is_empty() && text != last_text {
            first_partial.get_or_insert(snapshot.audio_seconds);
            first_partial_wall.get_or_insert(started.elapsed().as_secs_f64());
            // Text is written only by this explicit benchmark, never application logs.
            println!(
                "{}",
                json!({"event":"partial", "audioSeconds":snapshot.audio_seconds, "text":text})
            );
            last_text = text;
        }
    }
    session.push(&resampler.push(&frames)?)?;
    session.push(&resampler.finish()?)?;
    let stop = Instant::now();
    let result = session.finish()?;
    chunk_ms.sort_by(f64::total_cmp);
    let p95 = chunk_ms
        .get((chunk_ms.len() as f64 * 0.95).floor() as usize)
        .copied()
        .unwrap_or_default();
    let output = json!({"event":"completed", "model":"nvidia/nemotron-3.5-asr-streaming-0.6b",
        "audioSeconds":result.audio_seconds, "loadSeconds":load_seconds, "wallSeconds":started.elapsed().as_secs_f64(),
        "inferenceSeconds":result.inference_seconds, "realTimeFactor":result.inference_seconds/result.audio_seconds.max(0.001),
        "firstPartialAudioSeconds":first_partial, "firstPartialWallSeconds":first_partial_wall,
        "chunkP95Ms":p95, "finalizeMs":stop.elapsed().as_secs_f64()*1000.0,
        "chunks":result.chunks, "text":result.text(), "paced":realtime, "cleanup":cleanup, "format":mode});
    println!("{output}");
    ensure!(
        first_partial.is_some() && !result.text().is_empty(),
        "No speech recognized"
    );
    Ok(())
}
