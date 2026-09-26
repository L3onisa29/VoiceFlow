//! Confirmed terminology rules. Pure bounded matching over original source words.
use anyhow::{Result, ensure};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

pub const MAX_PHRASE_WORDS: usize = 7;
pub const MAX_RULES: usize = 4096;

#[derive(Debug, Clone, Copy, Default, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum MatchMode {
    #[default]
    Exact,
    UnicodeLowercase,
}
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Context {
    pub before: Option<String>,
    pub after: Option<String>,
    pub except_before: Vec<String>,
    pub except_after: Vec<String>,
}
#[derive(Debug, Clone)]
pub struct Rule {
    pub id: i64,
    pub variant: String,
    pub preferred: String,
    pub mode: MatchMode,
    pub context: Context,
    /// Language and group specificity, established by the snapshot compiler.
    pub scope: u8,
}
#[derive(Debug, Clone)]
struct CompiledRule {
    rule: Rule,
    words: Vec<String>,
}
#[derive(Debug, Clone, Default)]
pub struct Dictionary {
    rules: Vec<CompiledRule>,
    exact: HashMap<String, Vec<usize>>,
    normalized: HashMap<String, Vec<usize>>,
    lookahead: usize,
}
#[derive(Debug, PartialEq, Eq)]
pub enum Match {
    Replace { words: usize, text: String },
    Conflict { words: usize },
}

pub fn validate_rule(rule: &Rule) -> Result<()> {
    let words = rule.variant.split_whitespace().count();
    ensure!(
        words > 0 && words <= MAX_PHRASE_WORDS,
        "La variante deve contenere da 1 a 7 parole."
    );
    ensure!(
        rule.variant
            == rule
                .variant
                .split_whitespace()
                .collect::<Vec<_>>()
                .join(" "),
        "Usa un singolo spazio tra le parole della variante."
    );
    ensure!(
        rule.variant.chars().count() <= 200 && !rule.variant.chars().any(char::is_control),
        "Variante non valida."
    );
    ensure!(
        !rule.preferred.trim().is_empty()
            && rule.preferred.chars().count() <= 200
            && !rule.preferred.chars().any(char::is_control),
        "Grafia preferita non valida."
    );
    ensure!(
        !rule.preferred.contains(['<', '>']),
        "La grafia preferita non può introdurre marcatori tra parentesi angolari."
    );
    let context = &rule.context;
    ensure!(
        context.except_before.len() <= 32 && context.except_after.len() <= 32,
        "Troppe eccezioni."
    );
    for word in context
        .before
        .iter()
        .chain(context.after.iter())
        .chain(context.except_before.iter())
        .chain(context.except_after.iter())
    {
        ensure!(
            word.split_whitespace().count() == 1
                && word.trim() == word
                && !word.chars().any(char::is_control)
                && word.chars().count() <= 200,
            "Condizioni ed eccezioni richiedono una parola ciascuna."
        );
    }
    let right = usize::from(context.after.is_some() || !context.except_after.is_empty());
    ensure!(
        words + right <= MAX_PHRASE_WORDS,
        "Variante e contesto successivo superano la capacità di 7 parole."
    );
    Ok(())
}
fn key(text: &str, mode: MatchMode) -> String {
    match mode {
        MatchMode::Exact => text.to_owned(),
        MatchMode::UnicodeLowercase => text.to_lowercase(),
    }
}
// Strip only terminal prose punctuation. Apostrophes, accents, URL/code syntax
// and punctuation inside a token retain their exact identity.
fn terminal(text: &str) -> (&str, &str) {
    let core = text.trim_end_matches([',', '.', ':', ';', '!', '?']);
    (core, &text[core.len()..])
}
fn condition_count(context: &Context) -> usize {
    usize::from(context.before.is_some()) + usize::from(context.after.is_some())
}
fn contexts_overlap(a: &Context, b: &Context, mode: MatchMode) -> bool {
    fn side(
        a: &Option<String>,
        b: &Option<String>,
        except_a: &[String],
        except_b: &[String],
        mode: MatchMode,
    ) -> bool {
        if let (Some(a), Some(b)) = (a, b)
            && key(a, mode) != key(b, mode)
        {
            return false;
        }
        a.as_ref().or(b.as_ref()).is_none_or(|required| {
            !except_a
                .iter()
                .chain(except_b)
                .any(|except| key(except, mode) == key(required, mode))
        })
    }
    side(
        &a.before,
        &b.before,
        &a.except_before,
        &b.except_before,
        mode,
    ) && side(&a.after, &b.after, &a.except_after, &b.except_after, mode)
}

