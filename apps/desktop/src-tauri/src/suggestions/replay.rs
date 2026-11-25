//! Replay of reviewed and proposed rules over the saved ASR originals, and the count of
//! rule applications recorded by new sessions. Dictation text is read, compared and handed
//! to the caller; only counts are ever stored.
use super::*;
use std::collections::{BTreeMap, HashMap};

/// Excerpts returned per rule. The count always covers every session.
const EXCERPTS: usize = 5;
/// Unchanged words kept on each side of a difference.
const CONTEXT_WORDS: usize = 6;

#[derive(Debug, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ReplayExcerpt {
    pub session_id: i64,
    pub before: String,
    pub after: String,
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RuleReplay {
    pub proposal_id: i64,
    /// Whether the rule is part of the current publication. For an active rule the
    /// comparison is "without it" against "as published"; otherwise "as published"
    /// against "with it added".
    pub active: bool,
    /// Sessions whose processed text differs. Absent when the rule set does not compile.
    pub changed_sessions: Option<u32>,
    pub excerpts: Vec<ReplayExcerpt>,
    pub error: Option<String>,
    /// Count stored when this rule was last published, if it was replayed then.
    pub at_publication: Option<PublicationReplay>,
    /// Applications recorded by sessions saved since the rule was approved.
    pub usage: Option<RuleUsage>,
}
#[derive(Debug, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct PublicationReplay {
    pub publication: i64,
    pub changed_sessions: u32,
    pub sessions: u32,
}
#[derive(Debug, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct RuleUsage {
    pub activations: u64,
    pub sessions_with_activation: u32,
    /// Sessions that record applications and were saved after the approval.
    pub tracked_sessions: u32,
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReplayReport {
    /// Sessions with a saved ASR original.
    pub sessions: u32,
    pub version: i64,
    pub rules: Vec<RuleReplay>,
}

pub(super) fn migrate(c: &Connection) -> Result<()> {
    // Additive: counts only, written with the publication they describe.
    c.execute_batch("CREATE TABLE IF NOT EXISTS suggestion_replays(publication INTEGER PRIMARY KEY, proposal_id INTEGER NOT NULL REFERENCES suggestions(id) ON DELETE CASCADE, changed_sessions INTEGER NOT NULL, sessions INTEGER NOT NULL);")?;
    Ok(())
}

pub(super) struct Original {
    id: i64,
    language: String,
    format: FormatMode,
    utterances: Vec<String>,
}
pub(super) fn originals(c: &Connection) -> Result<Vec<Original>> {
    c.prepare("SELECT id,language,original_asr_json,json_extract(processing_json,'$.format') FROM transcriptions WHERE original_asr_json IS NOT NULL ORDER BY id")?
        .query_map([], |r| {
            Ok((
                r.get::<_, i64>(0)?,
                r.get::<_, String>(1)?,
                r.get::<_, String>(2)?,
                r.get::<_, Option<String>>(3)?,
            ))
        })?
        .map(|row| {
            let (id, language, raw, format) = row?;
            Ok(Original {
                id,
                language,
                // Sessions saved without metadata are replayed as plain dictation.
                format: if format.as_deref() == Some("spoken-commands") {
                    FormatMode::SpokenCommands
                } else {
                    FormatMode::Faithful
                },
                utterances: serde_json::from_str(&raw)?,
            })
        })
        .collect()
}

