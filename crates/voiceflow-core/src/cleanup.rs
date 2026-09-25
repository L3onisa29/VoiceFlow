//! Bounded declarative cleanup. Compilation is the untrusted-data boundary.
//! A compiled catalog is immutable; publication/approval belongs to the caller.
use anyhow::{Result, ensure};
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};

pub const BUILTIN_VERSION: &str = "cleanup-catalog-v1:builtin-1";
pub const MAX_RULES: usize = 256;
pub const MAX_WORDS: usize = 7;
const REPEATS: &[&str] = &[
    "il", "lo", "la", "gli", "le", "un", "una", "di", "del", "della", "dello", "dei", "degli",
    "delle",
];

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Catalog {
    pub schema_version: u32,
    pub version: String,
    pub rules: Vec<Rule>,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Rule {
    pub id: String,
    pub version: u32,
    /// Primary language code, or `*`. Builtins use `*` to preserve legacy behavior.
    pub language: String,
    pub operation: Operation,
    pub context: Context,
    pub provenance: Provenance,
    pub positive_examples: Vec<Example>,
    pub negative_examples: Vec<Example>,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "kind", rename_all = "kebab-case", deny_unknown_fields)]
pub enum Operation {
    /// Delete only the first source word, retaining every following original word.
    /// Two to seven explicit words; no captures, replacement text or wildcards.
    RemoveFalseStart { source: Vec<String> },
    /// Only the established Italian function-word whitelist is supported.
    Repeat { word: String },
    /// Only the established della / dell' and della / dell’ family is supported.
    Elision { first: String, prefix: String },
}
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Context {
    pub before: Option<String>,
    pub after: Option<String>,
    pub except_before: Vec<String>,
    pub except_after: Vec<String>,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Provenance {
    /// Descriptive origin, not proof of approval. C2 owns approval and evidence IDs.
    pub source: String,
    pub evidence_ids: Vec<String>,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Example {
    pub input: String,
    pub output: String,
}
#[derive(Debug, Clone)]
pub struct CompiledCatalog {
    catalog: Catalog,
    language: String,
    by_first: HashMap<String, Vec<usize>>,
    lookahead: usize,
    legacy_pairs: bool,
}

fn bounded_text(text: &str, max: usize) -> bool {
    !text.trim().is_empty() && text.len() <= max && !text.chars().any(char::is_control)
}
fn literal_word(word: &str) -> bool {
    bounded_text(word, 100)
        && word
            .chars()
            .all(|c| c.is_lowercase() || c == '\'' || c == '’')
        && word.chars().any(char::is_lowercase)
}
fn safe_deleted_word(word: &str) -> bool {
    literal_word(word)
        && ![
            "non", "no", "not", "never", "neither", "nor", "mai", "senza", "né", "nessuno",
            "niente", "nulla",
        ]
        .contains(&word)
}
fn terminal(word: &str) -> &str {
    word.trim_end_matches([',', '.', ':', ';', '!', '?'])
}
impl Operation {
    fn first(&self) -> &str {
        match self {
            Self::RemoveFalseStart { source } => &source[0],
            Self::Repeat { word } => word,
            Self::Elision { first, .. } => first,
        }
    }
    fn words(&self) -> usize {
        match self {
            Self::RemoveFalseStart { source } => source.len(),
            _ => 2,
        }
    }
    fn matches(&self, words: &[(&str, &str)]) -> bool {
        if words.len() < self.words() || words[0].1 != self.first() {
            return false;
        }
        if words
            .iter()
            .take(self.words())
            .skip(1)
            .any(|w| w.0.contains(['\n', '\r']))
        {
            return false;
        }
        match self {
            Self::RemoveFalseStart { source } => source.iter().enumerate().all(|(i, expected)| {
                let actual = words[i].1;
                (if i + 1 == source.len() {
                    terminal(actual)
                } else {
                    actual
                }) == expected
            }),
            Self::Repeat { word } => terminal(words[1].1) == word,
            Self::Elision { prefix, .. } => terminal(words[1].1)
                .strip_prefix(prefix)
                .is_some_and(|tail| !tail.is_empty() && tail.chars().all(char::is_lowercase)),
        }
    }
}
impl Rule {
    fn matches(&self, words: &[(&str, &str)], before: Option<&str>) -> bool {
        if !self.operation.matches(words) {
            return false;
        }
        let count = self.operation.words();
        let before = before.filter(|_| !words[0].0.contains(['\n', '\r']));
        let after = words
            .get(count)
            .filter(|_| terminal(words[count - 1].1) == words[count - 1].1)
            .filter(|w| !w.0.contains(['\n', '\r']))
            .map(|w| w.1);
        let ctx = &self.context;
        !ctx.before
            .as_ref()
            .is_some_and(|w| before != Some(w.as_str()))
            && !ctx
                .after
                .as_ref()
                .is_some_and(|w| after != Some(w.as_str()))
            && !ctx.except_before.iter().any(|w| before == Some(w.as_str()))
            && !ctx.except_after.iter().any(|w| after == Some(w.as_str()))
    }
}

impl CompiledCatalog {
    pub fn compile(catalog: Catalog, language: &str) -> Result<Self> {
        ensure!(
            catalog.schema_version == 1,
            "Unsupported cleanup schema version"
        );
        ensure!(
            bounded_text(&catalog.version, 128),
            "Invalid catalog version"
        );
        ensure!(
            catalog.rules.len() <= MAX_RULES,
            "At most 256 cleanup rules are supported"
        );
        let language = language
            .split('-')
            .next()
            .unwrap_or(language)
            .to_lowercase();
        ensure!(
            language == "auto" || valid_language(&language),
            "Invalid session language"
        );
        let mut ids = HashSet::new();
        let mut shapes = HashSet::new();
        for rule in &catalog.rules {
            ensure!(
                bounded_text(&rule.id, 128) && ids.insert(&rule.id),
                "Invalid or duplicate cleanup rule ID"
            );
            ensure!(rule.version > 0, "Rule version must be positive");
            ensure!(
                rule.language == "*" || valid_language(&rule.language),
                "Invalid rule language"
            );
            match &rule.operation {
                Operation::RemoveFalseStart { source } => {
                    ensure!(
                        (2..=MAX_WORDS).contains(&source.len()),
                        "False starts require 2 to 7 source words"
                    );
                    ensure!(
                        source.iter().all(|w| literal_word(w)) && safe_deleted_word(&source[0]),
                        "Only lowercase literal words and safe deletion are supported"
                    );
                    ensure!(
                        source[0] != source[1],
                        "Repeated words require the supported repeat operation"
                    );
                }
                Operation::Repeat { word } => {
                    ensure!(REPEATS.contains(&word.as_str()), "Unsupported repetition")
                }
                Operation::Elision { first, prefix } => ensure!(
                    first == "della" && ["dell'", "dell’"].contains(&prefix.as_str()),
                    "Unsupported elision family"
                ),
            }
            let ctx = &rule.context;
            ensure!(
                ctx.except_before.len() <= 16 && ctx.except_after.len() <= 16,
                "Too many cleanup exceptions"
            );
            for w in ctx
                .before
                .iter()
                .chain(ctx.after.iter())
                .chain(ctx.except_before.iter())
                .chain(ctx.except_after.iter())
            {
                ensure!(
                    literal_word(w),
                    "Conditions require one lowercase literal word"
                );
            }
            ensure!(
                !ctx.before
                    .as_ref()
                    .is_some_and(|w| ctx.except_before.contains(w))
                    && !ctx
                        .after
                        .as_ref()
                        .is_some_and(|w| ctx.except_after.contains(w)),
                "Contradictory cleanup condition"
            );
            ensure!(
                rule.operation.words()
                    + usize::from(ctx.after.is_some() || !ctx.except_after.is_empty())
                    <= MAX_WORDS,
                "Cleanup dependency exceeds seven words"
            );
            ensure!(
                bounded_text(&rule.provenance.source, 256)
                    && rule.provenance.evidence_ids.len() <= 32
                    && rule
                        .provenance
                        .evidence_ids
                        .iter()
                        .all(|id| bounded_text(id, 128)),
                "Invalid cleanup provenance"
            );
            // All supported operations delete exactly the first source token, so
            // overlapping matches cannot disagree about output. Reject duplicates.
            let shape = serde_json::to_string(&(&rule.language, &rule.operation, &rule.context))?;
            ensure!(
                shapes.insert(shape),
                "Duplicate cleanup operation and scope"
            );
            for (examples, positive) in [
                (&rule.positive_examples, true),
                (&rule.negative_examples, false),
            ] {
                ensure!(
                    !examples.is_empty() && examples.len() <= 16,
                    "Require 1 to 16 positive and negative examples"
                );
                for example in examples {
                    ensure!(
                        bounded_text(&example.input, 2048)
                            && bounded_text(&example.output, 2048)
                            && example.input.trim() == example.input
                            && example.output.trim() == example.output
                            && !example.input.contains(['<', '>'])
                            && !example.output.contains(['<', '>']),
                        "Invalid cleanup example"
                    );
                    let output = example_output(rule, &example.input);
                    ensure!(
                        output == example.output && (output != example.input) == positive,
                        "Cleanup example does not agree with its rule"
                    );
                }
            }
        }
        let mut result = Self {
            catalog,
            language,
            by_first: HashMap::new(),
            lookahead: 0,
            legacy_pairs: false,
        };
        for (index, rule) in result.catalog.rules.iter().enumerate() {
            if rule.language != "*" && rule.language != result.language {
                continue;
            }
            result.lookahead = result.lookahead.max(
                rule.operation.words()
                    + usize::from(
                        rule.context.after.is_some() || !rule.context.except_after.is_empty(),
                    ),
            );
            result
                .by_first
                .entry(rule.operation.first().to_owned())
                .or_default()
                .push(index);
        }
        Ok(result)
    }
    pub fn builtin() -> Self {
        static BUILTIN: std::sync::OnceLock<CompiledCatalog> = std::sync::OnceLock::new();
        BUILTIN
            .get_or_init(|| {
                let data: Catalog = serde_json::from_str(include_str!("cleanup-builtin.json"))
                    .expect("builtin schema");
                assert_eq!(data.version, BUILTIN_VERSION);
                let mut compiled = Self::compile(data, "auto").expect("valid builtin rules");
                compiled.legacy_pairs = true;
                compiled
            })
            .clone()
    }
    pub fn definition(&self) -> &Catalog {
        &self.catalog
    }
    pub fn version(&self) -> &str {
        &self.catalog.version
    }
    pub fn language(&self) -> &str {
        &self.language
    }
    pub fn is_empty(&self) -> bool {
        self.by_first.is_empty()
    }
    pub(crate) fn legacy_pairs(&self) -> bool {
        self.legacy_pairs
    }
    pub fn required_frozen_words(&self) -> usize {
        (self.lookahead + 1).max(2)
    }
    /// Returns the chosen rule's identity. Every match deletes one source word.
    /// Commands protect the entire dependency, including retained source words.
    pub fn matching_rule<'a>(
        &'a self,
        words: &[(&str, &str)],
        before: Option<&str>,
        protected: &[bool],
    ) -> Option<&'a Rule> {
        let candidates = self.by_first.get(words.first()?.1)?;
        candidates
            .iter()
            .map(|&i| &self.catalog.rules[i])
            .filter(|rule| {
                let count = rule.operation.words();
                protected
                    .get(..count)
                    .is_some_and(|p| !p.iter().any(|v| *v))
                    && rule.matches(words, before)
            })
            .max_by_key(|rule| {
                (
                    rule.operation.words(),
                    usize::from(rule.context.before.is_some())
                        + usize::from(rule.context.after.is_some()),
                    rule.language != "*",
                    std::cmp::Reverse(&rule.id),
                )
            })
    }
    pub(crate) fn pair(&self, first: &str, second: &str, separator: &str) -> bool {
        self.matching_rule(&[("", first), (separator, second)], None, &[false, false])
            .is_some()
    }
}
fn valid_language(language: &str) -> bool {
    (2..=3).contains(&language.len()) && language.bytes().all(|c| c.is_ascii_lowercase())
}
// Validate examples using source positions, never the rewritten output. Examples
// are per-rule semantic checks, not promises about cross-rule or command priority.
fn example_output(rule: &Rule, input: &str) -> String {
    let mut words = Vec::new();
    let mut offset = 0;
    while offset < input.len() {
        let rest = &input[offset..];
        let Some(start) = rest.find(|c: char| !c.is_whitespace()) else {
            break;
        };
        let end = rest[start..]
            .find(char::is_whitespace)
            .map_or(rest.len(), |n| start + n);
        words.push((&rest[..start], &rest[start..end]));
        offset += end;
    }
    let mut output = String::new();
    let mut separator = None;
    for i in 0..words.len() {
        let sep = separator.take().unwrap_or(words[i].0);
        if rule.matches(&words[i..], i.checked_sub(1).map(|j| words[j].1)) {
            separator = Some(sep);
        } else {
            output.push_str(sep);
            output.push_str(words[i].1);
        }
    }
    output.push_str(&input[offset..]);
    output
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        dictionary::{Dictionary, MatchMode, Rule as Term},
        formatting::{DICTIONARY_RETAINED_WORDS, FormatMode, TextProcessor},
    };

    fn rule(source: &[&str]) -> Rule {
        let input = source.join(" ");
        Rule {
            id: "prepared-fixture".into(),
            version: 1,
            language: "it".into(),
            operation: Operation::RemoveFalseStart {
                source: source.iter().map(|w| (*w).into()).collect(),
            },
            context: Context::default(),
            provenance: Provenance {
                source: "synthetic C1 fixture, not personal evidence".into(),
                evidence_ids: vec!["fixture-1".into()],
            },
            positive_examples: vec![Example {
                input,
                output: source[1..].join(" "),
            }],
            negative_examples: vec![Example {
                input: "non molto molto importante 42 Anna".into(),
                output: "non molto molto importante 42 Anna".into(),
            }],
        }
    }
    fn catalog(rules: Vec<Rule>) -> Catalog {
        Catalog {
            schema_version: 1,
            version: "fixture-1".into(),
            rules,
        }
    }
    fn compile(rules: Vec<Rule>) -> CompiledCatalog {
        CompiledCatalog::compile(catalog(rules), "it-IT").unwrap()
    }
    fn term(source: &str, output: &str) -> Term {
        Term {
            id: 1,
            variant: source.into(),
            preferred: output.into(),
            mode: MatchMode::Exact,
            context: Default::default(),
            scope: 0,
        }
    }
    fn processor(rules: Vec<Rule>, terms: Vec<Term>, cleanup: bool) -> TextProcessor {
        TextProcessor::with_rules(
            FormatMode::SpokenCommands,
            cleanup,
            Dictionary::compile(terms).unwrap(),
            compile(rules),
        )
    }
    fn process(rules: Vec<Rule>, terms: Vec<Term>, cleanup: bool, input: &str) -> String {
        let mut p = processor(rules, terms, cleanup);
        p.update(input, true).unwrap();
        p.parts().0.into()
    }
    #[test]
    fn declared_examples_also_execute_the_actual_processor() {
        let mut rules = CompiledCatalog::builtin().definition().rules.clone();
        rules.push(rule(&["vorrei", "volevo", "chiedere"]));
        for rule in rules {
            for example in rule.positive_examples.iter().chain(&rule.negative_examples) {
                let mut p = TextProcessor::with_rules(
                    FormatMode::Faithful,
                    true,
                    Dictionary::default(),
                    compile(vec![rule.clone()]),
                );
                p.update(&example.input, true).unwrap();
                assert_eq!(p.parts().0, example.output, "rule {}", rule.id);
            }
        }
    }
    #[test]
    fn serialized_new_variant_operates_on_real_processor_and_is_opt_in() {
        let data = catalog(vec![rule(&["vorrei", "volevo", "chiedere"])]);
        let json = serde_json::to_string(&data).unwrap();
        let roundtrip: Catalog = serde_json::from_str(&json).unwrap();
        assert_eq!(data, roundtrip);
        let compiled = CompiledCatalog::compile(roundtrip, "it").unwrap();
        assert_eq!(compiled.version(), "fixture-1");
        assert_eq!(
            compiled.definition().rules[0].provenance.evidence_ids,
            ["fixture-1"]
        );
        assert_eq!(
            process(
                data.rules.clone(),
                vec![],
                true,
                "vorrei volevo chiedere, qualcosa"
            ),
            "volevo chiedere, qualcosa"
        );
        assert_eq!(
            process(
                data.rules,
                vec![],
                false,
                "vorrei  volevo chiedere, qualcosa"
            ),
            "vorrei  volevo chiedere, qualcosa"
        );
        let mut builtin = TextProcessor::new(FormatMode::Faithful, true);
        builtin.update("vorrei volevo chiedere", true).unwrap();
        assert_eq!(builtin.parts().0, "vorrei volevo chiedere");
    }
    #[test]
    fn conditions_exceptions_language_and_pauses_are_source_boundaries() {
        let mut r = rule(&["vorrei", "volevo"]);
        r.context.before = Some("oggi".into());
        r.context.after = Some("chiedere".into());
        r.context.except_before = vec!["ieri".into()];
        r.positive_examples = vec![Example {
            input: "oggi vorrei volevo chiedere".into(),
            output: "oggi volevo chiedere".into(),
        }];
        let input = "oggi vorrei volevo chiedere ieri vorrei volevo chiedere oggi vorrei volevo sapere oggi\nvorrei volevo chiedere";
        assert_eq!(
            process(vec![r.clone()], vec![term("oggi", "OGGI")], true, input),
            "OGGI volevo chiedere ieri vorrei volevo chiedere OGGI vorrei volevo sapere OGGI\nvorrei volevo chiedere"
        );
        let mut p = processor(vec![r.clone()], vec![], true);
        p.update("oggi", true).unwrap();
        p.update("vorrei volevo chiedere", true).unwrap();
        assert_eq!(p.parts().0, "oggi vorrei volevo chiedere");
        for language in ["en-US", "auto"] {
            let compiled = CompiledCatalog::compile(catalog(vec![r.clone()]), language).unwrap();
            assert!(compiled.is_empty());
            let mut p = TextProcessor::with_rules(
                FormatMode::Faithful,
                true,
                Dictionary::default(),
                compiled,
            );
            p.update("oggi vorrei volevo chiedere", true).unwrap();
            assert_eq!(p.parts().0, "oggi vorrei volevo chiedere");
        }
        let mut except = rule(&["vorrei", "volevo"]);
        except.context.except_after = vec!["sapere".into()];
        assert_eq!(
            process(
                vec![except],
                vec![],
                true,
                "vorrei volevo sapere vorrei volevo chiedere"
            ),
            "vorrei volevo sapere volevo chiedere"
        );
    }
    #[test]
    fn commands_then_dictionary_then_cleanup_never_reprocess_output() {
        let mut other = rule(&["alpha", "beta"]);
        other.id = "second".into();
        let rules = vec![rule(&["vorrei", "volevo"]), other];
        assert_eq!(
            process(
                rules.clone(),
                vec![
                    term("vorrei", "alpha"),
                    term("beta", "vorrei volevo"),
                    term("volevo", "VoLeVo")
                ],
                true,
                "vorrei volevo alpha beta"
            ),
            "alpha VoLeVo vorrei volevo"
        );
        assert_eq!(
            process(
                rules.clone(),
                vec![
                    term("vorrei volevo", "First"),
                    term("vorrei volevo", "Second")
                ],
                true,
                "vorrei volevo"
            ),
            "vorrei volevo"
        );
        // The adjacent survivor has its original before-context after deletion.
        let mut next = rule(&["volevo", "chiedere"]);
        next.id = "next".into();
        next.context.before = Some("vorrei".into());
        next.positive_examples = vec![Example {
            input: "vorrei volevo chiedere".into(),
            output: "vorrei chiedere".into(),
        }];
        assert_eq!(
            process(
                vec![rules[0].clone(), next],
                vec![],
                true,
                "vorrei volevo chiedere"
            ),
            "chiedere"
        );
        // Every deletion matched its own original position. No new adjacency is matched.
        let mut skip = rule(&["prima", "volevo"]);
        skip.id = "skip".into();
        assert_eq!(
            process(
                vec![rules[0].clone(), skip],
                vec![],
                true,
                "prima vorrei volevo"
            ),
            "prima volevo"
        );
        for source in [
            vec!["vorrei", "new"],
            vec!["vorrei", "new", "line"],
            vec!["new", "line"],
        ] {
            assert_eq!(
                process(vec![rule(&source)], vec![], true, "vorrei new line fine"),
                "vorrei\nfine"
            );
        }
    }
    #[test]
    fn deletion_keeps_original_separators_for_adjacent_context() {
        let first = rule(&["alpha", "beta"]);
        for exception in [false, true] {
            let mut second = rule(&["beta", "gamma"]);
            second.id = "adjacent".into();
            if exception {
                second.context.except_before = vec!["alpha".into()];
                second.negative_examples = vec![Example {
                    input: "alpha beta gamma".into(),
                    output: "alpha beta gamma".into(),
                }];
            } else {
                second.context.before = Some("alpha".into());
                second.positive_examples = vec![Example {
                    input: "alpha beta gamma".into(),
                    output: "alpha gamma".into(),
                }];
            }
            for separator in [" ", "\n", "\r\n", "\t", "  "] {
                for long in [false, true] {
                    let suffix = if long {
                        " tail".repeat(24)
                    } else {
                        String::new()
                    };
                    let input = format!("è{separator}alpha beta gamma{suffix} ");
                    let survivor = if exception { "beta gamma" } else { "gamma" };
                    let expected = format!("è{separator}{survivor}{suffix}");
                    for fragmented in [false, true] {
                        let mut p = processor(vec![first.clone(), second.clone()], vec![], true);
                        if fragmented {
                            for (index, ch) in input.char_indices() {
                                p.update(&input[..index + ch.len_utf8()], false).unwrap();
                                assert!(p.buffered_words() <= 7);
                                assert!(p.retained_words() <= DICTIONARY_RETAINED_WORDS);
                            }
                        } else {
                            p.update(&input, false).unwrap();
                        }
                        let (confirmed, preview) = p.parts();
                        assert_eq!(
                            format!("{confirmed}{preview}"),
                            expected,
                            "preview {input:?}"
                        );
                        if long {
                            assert!(confirmed.contains(survivor), "deletion must be committed");
                        }
                        p.update(&input, true).unwrap();
                        assert_eq!(p.parts().0, expected, "complete {input:?}");
                        p.update("alpha beta gamma", true).unwrap();
                        assert_eq!(p.parts().0, format!("{expected} {survivor}"));
                    }
                }
            }
        }
    }
    #[test]
    fn right_context_does_not_cross_terminal_punctuation() {
        let mut required = rule(&["vorrei", "volevo"]);
        required.context.after = Some("chiedere".into());
        required.positive_examples = vec![Example {
            input: "vorrei volevo chiedere".into(),
            output: "volevo chiedere".into(),
        }];
        let mut exception = rule(&["vorrei", "volevo"]);
        exception.context.except_after = vec!["chiedere".into()];
        exception.negative_examples = vec![Example {
            input: "vorrei volevo chiedere".into(),
            output: "vorrei volevo chiedere".into(),
        }];
        for punctuation in [",", ".", ":", ";", "!", "?", "!?"] {
            let input = format!("vorrei volevo{punctuation} chiedere");
            let cleaned = format!("volevo{punctuation} chiedere");
            for (r, expected) in [
                (required.clone(), input.clone()),
                (exception.clone(), cleaned.clone()),
                (rule(&["vorrei", "volevo"]), cleaned),
            ] {
                let mut p = processor(vec![r], vec![], true);
                let snapshot = format!("{input} ");
                for (index, ch) in snapshot.char_indices() {
                    p.update(&snapshot[..index + ch.len_utf8()], false).unwrap();
                }
                let (confirmed, preview) = p.parts();
                assert_eq!(format!("{confirmed}{preview}"), expected);
                p.update(&snapshot, true).unwrap();
                assert_eq!(p.parts().0, expected);
            }
        }
        // The unpunctuated context still matches in both directions.
        assert_eq!(
            process(vec![required], vec![], true, "vorrei volevo chiedere"),
            "volevo chiedere"
        );
        assert_eq!(
            process(vec![exception], vec![], true, "vorrei volevo chiedere"),
            "vorrei volevo chiedere"
        );
    }
    #[test]
    fn longest_dependency_revisions_and_command_overlap_wait_for_frozen_source() {
        let source = [
            "vorrei", "volevo", "chiedere", "come", "fare", "questa", "cosa",
        ];
        for prefix in 0..18 {
            let lead = "pad ".repeat(prefix);
            let original = format!("{lead}{} tail tail tail tail tail tail ", source.join(" "));
            let mut p = processor(vec![rule(&source)], vec![], true);
            p.update(&original, false).unwrap();
            assert!(!p.parts().0.contains("volevo"));
            let revised = original.replacen("cosa", "domanda", 1);
            p.update(&revised, false).unwrap();
            assert_eq!(
                format!("{}{}", p.parts().0, p.parts().1),
                revised.trim_end()
            );
            assert!(p.retained_words() <= DICTIONARY_RETAINED_WORDS);
            p.update(&revised, true).unwrap();
            assert_eq!(p.parts().0, revised.trim_end());
        }
        let mut p = processor(vec![rule(&["vorrei", "new"])], vec![], true);
        p.update("vorrei new tail tail tail tail tail tail tail ", false)
            .unwrap();
        p.update("vorrei new line tail tail tail tail tail tail ", true)
            .unwrap();
        assert_eq!(p.parts().0, "vorrei\ntail tail tail tail tail tail");
    }
    #[test]
    fn custom_catalog_fragmentation_and_combined_controls_have_batch_parity() {
        let mut rules = CompiledCatalog::builtin().definition().rules.clone();
        rules.push(rule(&["vorrei", "volevo", "chiedere"]));
        let input = "inizio il il la scheda vorrei volevo chiedere, voice flow nuovo paragrafo della dell’acquisizione non 123 molto molto importante Anna anna che che the The la dispositivo quel ragionamento di prima non va bene <unk> fine";
        let terms = vec![term("voice flow", "VoiceFlow")];
        let expected = "inizio la scheda volevo chiedere, VoiceFlow\n\ndell’acquisizione non 123 molto molto importante Anna anna che che the The la dispositivo quel ragionamento di prima non va bene fine";
        assert_eq!(process(rules.clone(), terms.clone(), true, input), expected);
        let mut p = processor(rules.clone(), terms.clone(), true);
        for end in 1..=input.len() {
            if input.is_char_boundary(end) {
                p.update(&input[..end], false).unwrap();
                p.update(&input[..end], false).unwrap();
                assert!(p.buffered_words() <= 7);
                assert!(p.retained_words() <= DICTIONARY_RETAINED_WORDS);
            }
        }
        p.update(input, true).unwrap();
        assert_eq!(p.parts().0, expected);
        assert_eq!(p.retained_words(), 0);
        assert_eq!(
            process(rules, terms, false, input),
            input
                .replace("voice flow", "VoiceFlow")
                .replace("nuovo paragrafo ", "\n\n")
                .replace(" <unk>", "")
                .replace("VoiceFlow \n", "VoiceFlow\n")
        );
    }
    #[test]
    fn unsupported_ambiguous_unbounded_and_false_examples_are_rejected() {
        let base = catalog(vec![rule(&["vorrei", "volevo"])]);
        let reject = |data| assert!(CompiledCatalog::compile(data, "it").is_err());
        let mut bad = base.clone();
        bad.schema_version = 2;
        reject(bad);
        let mut bad = base.clone();
        bad.rules[0].version = 0;
        reject(bad);
        let mut bad = base.clone();
        bad.rules.push(bad.rules[0].clone());
        reject(bad);
        let mut bad = base.clone();
        let mut duplicate = bad.rules[0].clone();
        duplicate.id = "duplicate".into();
        bad.rules.push(duplicate);
        reject(bad);
        for source in [
            vec!["no", "yes"],
            vec!["Anna", "anna"],
            vec!["42", "quarantadue"],
            vec!["molto", "molto"],
            vec!["vorrei"],
            vec![
                "uno", "due", "tre", "quattro", "cinque", "sei", "sette", "otto",
            ],
            vec![".*", "qualsiasi"],
        ] {
            let mut bad = base.clone();
            bad.rules[0].operation = Operation::RemoveFalseStart {
                source: source.iter().map(|s| (*s).into()).collect(),
            };
            reject(bad);
        }
        let mut bad = base.clone();
        bad.rules[0].operation = Operation::Repeat {
            word: "molto".into(),
        };
        reject(bad);
        let mut bad = base.clone();
        bad.rules[0].operation = Operation::Elision {
            first: "abc".into(),
            prefix: "".into(),
        };
        reject(bad);
        let mut bad = base.clone();
        bad.rules[0].positive_examples.clear();
        reject(bad);
        let mut bad = base.clone();
        bad.rules[0].positive_examples[0].output = "invented".into();
        reject(bad);
        let mut bad = base.clone();
        bad.rules[0].negative_examples = bad.rules[0].positive_examples.clone();
        reject(bad);
        let mut bad = base.clone();
        bad.rules[0].context.before = Some("oggi".into());
        bad.rules[0].context.except_before.push("oggi".into());
        reject(bad);
        let mut bad = base.clone();
        bad.rules[0].context.except_after = vec!["x".into(); 17];
        reject(bad);
        let mut bad = catalog(vec![rule(&[
            "vorrei", "volevo", "chiedere", "come", "fare", "questa", "cosa",
        ])]);
        bad.rules[0].context.except_after.push("domani".into());
        reject(bad);
        let mut bad = base.clone();
        bad.rules = vec![base.rules[0].clone(); MAX_RULES + 1];
        reject(bad);
        for json in [
            r#"{"kind":"regex","pattern":".*"}"#,
            r#"{"kind":"repeat","word":"il","code":"run()"}"#,
        ] {
            assert!(serde_json::from_str::<Operation>(json).is_err());
        }
    }
    #[test]
    fn overlapping_rules_have_stable_identity_and_identical_deletion() {
        let a = rule(&["vorrei", "volevo"]);
        let mut b = rule(&["vorrei", "volevo", "chiedere"]);
        b.id = "longer".into();
        let words = [("", "vorrei"), (" ", "volevo"), (" ", "chiedere")];
        for rules in [vec![a.clone(), b.clone()], vec![b, a]] {
            let compiled = compile(rules);
            assert_eq!(
                compiled
                    .matching_rule(&words, None, &[false; 3])
                    .unwrap()
                    .id,
                "longer"
            );
            assert!(compiled.matching_rule(&words, None, &[]).is_none());
        }
    }
}
