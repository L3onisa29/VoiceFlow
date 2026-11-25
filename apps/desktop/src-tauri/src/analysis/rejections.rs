//! Why a candidate was rejected, and what the queue does about a batch that keeps failing.
//! Rows hold our own codes and source references only, never provider or dictation text.
use super::*;

/// Every code the validators can emit, with the instruction sent back to the model.
const CODES: &[(&str, &str)] = &[
    (
        "schema",
        "The finding did not match the output schema. Emit every required field with the exact types.",
    ),
    (
        "confidence-missing",
        "confidence must be an integer from 0 to 100.",
    ),
    (
        "evidence-count",
        "Cite between 1 and 64 evidence references.",
    ),
    (
        "evidence-reference",
        "An evidence reference set both revisionId and originalIndex. Use at most one of them.",
    ),
    (
        "evidence-outside-batch",
        "An evidence reference pointed outside the supplied sources. Cite only sessionId and revisionId pairs present in sources.",
    ),
    (
        "batch-inactive",
        "The batch was no longer active when the finding was checked.",
    ),
    ("source-unavailable", "A cited source no longer exists."),
    (
        "source-changed",
        "A cited source changed, or the reference did not match one of its views. Use originalIndex only for an existing originalAsr segment.",
    ),
    ("session-missing", "A cited session does not exist."),
    (
        "revision-foreign",
        "A cited revisionId does not belong to its sessionId.",
    ),
    (
        "revision-not-confirmed",
        "A cited revision is not a remembered human correction.",
    ),
    (
        "asr-unavailable",
        "originalIndex was used for a session without originalAsr.",
    ),
    (
        "segment-missing",
        "originalIndex pointed past the last originalAsr segment. Indexes are zero-based.",
    ),
    (
        "finding-text",
        "name, text or reason was empty, too long or contained control characters.",
    ),
    (
        "action-rule-mismatch",
        "action and candidate disagreed: keep needs candidate null, remove needs a cleanup rule, replace needs a terminology rule.",
    ),
    (
        "excerpt-not-in-source",
        "text was not an exact whole-word excerpt of every cited source view. Copy it verbatim, or cite only the sources that contain it.",
    ),
    (
        "keep-contradicts-correction",
        "A keep finding cited a human correction that changed the excerpt.",
    ),
    (
        "rule-no-effect",
        "Applying the rule to text changed nothing. The variant and its before/after context must match the excerpt itself.",
    ),
    (
        "rule-changes-context",
        "Applying the rule changed the context words around the excerpt.",
    ),
    (
        "example-not-grounded",
        "No positive example occurs verbatim in the cited source with text inside it and the rule's result as its output.",
    ),
    (
        "result-contradicts-correction",
        "The rule's result does not appear in the cited human correction.",
    ),
    (
        "evidence-lacks-example",
        "A cited source does not contain any positive example input verbatim, or its correction lacks the example output.",
    ),
    (
        "example-mismatch",
        "Applying the rule to an example input did not give the stated output. Simulate the replacement on every positive and negative example.",
    ),
    (
        "examples-count",
        "Supply between 1 and 16 positive and 1 and 16 negative examples.",
    ),
    (
        "example-text",
        "An example was empty, too long or contained control characters.",
    ),
    ("example-markers", "Examples must not contain < or >."),
    (
        "negative-example-changed",
        "A negative example must have identical input and output.",
    ),
    (
        "positive-example-unchanged",
        "A positive example must change its input.",
    ),
    (
        "variants-count",
        "A terminology rule needs between 1 and 32 explicit variants.",
    ),
    (
        "reason-text",
        "The candidate reason was empty, too long or contained control characters.",
    ),
    (
        "spelling-text",
        "A misspelling text must be an exact excerpt of 1 to 4 words, without markers.",
    ),
    (
        "spelling-intended",
        "A misspelling needs a non-empty intended term different from its text.",
    ),
    ("language", "language must be \"*\", \"it\" or \"en\"."),
    (
        "groups",
        "groupIds must reference existing groups, or be empty.",
    ),
    (
        "rule-invalid",
        "The rule definition was not accepted by the local compiler. Keep the variant plus adjacent context within seven words and context values to a single word.",
    ),
];
/// Sent only with a retry, so a first attempt keeps the whole byte budget for sources.
pub(super) const RETRY_INSTRUCTION: &str = "previousAttemptRejections lists local validation codes for findings rejected in the previous attempt on these same sources, with their meaning and the sessions they cited. Do not repeat those mistakes: fix the finding exactly as the meaning says, or omit it if you cannot. It is feedback, not evidence.";
pub(crate) fn hint(code: &str) -> Option<&'static str> {
    CODES
        .iter()
        .find(|(known, _)| *known == code)
        .map(|(_, hint)| *hint)
}

