use anyhow::{Result, ensure};
use serde::{Deserialize, Serialize};
use std::collections::VecDeque;

#[derive(Debug, Clone, Copy, Default, Deserialize, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum FormatMode {
    #[default]
    Faithful,
    SpokenCommands,
}

pub const MUTABLE_WORDS: usize = 7;
pub const RETAINED_WORDS: usize = MUTABLE_WORDS + 1;

#[derive(Clone)]
struct Word {
    separator: String,
    text: String,
    end: usize,
}

struct Atom<'a> {
    separator: &'a str,
    text: &'a str,
    command: bool,
}

/// Keep seven revisable words and one frozen-source word of lookbehind.
/// Its rendering waits until the next source word is frozen too, so a deletion
/// never commits while the word supporting it can still be revised.
/// Confirmed output and frozen source are immutable. Revisions to the seven-word
/// raw tail, including its separators and an unfinished word, are supported.
pub struct TextProcessor {
    mode: FormatMode,
    cleanup: bool,
    pending: VecDeque<Word>,
    lookbehind: Option<Word>,
    confirmed: String,
    incomplete: String,
    accepted_tail: String,
    parsed_offset: usize,
    // Source boundary, including lookbehind whose output is not yet confirmed.
    released_offset: usize,
    after_command: bool,
    utterance_start: bool,
}

impl TextProcessor {
    pub fn new(mode: FormatMode, cleanup: bool) -> Self {
        Self {
            mode,
            cleanup,
            pending: VecDeque::new(),
            lookbehind: None,
            confirmed: String::new(),
            incomplete: String::new(),
            accepted_tail: String::new(),
            parsed_offset: 0,
            released_offset: 0,
            after_command: false,
            utterance_start: true,
        }
    }

    /// Accept an accumulated decoder snapshot. A word is complete only when a
    /// whitespace boundary arrives, or when the utterance is explicitly flushed.
    pub fn update(&mut self, snapshot: &str, complete: bool) -> Result<()> {
        ensure!(
            snapshot.is_char_boundary(self.released_offset),
            "Decoder revised text outside the mutable window"
        );
        if snapshot.get(self.released_offset..self.parsed_offset)
            != Some(self.accepted_tail.as_str())
        {
            self.pending.clear();
            self.parsed_offset = self.released_offset;
        }
        let mut cursor = self.parsed_offset;
        while cursor < snapshot.len() {
            let rest = &snapshot[cursor..];
            let Some(start) = rest.find(|c: char| !c.is_whitespace()) else {
                break;
            };
            let Some(end) = decoder_word_end(&rest[start..], complete).map(|n| start + n) else {
                break;
            };
            self.pending.push_back(Word {
                separator: rest[..start].to_owned(),
                text: strip_decoder_markers(&rest[start..end], true),
                end: cursor + end,
            });
            cursor += end;
            self.parsed_offset = cursor;
            // Freeze source words before committing their dependent rendering.
            self.release(false);
        }
        self.accepted_tail = snapshot[self.released_offset..self.parsed_offset].to_owned();
        self.incomplete = strip_decoder_markers(&snapshot[self.parsed_offset..], false);
        if complete {
            self.release(true);
            self.incomplete.clear();
            self.accepted_tail.clear();
            self.parsed_offset = 0;
            self.released_offset = 0;
            self.utterance_start = true;
        }
        Ok(())
    }

