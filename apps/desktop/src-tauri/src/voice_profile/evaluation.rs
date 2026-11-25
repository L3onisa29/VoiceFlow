//! Replays the saved readings through a model and measures word error against the
//! sentences that were read. The report holds counts and rates only: no sentence,
//! hypothesis or file name is printed or logged.
use super::*;
use crate::dictionary::DictionarySnapshot;
use voiceflow_core::{
    audio::decode_wav,
    dictionary::Dictionary,
    evaluation::{Tally, normalize},
    formatting::{FormatMode, TextProcessor},
    streaming::{Language, NvidiaDecoder, StreamingDecoder, StreamingSession, load_model},
};

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Row {
    /// Language given to the recogniser: `auto` or `it-IT`.
    pub language: &'static str,
    /// Whether the dictionary's replacements were applied to the recognised text.
    pub dictionary: bool,
    /// `all`, `train` or `test`.
    pub split: &'static str,
    #[serde(flatten)]
    pub tally: Tally,
    pub word_error_rate: Option<f64>,
    pub term_error_rate: Option<f64>,
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Report {
    pub recordings: usize,
    pub audio_seconds: f64,
    /// Dictionary terms looked for in the references. Zero without a dictionary.
    pub terms: usize,
    pub rows: Vec<Row>,
}

/// `decoder` builds a fresh decoder per reading, as a live dictation does.
pub fn evaluate<D: StreamingDecoder>(
    store: &ProfileStore,
    dictionary: Option<&DictionarySnapshot>,
    mut decoder: impl FnMut(Language) -> Result<D>,
) -> Result<Report> {
    let (_, prompts) = store
        .script()?
        .context("Nessuna frase disponibile: manca voice-profile/prompts.json.")?;
    let recordings = store.recordings(&prompts);
    let terms: Vec<Vec<String>> = dictionary
        .into_iter()
        .flat_map(|dictionary| &dictionary.terms)
        .map(|term| normalize(&term.text))
        .filter(|words| !words.is_empty())
        .collect();
    let mut rows = vec![];
    for (language, code, scope) in [
        (Language::Auto, "auto", "auto"),
        (Language::Italian, "it-IT", "it"),
    ] {
        let rules = dictionary
            .map(|dictionary| Dictionary::compile(dictionary.rules(Some(scope))))
            .transpose()?;
        // [without dictionary, with dictionary] x [all, train, test]
        let mut tallies = vec![Tally::default(); 6];
        for prompt in &prompts {
            if !recordings.contains_key(&prompt.id) {
                continue;
            }
            let samples = decode_wav(&fs::read(store.wav(&prompt.id))?)?;
            let (plain, originals) =
                StreamingSession::new(decoder(language)?).transcribe(&samples)?;
            let mut hypotheses = vec![plain.text()];
            if let Some(rules) = &rules {
                // The same replacements a live dictation applies, on the same raw output.
                let mut processor =
                    TextProcessor::with_dictionary(FormatMode::Faithful, false, rules.clone());
                for original in &originals {
                    processor.update(original, true)?;
                }
                hypotheses.push(processor.parts().0.to_owned());
            }
            let split = if prompt.split == Split::Test { 2 } else { 1 };
            for (index, hypothesis) in hypotheses.iter().enumerate() {
                for column in [0, split] {
                    tallies[index * 3 + column].add(&prompt.text, hypothesis, &terms);
                }
            }
        }
        for (index, tally) in tallies.into_iter().enumerate() {
            if index >= 3 && rules.is_none() {
                continue;
            }
            rows.push(Row {
                language: code,
                dictionary: index >= 3,
                split: ["all", "train", "test"][index % 3],
                word_error_rate: tally.word_error_rate(),
                term_error_rate: tally.term_error_rate(),
                tally,
            });
        }
    }
    Ok(Report {
        recordings: recordings.len(),
        audio_seconds: recordings.values().map(|r| r.duration).sum(),
        terms: terms.len(),
        rows,
    })
}

/// `voice-profile-eval [--data DIR] [--model DIR]`. Reads only; prints the report.
pub fn entry() -> Result<()> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let value = |name: &str| {
        args.windows(2)
            .find(|pair| pair[0] == name)
            .map(|pair| pair[1].clone())
    };
    ensure!(
        args.iter()
            .all(|arg| !arg.starts_with("--") || arg == "--data" || arg == "--model"),
        "Usage: voice-profile-eval [--data DIR] [--model DIR]"
    );
    let data = match value("--data") {
        Some(path) => PathBuf::from(path),
        None => PathBuf::from(std::env::var_os("HOME").context("HOME non disponibile")?)
            .join("Library/Application Support/VoiceFlow"),
    };
    let model = value("--model")
        .map(PathBuf::from)
        .unwrap_or_else(|| data.join("models/nemotron-3.5-streaming-int8"));
    let dictionary = data.join("dictionary.sqlite3");
    // Opened read-only and never migrated: this command must not change the archive.
    let dictionary = if dictionary.is_file() {
        let connection = rusqlite::Connection::open_with_flags(
            &dictionary,
            rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY,
        )?;
        Some(crate::dictionary::read_schema_snapshot(
            &connection,
            "main",
        )?)
    } else {
        None
    };
    let handle = load_model(&model)?;
    let report = evaluate(
        &ProfileStore::new(data.join("voice-profile")),
        dictionary.as_ref(),
        |language| NvidiaDecoder::new(&handle, language),
    )?;
    println!("{}", serde_json::to_string_pretty(&report)?);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::super::tests::Fixture;
    use super::*;
    /// Recognises a reading by the level of its synthetic tone.
    struct ByLevel {
        heard: Vec<(f32, &'static str)>,
        text: String,
    }
    impl StreamingDecoder for ByLevel {
        fn chunk_samples(&self) -> usize {
            8_960
        }
        fn feed(&mut self, samples: &[f32]) -> Result<()> {
            let peak = samples.iter().fold(0.0f32, |peak, s| peak.max(s.abs()));
            if let Some((_, text)) = self
                .heard
                .iter()
                .find(|(level, _)| (level - peak).abs() < 0.02)
            {
                self.text = (*text).into();
            }
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
    fn report_separates_language_dictionary_and_split_and_carries_no_text() {
        let f = Fixture::new();
        f.script(&[
            ("p001", "Apri il bottom sheet adesso.", "train"),
            ("p002", "Carica la build su TestFlight.", "test"),
            ("p003", "Mai registrata.", "train"),
        ]);
        for (id, level) in [("p001", 0.2), ("p002", 0.4)] {
            f.store
                .save(
                    &Fixture::tone(1.5, level),
                    super::super::tests::take(id, ""),
                )
                .unwrap();
        }
        let dictionary: DictionarySnapshot = serde_json::from_value(serde_json::json!({
            "version":1,"groups":[],"selectedGroupIds":[],"conflicts":[],
            "terms":[
                {"id":1,"text":"bottom sheet","groupIds":[],"variants":["botton shit"],"language":"*","matching":"unicode-lowercase","context":{"before":null,"after":null,"exceptBefore":[],"exceptAfter":[]}},
                {"id":2,"text":"TestFlight","groupIds":[],"variants":["tes flight"],"language":"it","matching":"unicode-lowercase","context":{"before":null,"after":null,"exceptBefore":[],"exceptAfter":[]}}
            ]})).unwrap();
        let mut languages = vec![];
        let report = evaluate(&f.store, Some(&dictionary), |language| {
            languages.push(language);
            Ok(ByLevel {
                heard: vec![
                    (0.2, "apri il botton shit adesso"),
                    (0.4, "<unk> carica la build su tes flight"),
                ],
                text: String::new(),
            })
        })
        .unwrap();
        // One fresh decoder per reading and language.
        assert_eq!(languages.len(), 4);
        assert_eq!(
            (report.recordings, report.terms, report.audio_seconds),
            (2, 2, 3.0)
        );
        let row = |language: &str, dictionary: bool, split: &str| {
            let row = report
                .rows
                .iter()
                .find(|r| r.language == language && r.dictionary == dictionary && r.split == split)
                .unwrap();
            (
                row.tally.sentences,
                row.tally.words,
                row.tally.errors,
                row.tally.term_occurrences,
                row.tally.term_misses,
            )
        };
        assert_eq!(report.rows.len(), 12);
        // Raw model: both terms are wrong. "tes flight" is two words for one: two errors.
        assert_eq!(row("auto", false, "all"), (2, 10, 4, 2, 2));
        assert_eq!(row("auto", false, "train"), (1, 5, 2, 1, 1));
        assert_eq!(row("auto", false, "test"), (1, 5, 2, 1, 1));
        // The Italian-only variant also applies when the language is automatic.
        assert_eq!(row("auto", true, "all"), (2, 10, 0, 2, 0));
        assert_eq!(row("it-IT", true, "test"), (1, 5, 0, 1, 0));
        assert_eq!(row("it-IT", false, "all"), (2, 10, 4, 2, 2));
        let printed = serde_json::to_string(&report).unwrap();
        for private in ["bottom", "TestFlight", "botton", "p001", ".wav", "Apri"] {
            assert!(!printed.contains(private), "{private}");
        }
        // Without a dictionary only the raw rows exist and terms are not measured.
        let plain = evaluate(&f.store, None, |_| {
            Ok(ByLevel {
                heard: vec![(0.2, "apri il bottom sheet adesso")],
                text: String::new(),
            })
        })
        .unwrap();
        assert_eq!((plain.rows.len(), plain.terms), (6, 0));
        assert_eq!(plain.rows[0].term_error_rate, None);
        assert_eq!(plain.rows[1].word_error_rate, Some(0.0));
    }
}
