//! "Wrong spelling -> intended term" pairs reported by the analysis. One batch rarely shows
//! a term twice, so pairs are remembered per session and judged on everything seen so far.
//! A spelling that is always flagged the same way becomes a dictionary variant by itself
//! when automatic approval is on, and a proposal to confirm otherwise.
//! Only the verified excerpt (lowercase) and the intended term are stored.
use super::*;
use std::collections::{BTreeMap, HashSet};
use voiceflow_core::dictionary::validate_rule;

/// Same threshold as automatic approval of findings: strictly above.
const HIGH_CONFIDENCE: u8 = 90;
/// A spelling that is itself an existing word needs this many agreeing sessions.
const REAL_WORD_SESSIONS: usize = 5;
/// Rule id used only to replay and validate a variant that is not a rule.
const TRIAL_RULE: i64 = i64::MAX;

pub(crate) fn migrate(c: &Connection) -> Result<()> {
    // Additive. Observations follow their session: deletion cascades, exclusion scrubs.
    c.execute_batch("CREATE TABLE IF NOT EXISTS analysis_spellings(id INTEGER PRIMARY KEY AUTOINCREMENT,spelling TEXT NOT NULL,intended TEXT NOT NULL,intended_key TEXT NOT NULL,session_id INTEGER NOT NULL REFERENCES transcriptions(id) ON DELETE CASCADE,confidence INTEGER NOT NULL CHECK(confidence BETWEEN 0 AND 100),real_word INTEGER NOT NULL,attempt_id INTEGER REFERENCES analysis_attempts(id) ON DELETE SET NULL,outcome TEXT NOT NULL DEFAULT 'observed',UNIQUE(spelling,intended_key,session_id));
        CREATE INDEX IF NOT EXISTS analysis_spellings_attempt ON analysis_spellings(attempt_id);
        CREATE TABLE IF NOT EXISTS analysis_spelling_checked(session_id INTEGER PRIMARY KEY REFERENCES transcriptions(id) ON DELETE CASCADE);
        CREATE TRIGGER IF NOT EXISTS analysis_spellings_exclude AFTER INSERT ON analysis_exclusions BEGIN
        DELETE FROM analysis_spellings WHERE session_id=NEW.session_id;
        DELETE FROM analysis_spelling_checked WHERE session_id=NEW.session_id;
        END;
        CREATE TABLE IF NOT EXISTS dictionary_auto_variants(spelling TEXT PRIMARY KEY,term TEXT NOT NULL,added_at INTEGER NOT NULL,changed_sessions INTEGER,sessions INTEGER,removed_at INTEGER);")?;
    Ok(())
}

/// Lowercase with single spaces: the form under which a spelling is accumulated and
/// matched, the same folding a case-insensitive dictionary variant uses.
fn fold(text: &str) -> String {
    text.split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .to_lowercase()
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Pair {
    text: String,
    intended: String,
    evidence: Vec<EvidenceRef>,
    confidence: u8,
    real_word: bool,
}
/// A pair that passed validation in this attempt. `literal` is kept in memory only, to
/// cite the excerpt exactly as the source spells it if a proposal is needed.
#[derive(Debug)]
pub(crate) struct Observed {
    spelling: String,
    literal: String,
    refs: Vec<EvidenceRef>,
}

/// What the stored observations allow for one spelling.
enum Decision {
    Outcome(&'static str),
    /// Eligible, but to be confirmed by a person: automatic approval is off, or the
    /// existing term has a shape an automatic variant must not inherit.
    Propose {
        intended: String,
        sessions: usize,
    },
}

fn trial_candidate(spelling: &str, intended: &str, sessions: usize) -> Candidate {
    Candidate {
        definition: Definition::Terminology {
            preferred: intended.to_owned(),
            variants: vec![spelling.to_owned()],
            matching: MatchMode::UnicodeLowercase,
            context: RuleContext::default(),
        },
        language: "*".into(),
        group_ids: vec![],
        reason: format!(
            "Grafia segnalata dall'analisi in {sessions} {}, sempre con lo stesso termine inteso.",
            if sessions == 1 {
                "sessione"
            } else {
                "sessioni"
            }
        ),
        positive_examples: vec![Example {
            input: spelling.to_owned(),
            output: intended.to_owned(),
        }],
        // The intended term itself must survive the rule: a variant contained in its own
        // replacement would apply again to already correct text.
        negative_examples: vec![Example {
            input: intended.to_owned(),
            output: intended.to_owned(),
        }],
    }
}

/// Produced text of every checked, non-excluded session, folded once per attempt.
fn checked_texts(c: &Connection, also: &[i64]) -> Result<Vec<(i64, String)>> {
    c.prepare("SELECT t.id,t.produced_text FROM transcriptions t WHERE (t.id IN (SELECT session_id FROM analysis_spelling_checked) OR t.id IN (SELECT value FROM json_each(?1))) AND NOT EXISTS(SELECT 1 FROM analysis_exclusions x WHERE x.session_id=t.id)")?
        .query_map([serde_json::to_string(also)?], |r| {
            Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?))
        })?
        .map(|row| {
            let (id, text) = row?;
            Ok((id, fold(&text)))
        })
        .collect()
}

fn decide(c: &Connection, spelling: &str, texts: &[(i64, String)]) -> Result<Decision> {
    let rows = c
        .prepare("SELECT intended,intended_key,session_id,confidence,real_word FROM analysis_spellings WHERE spelling=?1 ORDER BY confidence DESC,id DESC")?
        .query_map([spelling], |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, String>(1)?,
                r.get::<_, i64>(2)?,
                r.get::<_, u8>(3)?,
                r.get::<_, bool>(4)?,
            ))
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    let Some((intended, key, ..)) = rows.first().cloned() else {
        return Ok(Decision::Outcome("observed"));
    };
    // One spelling, one term.
    if rows.iter().any(|row| row.1 != key) {
        return Ok(Decision::Outcome("waiting-terms"));
    }
    let dictionary = crate::dictionary::read_schema_snapshot(c, "vocabulary")?;
    let holder = dictionary.term_of_variant(spelling);
    // Added automatically once and no longer there, by the removal action or by any
    // manual edit of the dictionary: a person took it out, it does not come back.
    let known: Option<Option<i64>> = c
        .query_row(
            "SELECT removed_at FROM dictionary_auto_variants WHERE spelling=?1",
            [spelling],
            |r| r.get(0),
        )
        .optional()?;
    if known.is_some_and(|removed| removed.is_some() || holder.is_none()) {
        return Ok(Decision::Outcome("blocked-removed"));
    }
    if let Some(term) = holder {
        return Ok(Decision::Outcome(if term.text.to_lowercase() == key {
            "already-active"
        } else {
            "blocked-other-term"
        }));
    }
    let active = rules_at(c, current(c)?)?;
    if active.iter().any(|rule| {
        matches!(&rule.candidate.definition, Definition::Terminology { variants, .. }
            if variants.iter().any(|variant| variant.to_lowercase() == spelling))
    }) {
        return Ok(Decision::Outcome("already-active"));
    }
    // Every checked session where the spelling stands as whole words must have flagged it.
    let flagged: HashSet<i64> = rows.iter().map(|row| row.2).collect();
    if texts
        .iter()
        .any(|(session, text)| !flagged.contains(session) && contains_example(text, spelling))
    {
        return Ok(Decision::Outcome("waiting-agreement"));
    }
    let certain: HashSet<i64> = rows
        .iter()
        .filter(|row| row.3 > HIGH_CONFIDENCE)
        .map(|row| row.2)
        .collect();
    let real_word = rows.iter().any(|row| row.4);
    if certain.len() < if real_word { REAL_WORD_SESSIONS } else { 1 } {
        return Ok(Decision::Outcome(if real_word {
            "waiting-sessions"
        } else {
            "waiting-confidence"
        }));
    }
    let candidate = trial_candidate(spelling, &intended, flagged.len());
    let rule = ApprovedRule {
        id: TRIAL_RULE,
        candidate,
    };
    if dictionary_rules(TRIAL_RULE, &rule.candidate)
        .iter()
        .any(|rule| validate_rule(rule).is_err())
        || validate(&rule.candidate, &dictionary).is_err()
    {
        return Ok(Decision::Outcome("blocked-validation"));
    }
    // The variant must not introduce a conflict or change any existing expectation. What
    // the published set already fails on its own is not held against it.
    let mut combined = active.clone();
    combined.push(rule.clone());
    let (Ok(before), Ok(after)) = (
        combination_failures(&dictionary, &active),
        combination_failures(&dictionary, &combined),
    ) else {
        return Ok(Decision::Outcome("blocked-conflict"));
    };
    let known: HashSet<String> = before.iter().map(|failure| failure.to_string()).collect();
    if after
        .iter()
        .any(|failure| !known.contains(&failure.to_string()))
    {
        return Ok(Decision::Outcome("blocked-conflict"));
    }
    let config = crate::analysis::settings(c)?.0;
    let automatic = config.enabled && config.auto_approve_high_confidence;
    if !automatic || dictionary.accepts_automatic_variant(&intended) == Some(false) {
        return Ok(Decision::Propose {
            intended,
            sessions: flagged.len(),
        });
    }
    // Shown with the variant: how many saved dictations it would have changed.
    let replayed = replay::changed_by(c, &dictionary, &active, &rule).ok();
    crate::dictionary::add_automatic_variant(c, "vocabulary", &intended, spelling)?;
    c.execute(
        "INSERT OR REPLACE INTO dictionary_auto_variants(spelling,term,added_at,changed_sessions,sessions,removed_at) VALUES(?1,?2,?3,?4,?5,NULL)",
        params![
            spelling,
            intended,
            now()?,
            replayed.map(|counts| counts.0),
            replayed.map(|counts| counts.1)
        ],
    )?;
    Ok(Decision::Outcome("added"))
}