    fn atoms(&self) -> Vec<Atom<'_>> {
        let words: Vec<_> = self
            .lookbehind
            .iter()
            .chain(self.pending.iter())
            .filter(|word| !word.text.is_empty())
            .collect();
        let mut atoms: Vec<Atom<'_>> = Vec::with_capacity(words.len());
        let mut i = 0;
        while i < words.len() {
            let word = words[i];
            if matches!(self.mode, FormatMode::SpokenCommands)
                && let Some(next) = words.get(i + 1)
                && let Some(text) = command(&word.text, &next.text, &next.separator)
            {
                atoms.push(Atom {
                    separator: &word.separator,
                    text,
                    command: true,
                });
                i += 2;
                continue;
            }
            let mut atom = Atom {
                separator: &word.separator,
                text: &word.text,
                command: false,
            };
            if self.cleanup
                && let Some(previous) = atoms.iter_mut().rev().find(|atom| !atom.text.is_empty())
                && !previous.command
                && false_start(previous.text, atom.text, atom.separator)
            {
                atom.separator = previous.separator;
                // Preview only. Release retains source provenance separately.
                previous.text = "";
            }
            atoms.push(atom);
            i += 1;
        }
        atoms
    }

    fn release(&mut self, all: bool) {
        while !self.pending.is_empty() && (all || self.pending.len() > MUTABLE_WORDS) {
            let mut word = self.pending.pop_front().unwrap();
            self.released_offset = word.end;
            if word.text.is_empty() {
                continue;
            }
            if let Some(previous) = self.lookbehind.take() {
                if matches!(self.mode, FormatMode::SpokenCommands)
                    && let Some(text) = command(&previous.text, &word.text, &word.separator)
                {
                    self.commit(&previous.separator, text, true);
                    continue;
                }
                if self.cleanup && false_start(&previous.text, &word.text, &word.separator) {
                    // Both source words are now frozen. Only this immediate
                    // deletion is final; the survivor remains as lookbehind.
                    // Repeat chains therefore need no additional history.
                    word.separator = previous.separator;
                } else {
                    self.commit(&previous.separator, &previous.text, false);
                }
            }
            self.lookbehind = Some(word);
        }
        if all && let Some(word) = self.lookbehind.take() {
            self.commit(&word.separator, &word.text, false);
        }
    }

    fn commit(&mut self, separator: &str, text: &str, command: bool) {
        let previous = self.confirmed.chars().last();
        render(
            &mut self.confirmed,
            &Atom {
                separator,
                text,
                command,
            },
            &mut self.after_command,
            self.utterance_start,
            previous,
        );
        self.utterance_start = false;
    }

    /// Parts concatenate directly: partial owns its leading separator. This
    /// preserves newlines even when a command sits at the confirmed boundary.
    pub fn parts(&self) -> (&str, String) {
        let mut partial = String::new();
        let mut after_command = self.after_command;
        let mut utterance_start = self.utterance_start;
        let mut previous = self.confirmed.chars().last();
        for atom in self.atoms() {
            if atom.text.is_empty() {
                continue;
            }
            render(
                &mut partial,
                &atom,
                &mut after_command,
                utterance_start,
                previous,
            );
            previous = partial.chars().last().or(previous);
            utterance_start = false;
        }
        let start = self.incomplete.find(|c: char| !c.is_whitespace());
        if let Some(start) = start {
            let atom = Atom {
                separator: &self.incomplete[..start],
                text: &self.incomplete[start..],
                command: false,
            };
            render(
                &mut partial,
                &atom,
                &mut after_command,
                utterance_start,
                previous,
            );
        }
        (&self.confirmed, partial)
    }

    /// Complete words whose source is still revisable, excluding lookbehind.
    pub fn buffered_words(&self) -> usize {
        self.pending.len()
    }

    /// Includes the single frozen-source word whose output is still dependent
    /// on the revisable tail. Excludes an unfinished word.
    pub fn retained_words(&self) -> usize {
        self.pending.len() + usize::from(self.lookbehind.is_some())
    }
}

/// Marker contents may contain whitespace. Keep raw byte offsets so decoder
/// revisions still address the original snapshot, not the cleaned output.
fn decoder_word_end(text: &str, complete: bool) -> Option<usize> {
    let mut chars = text.char_indices();
    while let Some((index, ch)) = chars.next() {
        if ch.is_whitespace() {
            return Some(index);
        }
        if ch == '<' {
            if let Some(end) = text[index..].find('>') {
                let end = index + end;
                for (index, _) in chars.by_ref() {
                    if index == end {
                        break;
                    }
                }
            } else if !complete {
                return None;
            }
        }
    }
    complete.then_some(text.len())
}

