//! CPU-only probe. No model, audio, GUI or server is required.
use anyhow::{Result, ensure};
use serde_json::json;
use std::{hint::black_box, time::Instant};
use voiceflow_core::cleanup::{CompiledCatalog, Example, Operation};
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
    for (mode, cleanup, label, rule_count, catalog_enabled) in [
        (FormatMode::Faithful, false, "faithful", 0, false),
        (
            FormatMode::SpokenCommands,
            true,
            "cleanup-and-commands",
            0,
            false,
        ),
        (
            FormatMode::SpokenCommands,
            true,
            "dictionary-1000",
            1000,
            false,
        ),
        (
            FormatMode::SpokenCommands,
            true,
            "dictionary-4096",
            4096,
            false,
        ),
        (FormatMode::SpokenCommands, true, "catalog-256", 0, true),
        (
            FormatMode::SpokenCommands,
            true,
            "combined-4096-256",
            4096,
            true,
        ),
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
        let catalog = if catalog_enabled {
            benchmark_catalog()?
        } else {
            CompiledCatalog::builtin()
        };
        let compile_ms = compile_start.elapsed().as_secs_f64() * 1000.0;
        let mut processor = TextProcessor::with_rules(mode, cleanup, dictionary, catalog);
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
                <= if rule_count == 0 && !catalog_enabled {
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
                "mode": label, "preview": preview, "rules": rule_count, "cleanupRules": if catalog_enabled { 256 } else { 17 }, "compileMs": compile_ms, "inputWords": words, "inputBytes": input.len(),
                "outputBytes": processor.parts().0.len(), "maxMutableWords": maximum,
                "maxRetainedWords": maximum_retained,
                "elapsedMs": elapsed.as_secs_f64() * 1000.0,
                "microsecondsPerInputWord": elapsed.as_secs_f64() * 1_000_000.0 / words as f64,
            })
        );
    }
    Ok(())
}

// Synthetic data only. The first 17 rules preserve the shipped catalog; one
// seven-word dependency and distinct-key candidates exercise the extended path.
fn benchmark_catalog() -> Result<CompiledCatalog> {
    let mut data = CompiledCatalog::builtin().definition().clone();
    data.version = "benchmark-only-256".into();
    let template = data.rules[0].clone();
    while data.rules.len() < 256 {
        let index = data.rules.len();
        let suffix = format!(
            "{}{}",
            char::from(b'a' + (index / 26) as u8),
            char::from(b'a' + (index % 26) as u8)
        );
        let source = if index == 17 {
            vec!["uno", "due", "tre", "quattro", "cinque", "sei", "sette"]
                .into_iter()
                .map(str::to_owned)
                .collect::<Vec<_>>()
        } else {
            vec![format!("unmatched{suffix}"), "restart".into()]
        };
        let mut rule = template.clone();
        rule.id = format!("fixture-{index}");
        rule.provenance.source = "synthetic benchmark, not personal evidence".into();
        rule.positive_examples = vec![Example {
            input: source.join(" "),
            output: source[1..].join(" "),
        }];
        rule.operation = Operation::RemoveFalseStart { source };
        data.rules.push(rule);
    }
    CompiledCatalog::compile(data, "it")
}