/// One rule set compiled once per session language.
struct Compiled<'a> {
    dictionary: &'a DictionarySnapshot,
    rules: Vec<ApprovedRule>,
    by_language: HashMap<String, (Dictionary, CompiledCatalog)>,
}
impl<'a> Compiled<'a> {
    fn new(dictionary: &'a DictionarySnapshot, rules: Vec<ApprovedRule>) -> Self {
        Self {
            dictionary,
            rules,
            by_language: HashMap::new(),
        }
    }
    /// The same processor as live dictation, cleanup on so every rule can act.
    fn render(&mut self, original: &Original) -> Result<(String, BTreeMap<String, u32>)> {
        if !self.by_language.contains_key(&original.language) {
            let compiled = compile(self.dictionary, &self.rules, 1, &original.language)?;
            self.by_language.insert(original.language.clone(), compiled);
        }
        let (dictionary, cleanup) = &self.by_language[&original.language];
        let mut processor =
            TextProcessor::with_rules(original.format, true, dictionary.clone(), cleanup.clone());
        for utterance in &original.utterances {
            processor.update(utterance, true)?;
        }
        Ok((
            processor.parts().0.to_owned(),
            processor.rule_hits().clone(),
        ))
    }
}
/// The current publication, rendered at most once per session however many rules ask.
struct Published<'a> {
    compiled: Compiled<'a>,
    cache: Vec<Option<(String, BTreeMap<String, u32>)>>,
}
impl<'a> Published<'a> {
    fn new(dictionary: &'a DictionarySnapshot, active: &[ApprovedRule], sessions: usize) -> Self {
        Self {
            compiled: Compiled::new(dictionary, active.to_vec()),
            cache: vec![None; sessions],
        }
    }
    fn render(
        &mut self,
        index: usize,
        original: &Original,
    ) -> Result<&(String, BTreeMap<String, u32>)> {
        if self.cache[index].is_none() {
            self.cache[index] = Some(self.compiled.render(original)?);
        }
        Ok(self.cache[index].as_ref().unwrap())
    }
}
/// Keys under which the processor reports this rule. See `TextProcessor::rule_hits`.
fn hit_keys(id: i64) -> [String; 2] {
    [
        format!("dictionary:{}", -id),
        format!("cleanup:approved-{id}"),
    ]
}
fn fired(hits: &BTreeMap<String, u32>, id: i64) -> u32 {
    hit_keys(id).iter().filter_map(|key| hits.get(key)).sum()
}
fn excerpt(before: &str, after: &str) -> (String, String) {
    let (a, b): (Vec<&str>, Vec<&str>) = (
        before.split_whitespace().collect(),
        after.split_whitespace().collect(),
    );
    let prefix = a.iter().zip(&b).take_while(|(x, y)| x == y).count();
    let suffix = a[prefix..]
        .iter()
        .rev()
        .zip(b[prefix..].iter().rev())
        .take_while(|(x, y)| x == y)
        .count();
    let window = |words: &[&str]| {
        let start = prefix.saturating_sub(CONTEXT_WORDS);
        let end = (words.len() - suffix + CONTEXT_WORDS).min(words.len());
        format!(
            "{}{}{}",
            if start > 0 { "… " } else { "" },
            words[start..end].join(" "),
            if end < words.len() { " …" } else { "" }
        )
    };
    (window(&a), window(&b))
}

struct Outcome {
    changed: u32,
    excerpts: Vec<ReplayExcerpt>,
}
/// Effect of one rule against the current publication. A rule that never fires cannot
/// change a session, so the comparison run happens only where it fired.
fn effect(
    dictionary: &DictionarySnapshot,
    active: &[ApprovedRule],
    published: &mut Published<'_>,
    rule: &ApprovedRule,
    originals: &[Original],
    excerpts: usize,
) -> Result<Outcome> {
    let is_active = active.iter().any(|r| r.id == rule.id);
    let mut other = Compiled::new(
        dictionary,
        if is_active {
            active.iter().filter(|r| r.id != rule.id).cloned().collect()
        } else {
            active.iter().cloned().chain([rule.clone()]).collect()
        },
    );
    let mut outcome = Outcome {
        changed: 0,
        excerpts: vec![],
    };
    for (index, original) in originals.iter().enumerate() {
        // `with` contains the rule, `without` does not.
        let (with, without) = if is_active {
            let (with, hits) = published.render(index, original)?;
            if fired(hits, rule.id) == 0 {
                continue;
            }
            (with.clone(), other.render(original)?.0)
        } else {
            let (with, hits) = other.render(original)?;
            if fired(&hits, rule.id) == 0 {
                continue;
            }
            (with, published.render(index, original)?.0.clone())
        };
        if with == without {
            continue;
        }
        outcome.changed += 1;
        if outcome.excerpts.len() < excerpts {
            let (before, after) = excerpt(&without, &with);
            outcome.excerpts.push(ReplayExcerpt {
                session_id: original.id,
                before,
                after,
            });
        }
    }
    Ok(outcome)
}