impl SuggestionStore {
    /// Check one pair against the sources it cites and remember it for each session.
    pub(crate) fn observe_spelling(
        &self,
        value: serde_json::Value,
        batch_id: i64,
        attempt_id: i64,
    ) -> std::result::Result<Observed, IngestFailure> {
        (|| -> Result<Observed> {
            let pair: Pair = serde_json::from_value(value)
                .map_err(|e| CandidateRejection(anyhow::Error::new(coded("schema", e.to_string()))))?;
            let words = pair.text.split_whitespace().count();
            ensure!(
                (1..=4).contains(&words)
                    && pair.text.trim() == pair.text
                    && pair.text.chars().count() <= 80
                    && !pair.text.chars().any(char::is_control)
                    && !pair.text.contains(['<', '>']),
                reject(
                    "spelling-text",
                    "La grafia deve essere un estratto di 1–4 parole."
                )
            );
            let intended = pair.intended.trim().to_owned();
            ensure!(
                !intended.is_empty()
                    && intended.chars().count() <= 200
                    && !intended.chars().any(char::is_control)
                    && !intended.contains(['<', '>']),
                reject("spelling-intended", "Termine inteso non valido.")
            );
            ensure!(
                pair.text != intended,
                reject("rule-no-effect", "La grafia coincide con il termine inteso.")
            );
            ensure!(
                pair.confidence <= 100,
                reject("confidence-missing", "Confidenza richiesta.")
            );
            ensure!(
                !pair.evidence.is_empty() && pair.evidence.len() <= 64,
                reject("evidence-count", "Servono da 1 a 64 riferimenti reali.")
            );
            let spelling = fold(&pair.text);
            self.access(|connection| {
                let tx = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
                crate::analysis::validate_scope(&tx, batch_id, &pair.evidence)?;
                ensure!(tx.query_row("SELECT EXISTS(SELECT 1 FROM analysis_attempts WHERE id=?1 AND batch_id=?2 AND state='running')",params![attempt_id,batch_id],|r|r.get::<_,bool>(0))?,"Tentativo non attivo.");
                for reference in &pair.evidence {
                    let view = evidence(&tx, reference.clone())?;
                    ensure!(
                        contains_example(&view.before, &pair.text),
                        reject(
                            "excerpt-not-in-source",
                            "Estratto inventato o non presente nella fonte."
                        )
                    );
                }
                let sessions: HashSet<i64> = pair.evidence.iter().map(|r| r.session_id).collect();
                for session in sessions {
                    tx.execute(
                        "INSERT INTO analysis_spellings(spelling,intended,intended_key,session_id,confidence,real_word,attempt_id) VALUES(?1,?2,?3,?4,?5,?6,?7)
                         ON CONFLICT(spelling,intended_key,session_id) DO UPDATE SET confidence=MAX(confidence,excluded.confidence),real_word=MAX(real_word,excluded.real_word),attempt_id=excluded.attempt_id,outcome='observed'",
                        params![spelling, intended, intended.to_lowercase(), session, pair.confidence, pair.real_word, attempt_id],
                    )?;
                }
                tx.commit()?;
                Ok(())
            })?;
            Ok(Observed {
                spelling,
                literal: pair.text,
                refs: pair.evidence,
            })
        })()
        .map_err(IngestFailure::classify)
    }

