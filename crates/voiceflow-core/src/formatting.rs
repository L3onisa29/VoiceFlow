use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, Default, Deserialize, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum FormatMode {
    #[default]
    Faithful,
    SpokenCommands,
}

/// Faithful mode never rewrites the speaker's words. Explicit commands are
/// opt-in; semantic formatting will be a separate, replaceable text processor.
pub fn format_transcript(text: &str, mode: FormatMode) -> String {
    let text = text.trim();
    match mode {
        FormatMode::Faithful => text.to_owned(),
        FormatMode::SpokenCommands => {
            let commands = [
                ("nuovo paragrafo", "\n\n"),
                ("new paragraph", "\n\n"),
                ("punto elenco", "\n• "),
                ("bullet point", "\n• "),
                ("a capo", "\n"),
                ("new line", "\n"),
            ];
            // Work on words so commands never replace a substring of a name.
            let words: Vec<_> = text.split_whitespace().collect();
            let mut out = String::new();
            let mut i = 0;
            while i < words.len() {
                let matched = commands.iter().find(|(command, _)| {
                    let parts: Vec<_> = command.split_whitespace().collect();
                    i + parts.len() <= words.len()
                        && parts.iter().enumerate().all(|(j, p)| {
                            words[i + j]
                                .trim_matches([',', '.', ':'])
                                .eq_ignore_ascii_case(p)
                        })
                });
                if let Some((command, replacement)) = matched {
                    while out.ends_with(' ') {
                        out.pop();
                    }
                    if replacement.starts_with('\n') && out.ends_with('\n') {
                        out.push_str(replacement.trim_start_matches('\n'));
                    } else {
                        out.push_str(replacement);
                    }
                    i += command.split_whitespace().count();
                } else {
                    if !out.is_empty() && !out.ends_with(['\n', ' ']) {
                        out.push(' ');
                    }
                    out.push_str(words[i]);
                    i += 1;
                }
            }
            out.trim().to_owned()
        }
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn faithful_mode_preserves_negation_numbers_and_uncertainty() {
        let text = "Non approvare i 1.200 euro. Forse venerdì, non lunedì.";
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
    }
}