impl Dictionary {
    pub fn compile(rules: Vec<Rule>) -> Result<Self> {
        ensure!(
            rules.len() <= MAX_RULES,
            "Il dizionario supera il limite di 4096 varianti attive."
        );
        let mut result = Self::default();
        for rule in rules {
            validate_rule(&rule)?;
            let words: Vec<_> = rule
                .variant
                .split_whitespace()
                .map(|word| key(word, rule.mode))
                .collect();
            result.lookahead = result.lookahead.max(
                words.len()
                    + usize::from(
                        rule.context.after.is_some() || !rule.context.except_after.is_empty(),
                    ),
            );
            let index = result.rules.len();
            let map = match rule.mode {
                MatchMode::Exact => &mut result.exact,
                MatchMode::UnicodeLowercase => &mut result.normalized,
            };
            map.entry(terminal(&words[0]).0.to_owned())
                .or_default()
                .push(index);
            result.rules.push(CompiledRule { rule, words });
        }
        Ok(result)
    }
    pub fn is_empty(&self) -> bool {
        self.rules.is_empty()
    }
    /// Conservatively protect starts inside a multiword cleanup deletion without
    /// extending the dependency window for a dictionary rule beginning there.
    pub(crate) fn can_start_at(&self, word: &str) -> bool {
        let first = terminal(word).0;
        self.exact.contains_key(first) || self.normalized.contains_key(&first.to_lowercase())
    }
    /// One extra frozen source word protects commands overlapping a rule's end.
    pub fn required_frozen_words(&self) -> usize {
        (self.lookahead + 1).max(2)
    }
    pub fn apply(
        &self,
        words: &[(&str, &str)],
        before: Option<&str>,
        protected: &[bool],
    ) -> Option<Match> {
        let first = terminal(words.first()?.1).0;
        let candidates = self.exact.get(first).into_iter().flatten().chain(
            self.normalized
                .get(&first.to_lowercase())
                .into_iter()
                .flatten(),
        );
        type Specificity = (usize, usize, u8, bool);
        let mut best: Option<(Specificity, usize, String)> = None;
        let mut conflict = false;
        for &index in candidates {
            let compiled = &self.rules[index];
            let rule = &compiled.rule;
            let count = compiled.words.len();
            if count > words.len() || protected[..count].iter().any(|value| *value) {
                continue;
            }
            let mut suffix = "";
            let matches = compiled.words.iter().enumerate().all(|(i, expected)| {
                if i > 0
                    && (words[i].0.contains(['\n', '\r'])
                        || (rule.mode == MatchMode::Exact && words[i].0 != " "))
                {
                    return false;
                }
                let actual = words[i].1;
                if key(actual, rule.mode) == *expected {
                    return true;
                }
                if i + 1 == count && key(terminal(actual).0, rule.mode) == *expected {
                    suffix = terminal(actual).1;
                    return true;
                }
                false
            });
            if !matches {
                continue;
            }
            let equal = |actual: Option<&str>, expected: &str| {
                actual.is_some_and(|actual| key(actual, rule.mode) == key(expected, rule.mode))
            };
            let after = words.get(count).map(|word| word.1);
            let ctx = &rule.context;
            if ctx.before.as_ref().is_some_and(|word| !equal(before, word))
                || ctx.after.as_ref().is_some_and(|word| !equal(after, word))
                || ctx.except_before.iter().any(|word| equal(before, word))
                || ctx.except_after.iter().any(|word| equal(after, word))
            {
                continue;
            }
            let rank = (
                count,
                usize::from(ctx.before.is_some()) + usize::from(ctx.after.is_some()),
                rule.scope,
                rule.mode == MatchMode::Exact,
            );
            let replacement = format!("{}{suffix}", rule.preferred);
            match &best {
                Some((old, _, _)) if *old > rank => (),
                Some((old, _, text)) if *old == rank => {
                    conflict |= *text != replacement;
                }
                _ => {
                    best = Some((rank, count, replacement));
                    conflict = false;
                }
            }
        }
        best.map(|(_, words, text)| {
            if conflict {
                Match::Conflict { words }
            } else {
                Match::Replace { words, text }
            }
        })
    }
    /// Potential equal-specificity overlaps are surfaced even if the current
    /// group/language selection happens not to activate both rules.
    pub fn conflicts(&self) -> Vec<i64> {
        let mut ids = Vec::new();
        for (i, a) in self.rules.iter().enumerate() {
            for b in &self.rules[i + 1..] {
                let phrases_overlap =
                    a.words.len() == b.words.len()
                        && a.words.iter().zip(&b.words).enumerate().all(
                            |(index, (left, right))| {
                                left == right
                                    || (index + 1 == a.words.len()
                                        && terminal(left).0 == terminal(right).0)
                            },
                        );
                if phrases_overlap
                    && a.rule.mode == b.rule.mode
                    && a.rule.scope == b.rule.scope
                    && condition_count(&a.rule.context) == condition_count(&b.rule.context)
                    && contexts_overlap(&a.rule.context, &b.rule.context, a.rule.mode)
                    && (a.rule.preferred != b.rule.preferred || a.words.last() != b.words.last())
                {
                    ids.extend([a.rule.id, b.rule.id]);
                }
            }
        }
        ids.sort_unstable();
        ids.dedup();
        ids
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::formatting::{FormatMode, TextProcessor};
    fn rule(variant: &str, preferred: &str) -> Rule {
        Rule {
            id: 1,
            variant: variant.into(),
            preferred: preferred.into(),
            mode: MatchMode::Exact,
            context: Context::default(),
            scope: 0,
        }
    }
    fn process(rules: Vec<Rule>, input: &str, mode: FormatMode, cleanup: bool) -> String {
        let mut processor =
            TextProcessor::with_dictionary(mode, cleanup, Dictionary::compile(rules).unwrap());
        processor.update(input, true).unwrap();
        processor.parts().0.to_owned()
    }
    #[test]
    fn explicit_whole_words_preserve_unicode_apostrophes_punctuation_and_control_text() {
        let mut normalized = rule("école", "École");
        normalized.mode = MatchMode::UnicodeLowercase;
        assert_eq!(
            process(
                vec![
                    rule("voice flow", "VoiceFlow"),
                    normalized,
                    rule("dell'api", "dell’API")
                ],
                "voice flow, ÉCOLE! dell'api. dell’api voice flower non 42 https://voice.flow x_voice_flow `voice` molto molto importante la dispositivo",
                FormatMode::Faithful,
                false
            ),
            "VoiceFlow, École! dell’API. dell’api voice flower non 42 https://voice.flow x_voice_flow `voice` molto molto importante la dispositivo"
        );
        assert_eq!(
            process(
                vec![rule("voice flow", "VoiceFlow")],
                "voice  flow voice\nflow Voice flow",
                FormatMode::Faithful,
                false
            ),
            "voice  flow voice\nflow Voice flow"
        );
    }
    #[test]
    fn no_recursive_substitution_or_generated_command_or_cleanup() {
        assert_eq!(
            process(
                vec![
                    rule("alpha", "beta"),
                    rule("beta", "gamma"),
                    rule("delta", "new line"),
                    rule("echo", "il il")
                ],
                "alpha beta delta echo",
                FormatMode::SpokenCommands,
                true
            ),
            "beta gamma new line il il"
        );
    }
    #[test]
    fn most_specific_wins_and_equal_priority_conflicts_keep_original() {
        let mut scoped = rule("alpha", "scoped");
        scoped.scope = 1;
        assert_eq!(
            process(
                vec![
                    rule("alpha", "generic"),
                    scoped,
                    rule("alpha beta", "phrase")
                ],
                "alpha alpha beta",
                FormatMode::Faithful,
                false
            ),
            "scoped phrase"
        );
        let a = rule("alpha beta", "first");
        let mut b = rule("alpha beta", "second");
        b.id = 2;
        let compiled = Dictionary::compile(vec![a.clone(), b.clone()]).unwrap();
        assert_eq!(compiled.conflicts(), vec![1, 2]);
        assert_eq!(
            process(
                vec![a, b, rule("alpha", "short")],
                "alpha beta,",
                FormatMode::Faithful,
                false
            ),
            "alpha beta,"
        );
    }
    #[test]
    fn context_and_exceptions_use_original_source_and_do_not_cross_pauses() {
        let mut contextual = rule("flow", "Flow");
        contextual.context.before = Some("voice".into());
        contextual.context.after = Some("works".into());
        contextual.context.except_after = vec!["fails".into()];
        assert_eq!(
            process(
                vec![contextual.clone(), rule("voice", "VOICE")],
                "voice flow works voice flow fails other flow works",
                FormatMode::Faithful,
                false
            ),
            "VOICE Flow works VOICE flow fails other flow works"
        );
        let mut processor = TextProcessor::with_dictionary(
            FormatMode::Faithful,
            false,
            Dictionary::compile(vec![contextual]).unwrap(),
        );
        processor.update("voice", true).unwrap();
        processor.update("flow works", true).unwrap();
        assert_eq!(processor.parts().0, "voice flow works");
    }
    #[test]
    fn seven_word_dependencies_stay_uncommitted_until_last_source_is_frozen() {
        let phrase = "one two three four five six seven";
        for prefix in 0..18 {
            let mut processor = TextProcessor::with_dictionary(
                FormatMode::Faithful,
                false,
                Dictionary::compile(vec![rule(phrase, "Seven")]).unwrap(),
            );
            let leading = "pad ".repeat(prefix);
            let original = format!("{leading}{phrase} tail tail tail tail tail tail ");
            processor.update(&original, false).unwrap();
            assert!(
                !processor.parts().0.contains("Seven"),
                "dependent seventh source is still revisable"
            );
            assert!(
                !processor.parts().0.contains("one"),
                "first word cannot be committed separately"
            );
            let revised = original.replacen("seven", "changed", 1);
            processor.update(&revised, false).unwrap();
            assert!(!format!("{}{}", processor.parts().0, processor.parts().1).contains("Seven"));
            processor.update(&revised, true).unwrap();
            assert_eq!(processor.parts().0, revised.trim_end());
            assert_eq!(processor.retained_words(), 0);
        }
    }
    #[test]
    fn arbitrary_fragmentation_revisions_and_boundaries_have_batch_parity() {
        let rules = vec![
            rule("one two three four five six seven", "Seven"),
            rule("voice flow", "VoiceFlow"),
            rule("il", "IL"),
        ];
        let input = "inizio one two three four five six seven, voice flow nuovo paragrafo fine non 123 molto molto";
        let expected = process(rules.clone(), input, FormatMode::SpokenCommands, true);
        let mut processor = TextProcessor::with_dictionary(
            FormatMode::SpokenCommands,
            true,
            Dictionary::compile(rules).unwrap(),
        );
        for end in 1..=input.len() {
            if input.is_char_boundary(end) {
                processor.update(&input[..end], false).unwrap();
                assert!(processor.buffered_words() <= 7);
                assert!(processor.retained_words() <= crate::formatting::DICTIONARY_RETAINED_WORDS);
            }
        }
        processor.update(input, true).unwrap();
        assert_eq!(processor.parts().0, expected);
    }
    #[test]
    fn overlapping_commands_survive_at_every_phrase_boundary_and_mode_remains_explicit() {
        for variant in ["new", "line", "x new", "line y", "x new line y"] {
            assert_eq!(
                process(
                    vec![rule(variant, "WRONG")],
                    "x new line y",
                    FormatMode::SpokenCommands,
                    false
                ),
                "x\ny"
            );
        }
        assert_eq!(
            process(
                vec![rule("new line", "literal")],
                "new line",
                FormatMode::Faithful,
                false
            ),
            "literal"
        );
    }
    #[test]
    fn oversized_and_unbounded_rule_shapes_are_rejected() {
        assert!(
            Dictionary::compile(vec![rule("one two three four five six seven eight", "No")])
                .is_err()
        );
        let mut context = rule("one two three four five six seven", "No");
        context.context.after = Some("next".into());
        assert!(Dictionary::compile(vec![context]).is_err());
        let mut context = rule("word", "No");
        context.context.before = Some("two words".into());
        assert!(Dictionary::compile(vec![context]).is_err());
    }
    #[test]
    fn enabled_dictionary_preserves_existing_cleanup_and_markers_outside_explicit_matches() {
        for (input, expected) in [
            (
                "il il il la scheda della dell’acquisizione",
                "la scheda dell’acquisizione",
            ),
            ("ciao<unk> mondo <tag with space> bene", "ciao mondo bene"),
            (
                "non molto molto importante 42",
                "non molto molto importante 42",
            ),
        ] {
            assert_eq!(
                process(
                    vec![rule("unmatched", "unused")],
                    input,
                    FormatMode::SpokenCommands,
                    true
                ),
                expected
            );
        }
    }
    #[test]
    fn explicit_terminal_punctuation_overlap_is_visible_and_retains_original() {
        let a = rule("word", "Preferred");
        let mut b = rule("word.", "Preferred");
        b.id = 2;
        assert_eq!(
            Dictionary::compile(vec![a.clone(), b.clone()])
                .unwrap()
                .conflicts(),
            vec![1, 2]
        );
        assert_eq!(
            process(vec![a, b], "word.", FormatMode::Faithful, false),
            "word."
        );
    }
}