    /// Judge the spellings seen in this attempt on everything accumulated so far.
    /// `checked` are the sessions this attempt examined, counted as checked already.
    pub(crate) fn settle_spellings(
        &self,
        observed: &[Observed],
        batch_id: i64,
        attempt_id: i64,
        checked: &[i64],
        cancel: &dyn crate::analysis::Cancellation,
    ) -> std::result::Result<(), IngestFailure> {
        if observed.is_empty() {
            return Ok(());
        }
        let infrastructure = |_| IngestFailure::Infrastructure;
        let texts = self
            .access(|connection| checked_texts(connection, checked))
            .map_err(infrastructure)?;
        let mut by_spelling: BTreeMap<&str, Vec<&Observed>> = BTreeMap::new();
        for pair in observed {
            by_spelling.entry(&pair.spelling).or_default().push(pair);
        }
        for (spelling, pairs) in by_spelling {
            if cancel.cancelled() {
                break;
            }
            let decision = self
                .access(|connection| {
                    let tx =
                        connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
                    let decision = decide(&tx, spelling, &texts)?;
                    tx.commit()?;
                    Ok(decision)
                })
                .map_err(infrastructure)?;
            let outcome = match decision {
                Decision::Outcome(outcome) => outcome,
                Decision::Propose { intended, sessions } => {
                    let mut candidate = trial_candidate(spelling, &intended, sessions);
                    // Cite the excerpt as each source spells it; matching stays case-insensitive.
                    let mut literals: Vec<&str> =
                        pairs.iter().map(|p| p.literal.as_str()).collect();
                    literals.sort_unstable();
                    literals.dedup();
                    candidate.positive_examples = literals
                        .into_iter()
                        .take(16)
                        .map(|literal| Example {
                            input: literal.to_owned(),
                            output: intended.clone(),
                        })
                        .collect();
                    let mut refs: Vec<EvidenceRef> = vec![];
                    for reference in pairs.iter().flat_map(|p| &p.refs) {
                        if !refs.contains(reference) && refs.len() < 64 {
                            refs.push(reference.clone());
                        }
                    }
                    match self.ingest_prepared(
                        IngestRequest {
                            candidate,
                            evidence: refs,
                        },
                        batch_id,
                        attempt_id,
                    ) {
                        Ok(()) => "proposed",
                        Err(IngestFailure::InvalidCandidate(_)) => "blocked-validation",
                        Err(IngestFailure::Infrastructure) => {
                            return Err(IngestFailure::Infrastructure);
                        }
                    }
                }
            };
            self.access(|connection| {
                connection.execute(
                    "UPDATE analysis_spellings SET outcome=?1 WHERE spelling=?2 AND attempt_id=?3",
                    params![outcome, spelling, attempt_id],
                )?;
                Ok(())
            })
            .map_err(infrastructure)?;
        }
        Ok(())
    }