/// Remove every <...> span. Hide an unfinished marker in live previews, but
/// preserve an unmatched '<' when the utterance is finalized.
fn strip_decoder_markers(mut text: &str, complete: bool) -> String {
    let mut cleaned = String::with_capacity(text.len());
    while let Some(start) = text.find('<') {
        cleaned.push_str(&text[..start]);
        let Some(end) = text[start..].find('>') else {
            if complete {
                cleaned.push_str(&text[start..]);
            }
            return cleaned;
        };
        text = &text[start + end + 1..];
    }
    cleaned.push_str(text);
    cleaned
}

fn render(
    out: &mut String,
    atom: &Atom<'_>,
    after_command: &mut bool,
    utterance_start: bool,
    previous: Option<char>,
) {
    let last = out.chars().last().or(previous);
    if atom.command {
        // Adjacent commands retain the earlier paragraph break, as in the
        // original formatter: "new paragraph bullet point" produces \n\n•.
        let replacement = if last == Some('\n') {
            atom.text.trim_start_matches('\n')
        } else {
            atom.text
        };
        out.push_str(replacement);
        *after_command = true;
    } else {
        if *after_command && last == Some('•') {
            out.push(' ');
        } else if !*after_command && last.is_some() {
            if utterance_start {
                if !last.is_some_and(char::is_whitespace) {
                    out.push(' ');
                }
            } else {
                out.push_str(atom.separator);
            }
        }
        out.push_str(atom.text);
        *after_command = false;
    }
}

fn command(first: &str, second: &str, separator: &str) -> Option<&'static str> {
    if separator.contains(['\n', '\r']) {
        return None;
    }
    // Punctuation may follow the command, but never split its two words.
    let second = second.trim_end_matches([',', '.', ':', ';', '!', '?']);
    [
        ("nuovo", "paragrafo", "\n\n"),
        ("new", "paragraph", "\n\n"),
        ("punto", "elenco", "\n•"),
        ("bullet", "point", "\n•"),
        ("a", "capo", "\n"),
        ("new", "line", "\n"),
    ]
    .into_iter()
    .find(|(a, b, _)| first.eq_ignore_ascii_case(a) && second.eq_ignore_ascii_case(b))
    .map(|(_, _, replacement)| replacement)
}

fn false_start(first: &str, second: &str, separator: &str) -> bool {
    if separator.contains(['\n', '\r']) {
        return false;
    }
    // Deliberately lowercase and Italian-only. No general repeated-word rule:
    // "che che", English titles, emphasis, negation and names remain literal.
    let second = second.trim_end_matches([',', '.', ':', ';', '!', '?']);
    if first == "il" && second == "la" {
        return true;
    }
    if first == second
        && [
            "il", "lo", "la", "gli", "le", "un", "una", "di", "del", "della", "dello", "dei",
            "degli", "delle",
        ]
        .contains(&first)
    {
        return true;
    }
    first == "della"
        && ["dell'", "dell’"].iter().any(|prefix| {
            second
                .strip_prefix(prefix)
                .is_some_and(|tail| !tail.is_empty() && tail.chars().all(char::is_lowercase))
        })
}

