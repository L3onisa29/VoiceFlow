//! Synthetic quality measurement through the real streaming TextProcessor.
use anyhow::{Result, ensure};
use serde::Deserialize;
use serde_json::json;
use std::{collections::BTreeSet, time::Instant};
use voiceflow_core::{
    dictionary::{Context, Dictionary, MatchMode, Rule},
    formatting::{FormatMode, TextProcessor},
};
#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Corpus {
    version: String,
    provenance: String,
    rules: Vec<Term>,
    sessions: Vec<Session>,
}
#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Term {
    variant: String,
    preferred: String,
    proposal_session: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Session {
    id: String,
    split: String,
    language: String,
    tags: Vec<String>,
    input: String,
    truth: String,
    snapshots: Option<Vec<String>>,
    repeat: Option<usize>,
}
// Whitespace is a token, so commands/newlines and punctuation are scored too.
fn tokens(s: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut current = String::new();
    let mut whitespace = None;
    for c in s.chars() {
        let ws = c.is_whitespace();
        if whitespace.is_some_and(|v| v != ws) {
            out.push(std::mem::take(&mut current));
        }
        current.push(c);
        whitespace = Some(ws);
    }
    if !current.is_empty() {
        out.push(current);
    }
    out
}
#[derive(Debug, PartialEq, Eq, PartialOrd, Ord)]
struct Edit {
    offset: usize,
    // Distinguish repeated inserted tokens at one source offset.
    insertion_ordinal: usize,
    remove: Option<String>,
    insert: Option<String>,
}
// Deterministic minimum edit alignment against the same baseline. Exact matching
// edits count as useful. Other applied edits count as false, conservatively.
fn edits(from: &str, to: &str) -> BTreeSet<Edit> {
    let a = tokens(from);
    let b = tokens(to);
    let mut d = vec![vec![0; b.len() + 1]; a.len() + 1];
    for (i, row) in d.iter_mut().enumerate() {
        row[0] = i;
    }
    for (j, cell) in d[0].iter_mut().enumerate() {
        *cell = j;
    }
    for i in 1..=a.len() {
        for j in 1..=b.len() {
            d[i][j] = (d[i - 1][j] + 1)
                .min(d[i][j - 1] + 1)
                .min(d[i - 1][j - 1] + usize::from(a[i - 1] != b[j - 1]));
        }
    }
    let (mut i, mut j) = (a.len(), b.len());
    let mut result = BTreeSet::new();
    while i > 0 || j > 0 {
        if i > 0 && j > 0 && d[i][j] == d[i - 1][j - 1] + usize::from(a[i - 1] != b[j - 1]) {
            if a[i - 1] != b[j - 1] {
                result.insert(Edit {
                    offset: i - 1,
                    insertion_ordinal: 0,
                    remove: Some(a[i - 1].clone()),
                    insert: Some(b[j - 1].clone()),
                });
            }
            i -= 1;
            j -= 1;
        } else if i > 0 && d[i][j] == d[i - 1][j] + 1 {
            result.insert(Edit {
                offset: i - 1,
                insertion_ordinal: 0,
                remove: Some(a[i - 1].clone()),
                insert: None,
            });
            i -= 1;
        } else {
            let insertion_ordinal = result
                .iter()
                .filter(|e| e.offset == i && e.remove.is_none())
                .count();
            result.insert(Edit {
                offset: i,
                insertion_ordinal,
                remove: None,
                insert: Some(b[j - 1].clone()),
            });
            j -= 1;
        }
    }
    result
}
fn render(
    c: &Corpus,
    s: &Session,
    terminology: bool,
    cleanup: bool,
) -> Result<(String, f64, usize)> {
    let dictionary = Dictionary::compile(if terminology {
        c.rules
            .iter()
            .enumerate()
            .map(|(i, r)| Rule {
                id: i as i64,
                variant: r.variant.clone(),
                preferred: r.preferred.clone(),
                mode: MatchMode::Exact,
                context: Context::default(),
                scope: 0,
            })
            .collect()
    } else {
        vec![]
    })?;
    let mut p = TextProcessor::with_dictionary(FormatMode::SpokenCommands, cleanup, dictionary);
    let input = std::iter::repeat_n(s.input.as_str(), s.repeat.unwrap_or(1))
        .collect::<Vec<_>>()
        .join(" ");
    let snapshots = if let Some(v) = &s.snapshots {
        v.clone()
    } else {
        input
            .char_indices()
            .filter(|(_, c)| c.is_whitespace())
            .map(|(i, _)| input[..i + 1].to_owned())
            .collect()
    };
    let start = Instant::now();
    let mut max = 0;
    for snapshot in &snapshots {
        p.update(snapshot, false)?;
        max = max.max(p.retained_words());
    }
    p.update(&input, true)?;
    let elapsed = start.elapsed().as_secs_f64() * 1e6;
    ensure!(p.retained_words() == 0, "Pending source after finalization");
    Ok((p.parts().0.to_owned(), elapsed, max))
}
fn main() -> Result<()> {
    let path = std::env::args()
        .nth(1)
        .unwrap_or_else(|| "crates/voiceflow-core/tests/fixtures/quality-v1.json".into());
    let c: Corpus = serde_json::from_slice(&std::fs::read(path)?)?;
    validate(&c)?;
    // Apply the same frozen rules to decoder transcripts, without scoring them
    // as text fixtures. The experiment runner owns audio reference scoring.
    if let Some(path) = std::env::args().nth(2) {
        for line in std::fs::read_to_string(path)?.lines() {
            let mut row: serde_json::Value = serde_json::from_str(line)?;
            let input = row["text"]
                .as_str()
                .ok_or_else(|| anyhow::anyhow!("Missing text"))?
                .to_owned();
            let s = Session {
                id: String::new(),
                split: "evaluation".into(),
                language: "en".into(),
                tags: vec![],
                input,
                truth: String::new(),
                snapshots: None,
                repeat: None,
            };
            row["corrected"] = render(&c, &s, true, true)?.0.into();
            println!("{row}");
        }
        return Ok(());
    }
    println!(
        "{}",
        json!({"event":"metadata","version":c.version,"provenance":c.provenance,"samples":c.sessions.len(),"latencyScope":"processor updates+finalize only; excludes snapshot allocation, rule compilation, decoder and edit scoring","measurementRepeats":5})
    );
    for s in &c.sessions {
        let (baseline, _, _) = render(&c, s, false, false)?;
        let truth = std::iter::repeat_n(s.truth.as_str(), s.repeat.unwrap_or(1))
            .collect::<Vec<_>>()
            .join(" ");
        let wanted = edits(&baseline, &truth);
        for (label, term, clean) in [
            ("off", false, false),
            ("terminology", true, false),
            ("cleanup", false, true),
            ("combined", true, true),
        ] {
            let mut times = Vec::new();
            let mut output = String::new();
            let mut retained = 0; // One untimed warmup per case, then five measured runs.
            render(&c, s, term, clean)?;
            for _ in 0..5 {
                let (text, us, max) = render(&c, s, term, clean)?;
                output = text;
                times.push(us);
                retained = retained.max(max);
            }
            times.sort_by(f64::total_cmp);
            let applied = edits(&baseline, &output);
            println!(
                "{}",
                json!({"event":"session","id":s.id,"split":s.split,"language":s.language,"tags":s.tags,"mode":label,"usefulCorrections":applied.intersection(&wanted).count(),"falseChanges":applied.difference(&wanted).count(),"residualEdits":edits(&output,&truth).len(),"exact":output==truth,"medianProcessorUs":times[2],"maxProcessorUs":times[4],"maxRetainedWords":retained,"output":output})
            );
        }
    }
    Ok(())
}
fn validate(c: &Corpus) -> Result<()> {
    ensure!(
        (20..=30).contains(&c.sessions.len()),
        "Expected 20–30 sessions"
    );
    let ids = c
        .sessions
        .iter()
        .map(|s| s.id.as_str())
        .collect::<BTreeSet<_>>();
    ensure!(ids.len() == c.sessions.len(), "Duplicate session ID");
    for s in &c.sessions {
        ensure!(
            ["proposal", "calibration", "evaluation"].contains(&s.split.as_str()),
            "Invalid split"
        );
        ensure!(
            ["it", "en"].contains(&s.language.as_str()),
            "Invalid language"
        );
        ensure!(s.repeat.unwrap_or(1) > 0, "Empty repeat");
        ensure!(
            s.repeat.is_none() || s.snapshots.is_none(),
            "Snapshots plus repeat unsupported"
        );
        if let Some(snapshots) = &s.snapshots {
            ensure!(
                snapshots.last() == Some(&s.input),
                "Last snapshot must equal input"
            );
        }
    }
    for r in &c.rules {
        ensure!(
            c.sessions
                .iter()
                .any(|s| s.id == r.proposal_session && s.split == "proposal"),
            "Rule requires proposal-only provenance"
        );
    }
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn edit_scoring_protects_controls_and_counts_residuals() {
        assert!(edits("no no 42", "no no 42").is_empty());
        assert_eq!(edits("alpha beta", "Alpha beta").len(), 1);
        let desired = edits("alpha beta", "Alpha beta");
        let actual = edits("alpha beta", "Alpha Beta");
        assert_eq!(actual.intersection(&desired).count(), 1);
        assert_eq!(actual.difference(&desired).count(), 1);
        assert_eq!(edits("hello world", "hello\nworld").len(), 1);
        assert_eq!(edits("", "x x x").len(), 5);
        // Replacing an error with another error is a false change, even when
        // the distance to truth is unchanged.
        let wanted = edits("wrong", "right");
        let applied = edits("wrong", "other");
        assert_eq!(applied.intersection(&wanted).count(), 0);
        assert_eq!(applied.difference(&wanted).count(), 1);
    }
    #[test]
    fn fixture_has_disjoint_ids_and_proposal_provenance() {
        let c: Corpus =
            serde_json::from_str(include_str!("../../tests/fixtures/quality-v1.json")).unwrap();
        validate(&c).unwrap();
    }
}