/// Sessions changed by adding `rule` to the current publication, and the sessions replayed.
/// Runs inside the caller's transaction, before a publication is written.
pub(super) fn changed_by(
    c: &Connection,
    dictionary: &DictionarySnapshot,
    active: &[ApprovedRule],
    rule: &ApprovedRule,
) -> Result<(u32, u32)> {
    let originals = originals(c)?;
    let mut published = Published::new(dictionary, active, originals.len());
    let outcome = effect(dictionary, active, &mut published, rule, &originals, 0)?;
    Ok((outcome.changed, originals.len() as u32))
}
pub(super) fn record(
    c: &Connection,
    publication: i64,
    proposal: i64,
    (changed, sessions): (u32, u32),
) -> Result<()> {
    c.execute(
        "INSERT OR REPLACE INTO suggestion_replays VALUES(?1,?2,?3,?4)",
        params![publication, proposal, changed, sessions],
    )?;
    Ok(())
}

/// Hits recorded by each session that tracks them, with its creation time.
fn recorded_hits(c: &Connection) -> Result<Vec<(i64, BTreeMap<String, u32>)>> {
    c.prepare("SELECT created_at,json_extract(processing_json,'$.ruleHits') FROM transcriptions WHERE json_type(processing_json,'$.ruleHits')='object'")?
        .query_map([], |r| Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?)))?
        .map(|row| {
            let (created_at, hits) = row?;
            Ok((created_at, serde_json::from_str(&hits)?))
        })
        .collect()
}