/// Batch callers use exactly the same rules and word window as live dictation.
pub fn format_transcript(text: &str, mode: FormatMode) -> String {
    let mut processor = TextProcessor::new(mode, false);
    processor
        .update(text, true)
        .expect("new processor accepts any UTF-8 text");
    processor.parts().0.trim().to_owned()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn clean(text: &str) -> String {
        let mut processor = TextProcessor::new(FormatMode::Faithful, true);
        processor.update(text, true).unwrap();
        processor.parts().0.trim().to_owned()
    }

    #[test]
    fn decoder_markers_are_removed_in_every_mode_without_linguistic_cleanup() {
        for mode in [FormatMode::Faithful, FormatMode::SpokenCommands] {
            for (input, expected) in [
                ("<unk> Ciao <noise> mondo <eos>", "Ciao mondo"),
                ("Ciao<unk>, <|noise|>perché<>?", "Ciao, perché?"),
                ("<unk><noise>", ""),
                ("uno <background noise> due", "uno due"),
                ("uno\n<unk>\ndue", "uno\ndue"),
                ("ciao <incompleto", "ciao <incompleto"),
                ("2 < 3", "2 < 3"),
            ] {
                assert_eq!(format_transcript(input, mode), expected, "{input:?}");
            }
        }
    }

    #[test]
    fn fragmented_markers_never_leak_into_preview() {
        let input = "Ciao<rumore è forte> mondo <unk>";
        let mut processor = TextProcessor::new(FormatMode::Faithful, false);
        for end in (0..=input.len()).filter(|&end| input.is_char_boundary(end)) {
            processor.update(&input[..end], false).unwrap();
            let (confirmed, partial) = processor.parts();
            let preview = format!("{confirmed}{partial}");
            assert!("Ciao mondo".starts_with(&preview), "{preview:?}");
        }
        processor.update(input, true).unwrap();
        assert_eq!(processor.parts(), ("Ciao mondo", String::new()));
    }

    #[test]
    fn marker_revisions_keep_raw_offsets_and_confirmed_text() {
        let prefix = "<unk> uno due tre quattro cinque sei sette otto nove ";
        let mut processor = TextProcessor::new(FormatMode::Faithful, false);
        processor.update(&format!("{prefix}<un"), false).unwrap();
        let confirmed = processor.parts().0.to_owned();
        assert!(!confirmed.is_empty());
        for tail in ["<unk> fine ", "parola fine ", "<noise> fine "] {
            processor.update(&format!("{prefix}{tail}"), false).unwrap();
            let (current, partial) = processor.parts();
            assert!(current.starts_with(&confirmed));
            assert_eq!(
                format!("{current}{partial}"),
                format_transcript(&format!("{prefix}{tail}"), FormatMode::Faithful)
            );
        }
        processor
            .update(&format!("{prefix}<noise> fine "), true)
            .unwrap();
        assert_eq!(
            processor.parts().0,
            "uno due tre quattro cinque sei sette otto nove fine"
        );
    }

    #[test]
    fn marker_only_words_do_not_break_commands_or_grow_the_window() {
        let mut processor = TextProcessor::new(FormatMode::SpokenCommands, true);
        let mut input = String::from("il <unk> la scheda a ");
        for _ in 0..100 {
            input.push_str("<noise> ");
            processor.update(&input, false).unwrap();
            assert!(processor.buffered_words() <= MUTABLE_WORDS);
            assert!(processor.retained_words() <= RETAINED_WORDS);
        }
        input.push_str("capo fine ");
        processor.update(&input, false).unwrap();
        let (confirmed, partial) = processor.parts();
        assert_eq!(format!("{confirmed}{partial}"), "la scheda\nfine");
        processor.update(&input, true).unwrap();
        assert_eq!(processor.parts(), ("la scheda\nfine", String::new()));
    }

    #[test]
    fn cleanup_is_limited_to_local_italian_false_starts() {
        for (input, expected) in [
            ("il la scheda", "la scheda"),
            ("della dell’acquisizione", "dell’acquisizione"),
            ("della dell'acquisizione", "dell'acquisizione"),
            ("della dell’acquisizione.", "dell’acquisizione."),
            ("di di nuovo", "di nuovo"),
            ("il il la scheda", "la scheda"),
        ] {
            assert_eq!(clean(input), expected);
        }
        for text in [
            "molto molto importante",
            "non non approvare",
            "che che cosa",
            "the The Who",
            "The The",
            "la dispositivo",
            "quel ragionamento di prima non va bene",
            "Mario Mario Rossi",
            "12 12 1.200 euro",
            "un uno e due due",
            "i i II II",
            "il, la scheda",
            "il. la scheda",
            "il\nla scheda",
            "di: di nuovo",
            "La La Land",
            "della Dell’Acqua",
            "Forse venerdì, non lunedì.",
        ] {
            assert_eq!(clean(text), text);
        }
    }

    #[test]
    fn faithful_mode_preserves_words_punctuation_and_whitespace() {
        let text = "Non  approvare i 1.200 euro.\nForse venerdì, non lunedì. a capo il la scheda";
        assert_eq!(format_transcript(text, FormatMode::Faithful), text);
    }

    #[test]
    fn explicit_lists_support_italian_and_english() {
        assert_eq!(
            format_transcript(
                "Spesa: punto elenco pane punto elenco latte",
                FormatMode::SpokenCommands
            ),
            "Spesa:\n• pane\n• latte"
        );
        assert_eq!(
            format_transcript(
                "Notes. New paragraph Bullet point Test",
                FormatMode::SpokenCommands
            ),
            "Notes.\n\n• Test"
        );
        assert_eq!(
            format_transcript(
                "Ciao a capo mondo nuovo paragrafo Fine",
                FormatMode::SpokenCommands
            ),
            "Ciao\nmondo\n\nFine"
        );
        assert_eq!(
            format_transcript(
                "Hello new line world new paragraph Done",
                FormatMode::SpokenCommands
            ),
            "Hello\nworld\n\nDone"
        );
        assert_eq!(
            format_transcript(
                "punto elenco nuovo paragrafo Test",
                FormatMode::SpokenCommands
            ),
            "•\n\nTest"
        );
        for literal in [
            "a",
            "new",
            "new. line",
            "a\ncapo",
            "Newcastle line",
            "new linework",
        ] {
            assert_eq!(
                format_transcript(literal, FormatMode::SpokenCommands),
                literal
            );
        }
    }

    #[test]
    fn partial_word_and_repeated_snapshots_are_never_duplicated() {
        let mut processor = TextProcessor::new(FormatMode::Faithful, true);
        for (snapshot, expected) in [
            ("il la sch", "la sch"),
            ("il la sch", "la sch"),
            ("il la scheda", "la scheda"),
            ("il la scheda ", "la scheda"),
        ] {
            processor.update(snapshot, false).unwrap();
            let (confirmed, partial) = processor.parts();
            assert_eq!(format!("{confirmed}{partial}"), expected);
        }
        processor.update("il la scheda", true).unwrap();
        assert_eq!(processor.parts(), ("la scheda", String::new()));
    }

    #[test]
    fn mutable_tail_can_be_revised_without_touching_released_words() {
        let mut processor = TextProcessor::new(FormatMode::Faithful, true);
        processor
            .update("uno due tre quattro cinque sei sette otto il la sch", false)
            .unwrap();
        let released = processor.parts().0.to_owned();
        assert!(!released.is_empty());
        processor
            .update("uno due tre quattro cinque sei sette otto la scheda", true)
            .unwrap();
        assert!(processor.parts().0.starts_with(&released));
        assert_eq!(
            processor.parts().0,
            "uno due tre quattro cinque sei sette otto la scheda"
        );
    }

    #[test]
    fn cleanup_keeps_frozen_source_until_its_revisable_dependency_settles() {
        for (before, after, expected) in [
            ("di di", "di domani", "di domani"),
            ("il la", "il lo", "il lo"),
            ("della dell'acquisizione", "della casa", "della casa"),
            ("di di", "di di.", "di."),
            ("di di", "di domani,", "di domani,"),
            ("di di", "di\ndi", "di\ndi"),
            ("il la", "il\r\nla", "il\r\nla"),
            ("di\ndi", "di di", "di"),
            ("di di.", "di domani", "di domani"),
        ] {
            let suffix = " uno due tre quattro cinque sei ";
            let mut processor = TextProcessor::new(FormatMode::Faithful, true);
            processor
                .update(&format!("{before}{suffix}"), false)
                .unwrap();
            assert_eq!(processor.buffered_words(), MUTABLE_WORDS);
            assert_eq!(processor.retained_words(), RETAINED_WORDS);
            assert_eq!(processor.parts().0, "");
            let revised = format!("{after}{suffix}");
            let expected = format!("{expected}{}", suffix.trim_end());
            processor.update(&revised, false).unwrap();
            let (confirmed, partial) = processor.parts();
            assert_eq!(
                format!("{confirmed}{partial}"),
                expected,
                "{before:?} -> {after:?}"
            );
            processor.update(&revised, true).unwrap();
            assert_eq!(processor.parts(), (expected.as_str(), String::new()));
            assert_eq!(processor.retained_words(), 0);
        }
    }

    #[test]
    fn command_dependency_remains_revisable_at_the_same_boundary() {
        for (before, after, expected) in [
            ("a capo", "a casa", "a casa"),
            ("a capo", "a\ncapo", "a\ncapo"),
            ("a casa", "a capo.", "\n"),
            ("new paragraph", "new line", "\n"),
        ] {
            let suffix = " uno due tre quattro cinque sei ";
            let mut processor = TextProcessor::new(FormatMode::SpokenCommands, true);
            processor
                .update(&format!("{before}{suffix}"), false)
                .unwrap();
            processor.update(&format!("{after}{suffix}"), true).unwrap();
            let separator = if expected.ends_with('\n') { "" } else { " " };
            assert_eq!(
                processor.parts().0,
                format!("{expected}{separator}{}", suffix.trim())
            );
        }
    }

    #[test]
    fn long_repeat_chains_keep_constant_provenance_and_allow_tail_revisions() {
        for (word, replacement) in [("di", "domani"), ("il", "lo")] {
            let mut processor = TextProcessor::new(FormatMode::Faithful, true);
            let mut input = String::new();
            for _ in 0..10_000 {
                input.push_str(word);
                input.push(' ');
                processor.update(&input, false).unwrap();
                assert!(processor.buffered_words() <= MUTABLE_WORDS);
                assert!(processor.retained_words() <= RETAINED_WORDS);
                assert_eq!(processor.parts(), ("", word.to_owned()));
            }
            input.push_str("uno due tre quattro cinque sei ");
            processor.update(&input, false).unwrap();
            // Revise the first word of the seven-word mutable tail. The preceding
            // frozen word must reappear, even after thousands of prior deletions.
            input.replace_range(
                (10_000 - 1) * (word.len() + 1)..10_000 * (word.len() + 1),
                &format!("{replacement} "),
            );
            processor.update(&input, true).unwrap();
            assert_eq!(
                processor.parts().0,
                format!("{word} {replacement} uno due tre quattro cinque sei")
            );
            assert_eq!(processor.retained_words(), 0);
        }
    }

    #[test]
    fn long_input_keeps_only_seven_mutable_words_and_atomic_commands() {
        let mut processor = TextProcessor::new(FormatMode::SpokenCommands, true);
        let mut input = String::new();
        for _ in 0..10_000 {
            input.push_str("voce ");
            processor.update(&input, false).unwrap();
            assert!(processor.buffered_words() <= MUTABLE_WORDS);
        }
        input.push_str("a capo ");
        processor.update(&input, false).unwrap();
        for _ in 0..10 {
            input.push_str("fine ");
            processor.update(&input, false).unwrap();
            let (confirmed, partial) = processor.parts();
            assert!(!confirmed.ends_with(" a"));
            assert_eq!(format!("{confirmed}{partial}").matches('\n').count(), 1);
        }
        processor.update(&input, true).unwrap();
        assert_eq!(processor.buffered_words(), 0);
        assert_eq!(processor.parts().0.matches("voce").count(), 10_000);
    }

    #[test]
    fn a_long_false_start_keeps_the_surviving_word_mutable() {
        let input = format!("{}la scheda", "il ".repeat(100));
        assert_eq!(clean(&input), "la scheda");
    }
}
