//! CPU-only probe. No model, audio, GUI or server is required.
use anyhow::{Result, ensure};
use serde_json::json;
use std::{hint::black_box, time::Instant};
use voiceflow_core::formatting::{FormatMode, MUTABLE_WORDS, RETAINED_WORDS, TextProcessor};

fn main() -> Result<()> {
    let words: usize = std::env::args()
        .nth(1)
        .unwrap_or_else(|| "100000".into())
        .parse()?;
    ensure!(words > 0, "Word count must be positive");
    for (mode, cleanup, label) in [
        (FormatMode::Faithful, false, "faithful"),
        (FormatMode::SpokenCommands, true, "cleanup-and-commands"),
    ] {
        let mut processor = TextProcessor::new(mode, cleanup);
        let mut input = String::with_capacity(words * 8);
        let phrase = [
            "il",
            "la",
            "scheda",
            "della",
            "dell’acquisizione",
            "a",
            "capo",
            "molto",
            "molto",
            "importante",
        ];
        let mut maximum = 0;
        let mut maximum_retained = 0;
        let start = Instant::now();
        for i in 0..words {
            input.push_str(phrase[i % phrase.len()]);
            input.push(' ');
            processor.update(black_box(&input), false)?;
            maximum = maximum.max(processor.buffered_words());
            maximum_retained = maximum_retained.max(processor.retained_words());
        }
        processor.update(&input, true)?;
        let elapsed = start.elapsed();
        ensure!(
            maximum <= MUTABLE_WORDS,
            "Mutable window exceeded its bound"
        );
        ensure!(
            maximum_retained <= RETAINED_WORDS,
            "Retained source words exceeded their bound"
        );
        ensure!(
            processor.retained_words() == 0,
            "Finalization left pending words"
        );
        println!(
            "{}",
            json!({
                "mode": label, "inputWords": words, "inputBytes": input.len(),
                "outputBytes": processor.parts().0.len(), "maxMutableWords": maximum,
                "maxRetainedWords": maximum_retained,
                "elapsedMs": elapsed.as_secs_f64() * 1000.0,
                "microsecondsPerInputWord": elapsed.as_secs_f64() * 1_000_000.0 / words as f64,
            })
        );
    }
    Ok(())
}
