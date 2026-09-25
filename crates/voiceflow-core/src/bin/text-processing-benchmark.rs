//! CPU-only probe. No model, audio, GUI or server is required.
use anyhow::{Result, ensure};
use serde_json::json;
use std::{hint::black_box, time::Instant};
use voiceflow_core::dictionary::{Context, Dictionary, MatchMode, Rule};
use voiceflow_core::formatting::{
    DICTIONARY_RETAINED_WORDS, FormatMode, MUTABLE_WORDS, RETAINED_WORDS, TextProcessor,
};

fn main() -> Result<()> {
    let words: usize = std::env::args()
        .nth(1)
        .unwrap_or_else(|| "100000".into())
        .parse()?;
    ensure!(words > 0, "Word count must be positive");
    let preview = std::env::args().nth(2).as_deref() == Some("preview");
    for (mode, cleanup, label, rule_count) in [
        (FormatMode::Faithful, false, "faithful", 0),
        (FormatMode::SpokenCommands, true, "cleanup-and-commands", 0),
        (FormatMode::SpokenCommands, true, "dictionary-1000", 1000),
        (FormatMode::SpokenCommands, true, "dictionary-4096", 4096),
    ] {
        let compile_start = Instant::now();
        let rules = (0..rule_count)
            .map(|index| Rule {
                id: index,
                variant: if index == 0 {
                    "scheda".into()
                } else if index == 1 {
                    "uno due tre quattro cinque sei sette".into()
                } else {
                    format!("unmatched{index}")
                },
                preferred: format!("Preferred{index}"),
                mode: MatchMode::Exact,
                context: Context::default(),
                scope: 0,
            })
            .collect();
        let dictionary = Dictionary::compile(rules)?;
        let compile_ms = compile_start.elapsed().as_secs_f64() * 1000.0;
        let mut processor = TextProcessor::with_dictionary(mode, cleanup, dictionary);
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
            if preview {
                black_box(processor.parts());
            }
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
            maximum_retained
                <= if rule_count == 0 {
                    RETAINED_WORDS
                } else {
                    DICTIONARY_RETAINED_WORDS
                },
            "Retained source words exceeded their bound"
        );
        ensure!(
            processor.retained_words() == 0,
            "Finalization left pending words"
        );
        println!(
            "{}",
            json!({
                "mode": label, "preview": preview, "rules": rule_count, "compileMs": compile_ms, "inputWords": words, "inputBytes": input.len(),
                "outputBytes": processor.parts().0.len(), "maxMutableWords": maximum,
                "maxRetainedWords": maximum_retained,
                "elapsedMs": elapsed.as_secs_f64() * 1000.0,
                "microsecondsPerInputWord": elapsed.as_secs_f64() * 1_000_000.0 / words as f64,
            })
        );
    }
    Ok(())
}
