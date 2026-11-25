//! Word error against a known reference text. Counts only: no text leaves this module.
use serde::Serialize;

/// Lowercase words without punctuation or decoder markers. Hyphens and apostrophes
/// separate words, so "text-to-speech" equals "text to speech" and a term is found
/// behind any elided article ("l'accordion", "dell'accordion").
pub fn normalize(text: &str) -> Vec<String> {
    let mut clean = String::with_capacity(text.len());
    let mut marker = false;
    for character in text.chars() {
        match character {
            '<' => marker = true,
            '>' if marker => {
                marker = false;
                clean.push(' ');
            }
            _ if marker => (),
            c if c.is_alphanumeric() => clean.extend(c.to_lowercase()),
            _ => clean.push(' '),
        }
    }
    clean.split_whitespace().map(str::to_owned).collect()
}

/// Substitutions, deletions and insertions needed to turn the hypothesis into the reference.
pub fn word_errors(reference: &[String], hypothesis: &[String]) -> usize {
    let mut previous: Vec<usize> = (0..=hypothesis.len()).collect();
    for (i, expected) in reference.iter().enumerate() {
        let mut current = vec![i + 1];
        for (j, actual) in hypothesis.iter().enumerate() {
            current.push(
                (previous[j] + usize::from(expected != actual))
                    .min(previous[j + 1] + 1)
                    .min(current[j] + 1),
            );
        }
        previous = current;
    }
    previous[hypothesis.len()]
}

fn occurrences(words: &[String], term: &[String]) -> usize {
    if term.is_empty() || term.len() > words.len() {
        return 0;
    }
    let mut count = 0;
    let mut index = 0;
    while index + term.len() <= words.len() {
        if words[index..index + term.len()] == *term {
            count += 1;
            index += term.len();
        } else {
            index += 1;
        }
    }
    count
}

#[derive(Debug, Clone, Default, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Tally {
    pub sentences: usize,
    pub words: usize,
    pub errors: usize,
    /// Times a listed term occurs in the references.
    pub term_occurrences: usize,
    /// Of those, how many the hypothesis of the same sentence does not contain.
    pub term_misses: usize,
}
impl Tally {
    /// `terms` are already normalized word sequences.
    pub fn add(&mut self, reference: &str, hypothesis: &str, terms: &[Vec<String>]) {
        let (reference, hypothesis) = (normalize(reference), normalize(hypothesis));
        self.sentences += 1;
        self.words += reference.len();
        self.errors += word_errors(&reference, &hypothesis);
        for term in terms {
            let expected = occurrences(&reference, term);
            self.term_occurrences += expected;
            self.term_misses += expected.saturating_sub(occurrences(&hypothesis, term));
        }
    }
    pub fn word_error_rate(&self) -> Option<f64> {
        (self.words > 0).then(|| self.errors as f64 / self.words as f64)
    }
    pub fn term_error_rate(&self) -> Option<f64> {
        (self.term_occurrences > 0).then(|| self.term_misses as f64 / self.term_occurrences as f64)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn words(text: &str) -> Vec<String> {
        normalize(text)
    }
    #[test]
    fn normalization_ignores_case_punctuation_markers_and_hyphens() {
        assert_eq!(
            words("<unk> Apri il Bottom Sheet, poi l’omino: text-to-speech!"),
            [
                "apri", "il", "bottom", "sheet", "poi", "l", "omino", "text", "to", "speech"
            ]
        );
        assert!(words("... <noise> —").is_empty());
        assert_eq!(words("Dell'accordion"), words("dell’ accordion"));
    }
    #[test]
    fn word_errors_count_substitutions_deletions_and_insertions() {
        assert_eq!(word_errors(&words("a b c d"), &words("a b c d")), 0);
        assert_eq!(word_errors(&words("a b c d"), &words("a x c d")), 1);
        assert_eq!(word_errors(&words("a b c d"), &words("a c d")), 1);
        assert_eq!(word_errors(&words("a b c d"), &words("a b b c d e")), 2);
        assert_eq!(word_errors(&words("a b"), &[]), 2);
        assert_eq!(word_errors(&[], &words("a b")), 2);
    }
    #[test]
    fn tally_reports_overall_and_term_error() {
        let terms = vec![words("bottom sheet"), words("TestFlight"), words("task")];
        let mut tally = Tally::default();
        tally.add(
            "Apri il bottom sheet e chiudi il task.",
            "apri il botton shit e chiudi il task",
            &terms,
        );
        tally.add(
            "Carica la build su TestFlight.",
            "Carica la build su Tes Flight",
            &terms,
        );
        tally.add("Nessun termine qui.", "nessun termine qui", &terms);
        assert_eq!((tally.sentences, tally.words, tally.errors), (3, 16, 4));
        assert_eq!((tally.term_occurrences, tally.term_misses), (3, 2));
        assert_eq!(tally.word_error_rate(), Some(0.25));
        assert!((tally.term_error_rate().unwrap() - 2.0 / 3.0).abs() < 1e-9);
        assert_eq!(Tally::default().word_error_rate(), None);
        // A term said twice and heard once is one miss; extra hits are not credited.
        let mut repeated = Tally::default();
        repeated.add("task e poi task", "task e poi tasca", &terms);
        repeated.add("task", "task task", &terms);
        assert_eq!((repeated.term_occurrences, repeated.term_misses), (3, 1));
    }
}
