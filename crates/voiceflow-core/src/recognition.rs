//! What the recogniser itself reported about a session: how sure it was, which words it
//! was unsure of, which markers were dropped and which language tags it emitted.
//! Positions refer to the saved original ASR utterances; no word text is copied here.
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// A word is reported when its least certain token falls below this probability.
/// Greedy transducer tokens are usually above 0.9; below one half the model was closer
/// to another choice than to the one it emitted.
pub const LOW_CONFIDENCE: f32 = 0.5;
/// Positions kept per session. `low_words_total` still counts every uncertain word.
const MAX_LOW_WORDS: usize = 64;

/// One emitted token with the model's own probability for it.
#[derive(Debug, Clone, PartialEq)]
pub struct ScoredToken {
    /// Decoded piece, with a leading space where a word starts.
    pub text: String,
    pub probability: f32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct WordPosition {
    /// Index into the session's original ASR utterances.
    pub utterance: u32,
    /// Zero-based whitespace-separated word within that utterance.
    pub word: u32,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Recognition {
    /// Mean probability of the text tokens. Absent when the decoder gave no scores.
    pub mean_confidence: Option<f32>,
    pub scored_tokens: u32,
    pub threshold: f32,
    pub low_words: Vec<WordPosition>,
    pub low_words_total: u32,
    /// `<unk>` markers the recogniser emitted and the text processor removed.
    pub unknown_removed: u32,
    /// Language tags emitted by the model, by tag. Only the multilingual model in
    /// automatic mode emits them; they never reach the transcript.
    pub language_tags: BTreeMap<String, u32>,
}

/// Mirrors the recogniser's own rule for `<en>` and `<en-US>` pieces.
fn language_tag(piece: &str) -> bool {
    let Some(inner) = piece
        .strip_prefix('<')
        .and_then(|rest| rest.strip_suffix('>'))
        .map(str::as_bytes)
    else {
        return false;
    };
    match inner {
        [a, b] => a.is_ascii_lowercase() && b.is_ascii_lowercase(),
        [a, b, b'-', c, d] => {
            a.is_ascii_lowercase()
                && b.is_ascii_lowercase()
                && c.is_ascii_uppercase()
                && d.is_ascii_uppercase()
        }
        _ => false,
    }
}
fn marker(piece: &str) -> bool {
    let piece = piece.trim();
    piece.len() > 2 && piece.starts_with('<') && piece.ends_with('>')
}

#[derive(Debug, Default)]
pub(crate) struct Accumulator {
    sum: f64,
    scored: u32,
    result: Recognition,
}
impl Accumulator {
    /// Fold one finished utterance. `tokens` are the tokens behind `original`.
    pub fn utterance(&mut self, index: usize, original: &str, tokens: &[ScoredToken]) {
        self.result.unknown_removed += original.matches("<unk>").count() as u32;
        let mut word = 0u32;
        // Least certain text token of the word being read, and whether it has any character.
        let mut lowest: Option<f32> = None;
        let mut open = false;
        let mut close = |lowest: &mut Option<f32>, open: &mut bool, word: &mut u32| {
            if !*open {
                return;
            }
            if lowest.take().is_some_and(|p| p < LOW_CONFIDENCE) {
                self.result.low_words_total += 1;
                if self.result.low_words.len() < MAX_LOW_WORDS {
                    self.result.low_words.push(WordPosition {
                        utterance: index as u32,
                        word: *word,
                    });
                }
            }
            *open = false;
            *word += 1;
        };
        for token in tokens {
            let piece = token.text.trim();
            if language_tag(piece) {
                // Stripped from the transcript, so it is not a word of the original.
                *self.result.language_tags.entry(piece.into()).or_default() += 1;
                continue;
            }
            let scored = !marker(&token.text) && token.probability.is_finite();
            if scored && !piece.is_empty() {
                self.sum += f64::from(token.probability);
                self.scored += 1;
            }
            for character in token.text.chars() {
                if character.is_whitespace() {
                    close(&mut lowest, &mut open, &mut word);
                } else {
                    open = true;
                    if scored {
                        lowest =
                            Some(lowest.map_or(token.probability, |p| p.min(token.probability)));
                    }
                }
            }
        }
        close(&mut lowest, &mut open, &mut word);
    }
    pub fn finish(&self) -> Recognition {
        Recognition {
            mean_confidence: (self.scored > 0).then(|| (self.sum / f64::from(self.scored)) as f32),
            scored_tokens: self.scored,
            threshold: LOW_CONFIDENCE,
            ..self.result.clone()
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn token(text: &str, probability: f32) -> ScoredToken {
        ScoredToken {
            text: text.into(),
            probability,
        }
    }
    #[test]
    fn word_positions_follow_the_original_text_and_use_the_weakest_token() {
        let tokens = [
            token("<it-IT>", 0.2),
            token(" Buon", 0.99),
            token("gior", 0.45),
            token("no", 0.97),
            token(" <unk>", 0.1),
            token(" a", 0.9),
            token(" tut", 0.3),
            token("ti", 0.95),
            token(".", 0.2),
            token("<en-US>", 0.6),
        ];
        let original: String = tokens
            .iter()
            .filter(|t| !language_tag(t.text.trim()))
            .map(|t| t.text.as_str())
            .collect::<String>()
            .trim_start()
            .into();
        assert_eq!(original, "Buongiorno <unk> a tutti.");
        let mut stats = Accumulator::default();
        stats.utterance(3, &original, &tokens);
        let result = stats.finish();
        let words: Vec<&str> = original.split_whitespace().collect();
        let low: Vec<&str> = result
            .low_words
            .iter()
            .map(|p| {
                assert_eq!(p.utterance, 3);
                words[p.word as usize]
            })
            .collect();
        // The marker is counted as removed, never as an uncertain word.
        assert_eq!(low, ["Buongiorno", "tutti."]);
        assert_eq!((result.low_words_total, result.unknown_removed), (2, 1));
        assert_eq!(result.scored_tokens, 7);
        let mean = (0.99 + 0.45 + 0.97 + 0.9 + 0.3 + 0.95 + 0.2) / 7.0;
        assert!((result.mean_confidence.unwrap() - mean).abs() < 1e-6);
        assert_eq!(
            result.language_tags,
            BTreeMap::from([("<en-US>".to_owned(), 1), ("<it-IT>".to_owned(), 1)])
        );
        assert_eq!(result.threshold, LOW_CONFIDENCE);
    }
    #[test]
    fn unscored_decoders_still_count_removed_markers() {
        let mut stats = Accumulator::default();
        stats.utterance(0, "<unk> il la <unk> scheda", &[]);
        let result = stats.finish();
        assert_eq!(result.unknown_removed, 2);
        assert_eq!(result.mean_confidence, None);
        assert!(result.low_words.is_empty() && result.language_tags.is_empty());
    }
    #[test]
    fn positions_are_bounded_and_the_total_is_not() {
        let tokens: Vec<_> = (0..100).map(|_| token(" x", 0.1)).collect();
        let mut stats = Accumulator::default();
        stats.utterance(0, "", &tokens);
        let result = stats.finish();
        assert_eq!(result.low_words.len(), MAX_LOW_WORDS);
        assert_eq!(result.low_words_total, 100);
        assert_eq!(result.low_words[63].word, 63);
    }
    #[test]
    fn only_real_language_tags_are_recognised() {
        for tag in ["<it>", "<it-IT>", "<en-US>"] {
            assert!(language_tag(tag), "{tag}");
        }
        for other in ["<unk>", "<noise>", "<IT>", "<it-it>", "it-IT", "<>", "<i>"] {
            assert!(!language_tag(other), "{other}");
        }
    }
}