    /// Variants added automatically that are still in the dictionary.
    pub fn auto_variants(&self) -> Result<Vec<AutoVariant>> {
        self.access(|connection| {
            let tx = connection.transaction()?;
            let result = read_auto_variants(&tx)?;
            tx.commit()?;
            Ok(result)
        })
    }
    /// One action: the variant leaves the dictionary and never comes back by itself.
    pub fn remove_auto_variant(&self, spelling: &str) -> Result<Vec<AutoVariant>> {
        let spelling = fold(spelling);
        self.access(|connection| {
            let tx = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
            ensure!(
                tx.execute(
                    "UPDATE dictionary_auto_variants SET removed_at=?1 WHERE spelling=?2 AND removed_at IS NULL",
                    params![now()?, spelling],
                )? == 1,
                "Variante automatica non più presente. Aggiorna il dizionario."
            );
            crate::dictionary::remove_variant(&tx, "vocabulary", &spelling)?;
            let result = read_auto_variants(&tx)?;
            tx.commit()?;
            Ok(result)
        })
    }
}

#[derive(Debug, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct AutoVariant {
    pub spelling: String,
    pub term_id: i64,
    pub term: String,
    pub added_at: i64,
    /// Sessions still in the archive in which the analysis flagged this spelling.
    pub seen_sessions: u32,
    /// Replay over the saved originals at the time it was added.
    pub changed_sessions: Option<u32>,
    pub replayed_sessions: Option<u32>,
}
fn read_auto_variants(c: &Connection) -> Result<Vec<AutoVariant>> {
    let dictionary = crate::dictionary::read_schema_snapshot(c, "vocabulary")?;
    let rows = c
        .prepare("SELECT a.spelling,a.added_at,a.changed_sessions,a.sessions,(SELECT COUNT(DISTINCT s.session_id) FROM analysis_spellings s WHERE s.spelling=a.spelling) FROM dictionary_auto_variants a WHERE a.removed_at IS NULL ORDER BY a.added_at DESC,a.spelling")?
        .query_map([], |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, i64>(1)?,
                r.get::<_, Option<u32>>(2)?,
                r.get::<_, Option<u32>>(3)?,
                r.get::<_, u32>(4)?,
            ))
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    Ok(rows
        .into_iter()
        .filter_map(
            |(spelling, added_at, changed_sessions, replayed_sessions, seen_sessions)| {
                // Taken out by a manual edit: no longer listed, and vetoed from now on.
                let term = dictionary.term_of_variant(&spelling)?;
                Some(AutoVariant {
                    term_id: term.id,
                    term: term.text.clone(),
                    spelling,
                    added_at,
                    seen_sessions,
                    changed_sessions,
                    replayed_sessions,
                })
            },
        )
        .collect())
}