pub(super) fn migrate(c: &Connection) -> Result<()> {
    // Additive tables: an older build opening this history ignores them.
    c.execute_batch("CREATE TABLE IF NOT EXISTS analysis_attempt_rejections(id INTEGER PRIMARY KEY AUTOINCREMENT,attempt_id INTEGER NOT NULL REFERENCES analysis_attempts(id) ON DELETE CASCADE,kind TEXT NOT NULL DEFAULT 'candidate',position INTEGER NOT NULL,code TEXT NOT NULL,UNIQUE(attempt_id,kind,position));
        CREATE TABLE IF NOT EXISTS analysis_rejection_sources(rejection_id INTEGER NOT NULL REFERENCES analysis_attempt_rejections(id) ON DELETE CASCADE,event_id INTEGER NOT NULL REFERENCES analysis_events(id) ON DELETE CASCADE,PRIMARY KEY(rejection_id,event_id));
        CREATE TABLE IF NOT EXISTS analysis_parked(event_id INTEGER PRIMARY KEY REFERENCES analysis_events(id) ON DELETE CASCADE,batch_id INTEGER NOT NULL,parked_at INTEGER NOT NULL);")?;
    Ok(())
}

/// One rejected candidate. `cited` keeps only references that resolve to a batch source.
#[derive(Debug)]
pub(super) struct Rejection {
    /// `candidate` for a finding, `misspelling` for a spelling pair. Each list is
    /// numbered on its own.
    pub kind: &'static str,
    pub position: usize,
    pub code: &'static str,
    pub cited: Vec<i64>,
}
impl Rejection {
    /// References are read structurally. Nothing else of the provider value is retained.
    pub fn new(
        position: usize,
        code: &'static str,
        value: &serde_json::Value,
        sources: &[Source],
    ) -> Self {
        let mut cited: Vec<i64> = value
            .get("evidence")
            .and_then(serde_json::Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(|reference| {
                let session = reference.get("sessionId")?.as_i64()?;
                let revision = reference.get("revisionId").and_then(|v| v.as_i64());
                sources
                    .iter()
                    .find(|s| s.session_id == session && s.revision_id == revision)
                    .map(|s| s.event_id)
            })
            .collect();
        cited.sort_unstable();
        cited.dedup();
        Self {
            kind: "candidate",
            position,
            code,
            cited,
        }
    }
    pub fn spelling(
        position: usize,
        code: &'static str,
        value: &serde_json::Value,
        sources: &[Source],
    ) -> Self {
        Self {
            kind: "misspelling",
            ..Self::new(position, code, value, sources)
        }
    }
}
pub(super) fn save(c: &Connection, attempt: i64, rejections: &[Rejection]) -> Result<()> {
    for rejection in rejections {
        c.execute(
            "INSERT OR IGNORE INTO analysis_attempt_rejections(attempt_id,kind,position,code) VALUES(?1,?2,?3,?4)",
            params![attempt, rejection.kind, rejection.position as i64, rejection.code],
        )?;
        let id: i64 = c.query_row(
            "SELECT id FROM analysis_attempt_rejections WHERE attempt_id=?1 AND kind=?2 AND position=?3",
            params![attempt, rejection.kind, rejection.position as i64],
            |r| r.get(0),
        )?;
        for event in &rejection.cited {
            // A source deleted while the provider was running has no event left to cite.
            c.execute(
                "INSERT OR IGNORE INTO analysis_rejection_sources SELECT ?1,id FROM analysis_events WHERE id=?2",
                params![id, event],
            )?;
        }
    }
    Ok(())
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RejectionResult {
    /// `candidate` or `misspelling`.
    pub kind: String,
    /// One-based position within its list in the provider reply.
    pub position: i64,
    pub code: String,
    pub sources: Vec<RejectionSource>,
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RejectionSource {
    pub session_id: i64,
    pub revision_id: Option<i64>,
}
pub(super) fn read(c: &Connection, attempt: Option<i64>) -> Result<Vec<RejectionResult>> {
    let rows = c
        .prepare("SELECT id,position,code,kind FROM analysis_attempt_rejections WHERE attempt_id=?1 ORDER BY kind,position")?
        .query_map([attempt], |r| {
            Ok((
                r.get::<_, i64>(0)?,
                r.get::<_, i64>(1)?,
                r.get::<_, String>(2)?,
                r.get::<_, String>(3)?,
            ))
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    rows.into_iter()
        .map(|(id, position, code, kind)| {
            let sources = c
                .prepare("SELECT e.session_id,e.revision_id FROM analysis_rejection_sources s JOIN analysis_events e ON e.id=s.event_id WHERE s.rejection_id=?1 ORDER BY e.session_id,COALESCE(e.revision_id,0)")?
                .query_map([id], |r| {
                    Ok(RejectionSource {
                        session_id: r.get(0)?,
                        revision_id: r.get(1)?,
                    })
                })?
                .collect::<rusqlite::Result<Vec<_>>>()?;
            Ok(RejectionResult {
                kind,
                position: position + 1,
                code,
                sources,
            })
        })
        .collect()
}

/// Codes from the latest attempt of this batch that rejected something, for the next prompt.
/// Only sessions still sent with the batch are named.
pub(super) fn feedback(
    c: &Connection,
    batch: i64,
    sources: &[Source],
) -> Result<Vec<serde_json::Value>> {
    let rows = c
        .prepare("SELECT r.id,r.code FROM analysis_attempt_rejections r WHERE r.attempt_id=(SELECT MAX(x.attempt_id) FROM analysis_attempt_rejections x JOIN analysis_attempts a ON a.id=x.attempt_id WHERE a.batch_id=?1) ORDER BY r.position")?
        .query_map([batch], |r| Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?)))?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    rows.into_iter()
        .map(|(id, code)| {
            let events = c
                .prepare("SELECT event_id FROM analysis_rejection_sources WHERE rejection_id=?1 ORDER BY event_id")?
                .query_map([id], |r| r.get::<_, i64>(0))?
                .collect::<rusqlite::Result<Vec<_>>>()?;
            let mut sessions: Vec<i64> = sources
                .iter()
                .filter(|s| events.contains(&s.event_id))
                .map(|s| s.session_id)
                .collect();
            sessions.dedup();
            Ok(serde_json::json!({
                "code": code,
                "meaning": hint(&code).unwrap_or("The finding failed local validation."),
                "sessionIds": sessions,
            }))
        })
        .collect()
}

/// A batch whose last two provider outcomes rejected every candidate is not sent a third time.
/// The sources the rejected candidates cited are set aside when they are a minority; otherwise
/// the batch is halved, and a single source that still fails is set aside. Nothing is marked
/// analysed: parked sources stay pending until the analysis configuration changes.
pub(super) fn supersede(c: &Connection, batch: i64, version: i64) -> Result<bool> {
    let outcomes = c
        .prepare("SELECT a.id,a.state,a.error FROM analysis_attempts a JOIN analysis_batches b ON b.id=a.batch_id WHERE a.batch_id=?1 AND b.state='failed' AND b.privacy_version=?2 AND a.state NOT IN ('running','interrupted','cancelled') ORDER BY a.id DESC LIMIT 2")?
        .query_map(params![batch, version], |r| {
            Ok((
                r.get::<_, i64>(0)?,
                r.get::<_, String>(1)?,
                r.get::<_, Option<String>>(2)?,
            ))
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    if outcomes.len() < 2
        || outcomes.iter().any(|(_, state, error)| {
            state != "failed" || error.as_deref() != Some("all-candidates-rejected")
        })
    {
        return Ok(false);
    }
    let events = c
        .prepare("SELECT event_id,fingerprint FROM analysis_batch_sources WHERE batch_id=?1 ORDER BY event_id")?
        .query_map([batch], |r| Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?)))?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    let cited_events = c
        .prepare("SELECT DISTINCT s.event_id FROM analysis_rejection_sources s JOIN analysis_attempt_rejections r ON r.id=s.rejection_id WHERE r.attempt_id IN (?1,?2)")?
        .query_map(params![outcomes[0].0, outcomes[1].0], |r| r.get::<_, i64>(0))?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    let (parked, groups, reason): (Vec<_>, Vec<Vec<_>>, _) = {
        let (cited, rest): (Vec<_>, Vec<_>) = events
            .iter()
            .cloned()
            .partition(|(event, _)| cited_events.contains(event));
        if !cited.is_empty() && cited.len() * 2 <= events.len() {
            (cited, vec![rest], "rejected-sources-parked")
        } else if events.len() >= 2 {
            let (first, second) = events.split_at(events.len().div_ceil(2));
            (
                vec![],
                vec![first.to_vec(), second.to_vec()],
                "rejected-split",
            )
        } else {
            (events.clone(), vec![], "rejected-sources-parked")
        }
    };
    for (event, _) in &parked {
        c.execute(
            "INSERT OR REPLACE INTO analysis_parked VALUES(?1,?2,?3)",
            params![event, batch, now()?],
        )?;
    }
    for group in groups.iter().filter(|group| !group.is_empty()) {
        c.execute(
            "INSERT INTO analysis_batches(privacy_version,state,created_at,source_count) VALUES(?1,'prepared',?2,?3)",
            params![version, now()?, group.len() as i64],
        )?;
        let id = c.last_insert_rowid();
        for (event, fingerprint) in group {
            c.execute(
                "INSERT INTO analysis_batch_sources VALUES(?1,?2,?3)",
                params![id, event, fingerprint],
            )?;
        }
    }
    c.execute(
        "UPDATE analysis_batches SET state='cancelled',error=?2 WHERE id=?1",
        params![batch, reason],
    )?;
    Ok(true)
}