impl SuggestionStore {
    /// Every proposal that is not rejected, replayed against the saved originals.
    /// Reads under the store lock, then computes without holding it.
    pub fn replay(&self) -> Result<ReplayReport> {
        self.replay_with(EXCERPTS)
    }
    fn replay_with(&self, excerpts: usize) -> Result<ReplayReport> {
        let (dictionary, version, active, targets, originals, recorded, stored) =
            self.access(|connection| {
                let tx = connection.transaction()?;
                let dictionary = crate::dictionary::read_schema_snapshot(&tx, "vocabulary")?;
                let version = current(&tx)?;
                let active = rules_at(&tx, version)?;
                let targets = tx
                    .prepare("SELECT id,candidate_json,approved_at FROM suggestions WHERE state<>'rejected' ORDER BY id DESC")?
                    .query_map([], |r| {
                        Ok((
                            r.get::<_, i64>(0)?,
                            r.get::<_, String>(1)?,
                            r.get::<_, Option<i64>>(2)?,
                        ))
                    })?
                    .collect::<rusqlite::Result<Vec<_>>>()?;
                let stored = tx
                    .prepare("SELECT proposal_id,publication,changed_sessions,sessions FROM suggestion_replays ORDER BY publication")?
                    .query_map([], |r| {
                        Ok((
                            r.get::<_, i64>(0)?,
                            PublicationReplay {
                                publication: r.get(1)?,
                                changed_sessions: r.get(2)?,
                                sessions: r.get(3)?,
                            },
                        ))
                    })?
                    .collect::<rusqlite::Result<HashMap<_, _>>>()?;
                let result = (
                    dictionary,
                    version,
                    active,
                    targets,
                    originals(&tx)?,
                    recorded_hits(&tx)?,
                    stored,
                );
                tx.commit()?;
                Ok(result)
            })?;
        let mut stored = stored;
        let mut published = Published::new(&dictionary, &active, originals.len());
        let mut rules = Vec::with_capacity(targets.len());
        for (id, json, approved_at) in targets {
            let rule = ApprovedRule {
                id,
                candidate: serde_json::from_str(&json)?,
            };
            let outcome = effect(
                &dictionary,
                &active,
                &mut published,
                &rule,
                &originals,
                excerpts,
            );
            let usage = approved_at.map(|approved_at| {
                let tracked: Vec<_> = recorded
                    .iter()
                    .filter(|(created_at, _)| *created_at >= approved_at)
                    .map(|(_, hits)| fired(hits, id))
                    .collect();
                RuleUsage {
                    activations: tracked.iter().map(|n| u64::from(*n)).sum(),
                    sessions_with_activation: tracked.iter().filter(|n| **n > 0).count() as u32,
                    tracked_sessions: tracked.len() as u32,
                }
            });
            let (changed_sessions, excerpts, error) = match outcome {
                Ok(outcome) => (Some(outcome.changed), outcome.excerpts, None),
                Err(error) => (None, vec![], Some(format!("{error:#}"))),
            };
            rules.push(RuleReplay {
                proposal_id: id,
                active: active.iter().any(|r| r.id == id),
                changed_sessions,
                excerpts,
                error,
                at_publication: stored.remove(&id),
                usage,
            });
        }
        Ok(ReplayReport {
            sessions: originals.len() as u32,
            version,
            rules,
        })
    }
}
#[tauri::command]
pub async fn suggestions_replay(
    store: tauri::State<'_, SuggestionStore>,
) -> Result<ReplayReport, String> {
    let store = store.inner().clone();
    tauri::async_runtime::spawn_blocking(move || store.replay())
        .await
        .map_err(|e| e.to_string())?
        .map_err(|e| format!("{e:#}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn excerpts_keep_bounded_context_around_the_difference() {
        let before = "uno due tre quattro cinque sei sette voice flow otto nove dieci undici dodici tredici quattordici";
        let after = "uno due tre quattro cinque sei sette VoiceFlow otto nove dieci undici dodici tredici quattordici";
        assert_eq!(
            excerpt(before, after),
            (
                "… due tre quattro cinque sei sette voice flow otto nove dieci undici dodici tredici …".into(),
                "… due tre quattro cinque sei sette VoiceFlow otto nove dieci undici dodici tredici …".into()
            )
        );
        assert_eq!(excerpt("a b", "b"), ("a b".into(), "b".into()));
        assert_eq!(excerpt("b", "b c"), ("b".into(), "b c".into()));
    }
    /// Local replay over a copy of a real archive, for a one-off review of the rules.
    /// The report contains dictation excerpts: the output path must be outside the repository.
    #[test]
    #[ignore = "Needs VOICEFLOW_REPLAY_DATA (directory with copies of history.sqlite3 and dictionary.sqlite3) and VOICEFLOW_REPLAY_OUT"]
    fn replay_a_copied_archive_into_a_private_report() {
        let data = std::path::PathBuf::from(std::env::var("VOICEFLOW_REPLAY_DATA").unwrap());
        let output = std::path::PathBuf::from(std::env::var("VOICEFLOW_REPLAY_OUT").unwrap());
        let repository = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .ancestors()
            .nth(3)
            .unwrap();
        assert!(
            !output.starts_with(repository) && !data.starts_with(repository),
            "Use a directory outside the repository"
        );
        let store = SuggestionStore::open(
            data.join("history.sqlite3"),
            data.join("dictionary.sqlite3"),
        );
        let started = std::time::Instant::now();
        let report = store.replay_with(12).unwrap();
        let elapsed = started.elapsed();
        // Read directly: the review snapshot hides hypotheses already handled locally.
        let candidates: HashMap<i64, (Candidate, String)> = store
            .access(|c| {
                c.prepare("SELECT id,candidate_json,CASE WHEN approved_at IS NULL THEN state ELSE 'approved' END FROM suggestions")?
                    .query_map([], |r| {
                        Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?, r.get::<_, String>(2)?))
                    })?
                    .map(|row| {
                        let (id, json, state) = row?;
                        Ok((id, (serde_json::from_str(&json)?, state)))
                    })
                    .collect()
            })
            .unwrap();
        let rules: Vec<_> = report
            .rules
            .iter()
            .map(|rule| {
                let (candidate, state) = &candidates[&rule.proposal_id];
                let state = if rule.active { "published" } else { state.as_str() };
                serde_json::json!({"id":rule.proposal_id,"state":state,"definition":candidate.definition,"language":candidate.language,"changedSessions":rule.changed_sessions,"error":rule.error,"excerpts":rule.excerpts})
            })
            .collect();
        std::fs::write(
            &output,
            serde_json::to_string_pretty(
                &serde_json::json!({"sessions":report.sessions,"version":report.version,"rules":rules}),
            )
            .unwrap(),
        )
        .unwrap();
        // Counts only on the console.
        println!(
            "replayed {} rules over {} sessions in {elapsed:?}; without effect: {}",
            report.rules.len(),
            report.sessions,
            report
                .rules
                .iter()
                .filter(|r| r.changed_sessions == Some(0))
                .count()
        );
    }
}