/// Sessions of an attempt that were examined for misspellings.
pub(crate) fn mark_checked(c: &Connection, sessions: &[i64]) -> Result<()> {
    c.execute(
        "INSERT OR IGNORE INTO analysis_spelling_checked SELECT t.id FROM transcriptions t WHERE t.id IN (SELECT value FROM json_each(?1)) AND NOT EXISTS(SELECT 1 FROM analysis_exclusions x WHERE x.session_id=t.id)",
        [serde_json::to_string(sessions)?],
    )?;
    Ok(())
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SpellingResult {
    pub spelling: String,
    pub intended: String,
    pub session_ids: Vec<i64>,
    pub confidence: u8,
    pub real_word: bool,
    pub outcome: String,
}
/// Pairs remembered by one attempt, for its detail page.
pub(crate) fn read(c: &Connection, attempt: Option<i64>) -> Result<Vec<SpellingResult>> {
    let rows = c
        .prepare("SELECT spelling,intended,session_id,confidence,real_word,outcome FROM analysis_spellings WHERE attempt_id=?1 ORDER BY spelling,intended_key,session_id")?
        .query_map([attempt], |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, String>(1)?,
                r.get::<_, i64>(2)?,
                r.get::<_, u8>(3)?,
                r.get::<_, bool>(4)?,
                r.get::<_, String>(5)?,
            ))
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    let mut result: Vec<SpellingResult> = vec![];
    for (spelling, intended, session, confidence, real_word, outcome) in rows {
        match result.last_mut() {
            Some(last)
                if last.spelling == spelling
                    && last.intended.to_lowercase() == intended.to_lowercase() =>
            {
                last.session_ids.push(session);
                last.confidence = last.confidence.max(confidence);
                last.real_word |= real_word;
            }
            _ => result.push(SpellingResult {
                spelling,
                intended,
                session_ids: vec![session],
                confidence,
                real_word,
                outcome,
            }),
        }
    }
    Ok(result)
}

#[tauri::command]
pub async fn dictionary_auto_variants(
    store: tauri::State<'_, SuggestionStore>,
) -> Result<Vec<AutoVariant>, String> {
    let store = store.inner().clone();
    tauri::async_runtime::spawn_blocking(move || store.auto_variants())
        .await
        .map_err(|e| e.to_string())?
        .map_err(|e| format!("{e:#}"))
}
#[tauri::command]
pub async fn dictionary_remove_auto_variant(
    store: tauri::State<'_, SuggestionStore>,
    spelling: String,
) -> Result<Vec<AutoVariant>, String> {
    let store = store.inner().clone();
    tauri::async_runtime::spawn_blocking(move || store.remove_auto_variant(&spelling))
        .await
        .map_err(|e| e.to_string())?
        .map_err(|e| format!("{e:#}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn folding_matches_case_insensitive_variants() {
        assert_eq!(fold("  Tes   Flight\n"), "tes flight");
        assert!(contains_example(
            &fold("Apri Tes  Flight, poi"),
            "tes flight"
        ));
        assert!(!contains_example(&fold("le tasche piene"), "tasch"));
    }
    /// Whether automatic variants can pass their checks against a real dictionary and the
    /// rules published with it. Works on copies and writes nothing but its report.
    #[test]
    #[ignore = "Needs VOICEFLOW_REPLAY_DATA (copies of history.sqlite3 and dictionary.sqlite3) and VOICEFLOW_REPLAY_OUT, both outside the repository"]
    fn automatic_variant_checks_on_a_copied_archive() {
        let data = std::path::PathBuf::from(std::env::var("VOICEFLOW_REPLAY_DATA").unwrap());
        let output = std::path::PathBuf::from(std::env::var("VOICEFLOW_REPLAY_OUT").unwrap());
        let repository = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .ancestors()
            .nth(3)
            .unwrap();
        assert!(!output.starts_with(repository) && !data.starts_with(repository));
        let store = SuggestionStore::open(
            data.join("history.sqlite3"),
            data.join("dictionary.sqlite3"),
        );
        let report = store
            .access(|connection| {
                let tx = connection.transaction()?;
                let dictionary = crate::dictionary::read_schema_snapshot(&tx, "vocabulary")?;
                let active = rules_at(&tx, current(&tx)?)?;
                let started = std::time::Instant::now();
                let existing = validate_combination(&dictionary, &active);
                let existing_ms = started.elapsed().as_millis();
                let accepting = dictionary
                    .terms
                    .iter()
                    .filter(|term| dictionary.accepts_automatic_variant(&term.text) == Some(true))
                    .count();
                // A spelling that cannot occur, towards a new term and towards an existing one.
                let mut trials = vec![];
                for intended in [
                    "Zzqxterm",
                    dictionary
                        .terms
                        .first()
                        .map_or("Zzqxterm", |t| t.text.as_str()),
                ] {
                    let rule = ApprovedRule {
                        id: TRIAL_RULE,
                        candidate: trial_candidate("zzqx prova", intended, 1),
                    };
                    let started = std::time::Instant::now();
                    let own = validate(&rule.candidate, &dictionary);
                    let mut combined = active.clone();
                    combined.push(rule.clone());
                    let before = combination_failures(&dictionary, &active)?;
                    let combination = combination_failures(&dictionary, &combined).map(|after| {
                        after
                            .into_iter()
                            .map(|failure| failure.to_string())
                            .filter(|failure| {
                                !before.iter().any(|known| known.to_string() == *failure)
                            })
                            .collect::<Vec<_>>()
                    });
                    let replayed = replay::changed_by(&tx, &dictionary, &active, &rule);
                    trials.push(serde_json::json!({
                        "existingTerm": intended != "Zzqxterm",
                        "ownError": own.err().map(|e| format!("{e:#}")),
                        "newFailures": combination.as_ref().ok(),
                        "combinationError": combination.as_ref().err().map(|e| format!("{e:#}")),
                        "replay": replayed.ok(),
                        "ms": started.elapsed().as_millis() as u64,
                    }));
                }
                Ok(serde_json::json!({
                    "dictionaryRevision": dictionary.version,
                    "terms": dictionary.terms.len(),
                    "variants": dictionary.terms.iter().map(|t| t.rule_variants()).sum::<usize>(),
                    "termsAcceptingAutomaticVariants": accepting,
                    "dictionaryConflicts": dictionary.conflicts.len(),
                    "activeRules": active.len(),
                    "publishedCombinationError": existing.err().map(|e| format!("{e:#}")),
                    "publishedFailures": combination_failures(&dictionary, &active)?
                        .iter()
                        .map(|failure| failure.to_string())
                        .collect::<std::collections::BTreeSet<_>>(),
                    "publishedCombinationMs": existing_ms as u64,
                    "trials": trials,
                }))
            })
            .unwrap();
        std::fs::write(&output, serde_json::to_string_pretty(&report).unwrap()).unwrap();
        println!(
            "terms {} accepting {} conflicts {} rules {} published-ok {} published-failures {} trials-ok {}",
            report["terms"],
            report["termsAcceptingAutomaticVariants"],
            report["dictionaryConflicts"],
            report["activeRules"],
            report["publishedCombinationError"].is_null(),
            report["publishedFailures"].as_array().unwrap().len(),
            report["trials"]
                .as_array()
                .unwrap()
                .iter()
                .filter(|t| {
                    t["ownError"].is_null()
                        && t["newFailures"]
                            .as_array()
                            .is_some_and(|new| new.is_empty())
                })
                .count()
        );
    }
}
