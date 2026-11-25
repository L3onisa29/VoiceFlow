//! Validated decisions. Source-linked text is deleted with any contributing source.
use super::*;
use crate::suggestions::{Candidate, Definition, coded};

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub(crate) enum Action {
    Keep,
    Remove,
    Replace,
}
impl Action {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Keep => "keep",
            Self::Remove => "remove",
            Self::Replace => "replace",
        }
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Finding {
    pub name: String,
    pub text: String,
    pub reason: String,
    pub action: Action,
    pub confidence: Option<u8>,
    pub candidate: Option<Candidate>,
    pub evidence: Vec<EvidenceRef>,
}
impl Finding {
    pub fn parse(value: serde_json::Value) -> Result<Self> {
        let finding: Self =
            serde_json::from_value(value).map_err(|e| coded("schema", e.to_string()))?;
        ensure!(
            finding.confidence.is_some_and(|c| c <= 100),
            coded("confidence-missing", "Confidenza richiesta.")
        );
        Ok(finding)
    }
    pub fn validate(&self, c: &Connection) -> Result<()> {
        ensure!(
            !self.evidence.is_empty() && self.evidence.len() <= 64,
            coded("evidence-count", "Evidenze mancanti.")
        );
        for (text, max) in [(&self.name, 120), (&self.text, 2048), (&self.reason, 2000)] {
            ensure!(
                !text.trim().is_empty()
                    && text.chars().count() <= max
                    && !text.chars().any(char::is_control),
                coded("finding-text", "Rilievo non valido.")
            );
        }
        ensure!(
            matches!(
                (&self.action, &self.candidate),
                (Action::Keep, None)
                    | (
                        Action::Remove,
                        Some(Candidate {
                            definition: Definition::Cleanup { .. },
                            ..
                        })
                    )
                    | (
                        Action::Replace,
                        Some(Candidate {
                            definition: Definition::Terminology { .. },
                            ..
                        })
                    )
            ),
            coded("action-rule-mismatch", "Azione e regola incoerenti.")
        );
        let result_text = self
            .candidate
            .as_ref()
            .map(|candidate| crate::suggestions::render_candidate_excerpt(candidate, &self.text))
            .transpose()?;
        for reference in &self.evidence {
            let evidence = crate::suggestions::evidence(c, reference.clone())?;
            ensure!(
                crate::suggestions::contains_example(&evidence.before, &self.text),
                coded(
                    "excerpt-not-in-source",
                    "Estratto inventato o non presente nella fonte."
                )
            );
            if matches!(self.action, Action::Keep) {
                ensure!(
                    evidence.after.as_ref().is_none_or(|after| {
                        crate::suggestions::contains_example(after, &self.text)
                    }),
                    coded(
                        "keep-contradicts-correction",
                        "Il mantenimento contraddice la correzione citata."
                    )
                );
            }
            if let Some(result_text) = &result_text {
                // One requirement, three distinguishable ways to miss it.
                const UNSUPPORTED: &str =
                    "L'azione sull'estratto deve essere supportata da ogni correzione citata.";
                ensure!(
                    result_text != &self.text,
                    coded("rule-no-effect", UNSUPPORTED)
                );
                ensure!(
                    self.candidate
                        .as_ref()
                        .unwrap()
                        .positive_examples
                        .iter()
                        .any(|example| {
                            crate::suggestions::contains_example(&evidence.before, &example.input)
                                && crate::suggestions::contains_example(&example.input, &self.text)
                                && example.input.replacen(&self.text, result_text, 1)
                                    == example.output
                        }),
                    coded("example-not-grounded", UNSUPPORTED)
                );
                ensure!(
                    evidence.after.as_ref().is_none_or(|after| {
                        crate::suggestions::contains_example(after, result_text)
                    }),
                    coded("result-contradicts-correction", UNSUPPORTED)
                );
            }
        }
        Ok(())
    }
    pub fn key(&self) -> Result<String> {
        Ok(format!("{:x}", Sha256::digest(serde_json::to_vec(self)?)))
    }
    pub fn save(
        &self,
        c: &Connection,
        attempt: i64,
        proposal: Option<i64>,
        outcome: &str,
        publication: Option<i64>,
    ) -> Result<()> {
        let result_text = self
            .candidate
            .as_ref()
            .map(|candidate| crate::suggestions::render_candidate_excerpt(candidate, &self.text))
            .transpose()?
            .unwrap_or_else(|| self.text.clone());
        c.execute("INSERT INTO analysis_findings(attempt_id,identity,proposal_id,name,excerpt,reason,action,confidence,outcome,publication,result_text) VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11)",params![attempt,self.key()?,proposal,self.name,self.text,self.reason,self.action.as_str(),self.confidence,outcome,publication,result_text])?;
        let id = c.last_insert_rowid();
        // Store only a hash of the scored candidate. A later manual edit must not
        // inherit this score, and history must not gain another provider-text copy.
        if let Some(candidate) = &self.candidate {
            c.execute(
                "UPDATE analysis_findings SET candidate_hash=?1 WHERE id=?2",
                params![crate::suggestions::candidate_hash(candidate)?, id],
            )?;
        }
        for e in &self.evidence {
            c.execute("INSERT INTO analysis_finding_sources(finding_id,session_id,revision_id,original_index) VALUES(?1,?2,?3,?4)",params![id,e.session_id,e.revision_id,e.original_index])?;
        }
        Ok(())
    }
}
pub(super) fn migrate(c: &Connection) -> Result<()> {
    c.execute_batch("CREATE TABLE IF NOT EXISTS analysis_findings(id INTEGER PRIMARY KEY AUTOINCREMENT,attempt_id INTEGER NOT NULL REFERENCES analysis_attempts(id),identity TEXT NOT NULL,proposal_id INTEGER REFERENCES suggestions(id) ON DELETE SET NULL,name TEXT NOT NULL,excerpt TEXT NOT NULL,reason TEXT NOT NULL,action TEXT NOT NULL,confidence INTEGER CHECK(confidence BETWEEN 0 AND 100),outcome TEXT NOT NULL,result_text TEXT NOT NULL,publication INTEGER REFERENCES suggestion_publications(version),UNIQUE(attempt_id,identity));
        CREATE TABLE IF NOT EXISTS analysis_finding_sources(finding_id INTEGER NOT NULL REFERENCES analysis_findings(id) ON DELETE CASCADE,session_id INTEGER NOT NULL REFERENCES transcriptions(id) ON DELETE CASCADE,revision_id INTEGER REFERENCES history_revisions(id) ON DELETE CASCADE,original_index INTEGER);
        CREATE INDEX IF NOT EXISTS analysis_findings_attempt ON analysis_findings(attempt_id);
        CREATE INDEX IF NOT EXISTS analysis_findings_proposal ON analysis_findings(proposal_id,id);
        CREATE TRIGGER IF NOT EXISTS analysis_findings_delete_session BEFORE DELETE ON transcriptions BEGIN DELETE FROM analysis_findings WHERE id IN (SELECT finding_id FROM analysis_finding_sources WHERE session_id=OLD.id); END;
        CREATE TRIGGER IF NOT EXISTS analysis_findings_delete_revision BEFORE DELETE ON history_revisions BEGIN DELETE FROM analysis_findings WHERE id IN (SELECT finding_id FROM analysis_finding_sources WHERE revision_id=OLD.id); END;
        CREATE TRIGGER IF NOT EXISTS analysis_findings_exclude AFTER INSERT ON analysis_exclusions BEGIN DELETE FROM analysis_findings WHERE id IN (SELECT finding_id FROM analysis_finding_sources WHERE session_id=NEW.session_id); END;
        CREATE TRIGGER IF NOT EXISTS analysis_findings_forget AFTER DELETE ON confirmed_corrections BEGIN DELETE FROM analysis_findings WHERE id IN (SELECT finding_id FROM analysis_finding_sources WHERE revision_id=OLD.revision_id); END;
        CREATE TRIGGER IF NOT EXISTS analysis_findings_undo AFTER UPDATE OF reverted ON history_revisions WHEN NEW.reverted=1 BEGIN DELETE FROM analysis_findings WHERE id IN (SELECT finding_id FROM analysis_finding_sources WHERE revision_id=NEW.id); END;
        CREATE TABLE IF NOT EXISTS suggestion_human_veto(identity TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS suggestion_auto_origin(proposal_id INTEGER PRIMARY KEY REFERENCES suggestions(id) ON DELETE CASCADE,eligible INTEGER NOT NULL);
        INSERT OR IGNORE INTO suggestion_auto_origin SELECT id,0 FROM suggestions;
        UPDATE analysis_schema SET version=3 WHERE version<3;")?;
    // Older action records only store a hash, so any manual action conservatively vetoes automation.
    let identities=c.prepare("SELECT identity FROM suggestions WHERE id IN (SELECT proposal_id FROM suggestion_actions WHERE request_key NOT LIKE 'analysis-auto:%')")?.query_map([],|r|r.get::<_,String>(0))?.collect::<rusqlite::Result<Vec<_>>>()?;
    for identity in identities {
        crate::suggestions::remember_veto(c, &identity)?;
    }
    let has_hash: bool = c.query_row("SELECT EXISTS(SELECT 1 FROM pragma_table_info('analysis_findings') WHERE name='candidate_hash')",[],|r|r.get(0))?;
    if !has_hash {
        // Legacy findings cannot be reliably bound to a current edited candidate.
        c.execute_batch("ALTER TABLE analysis_findings ADD COLUMN candidate_hash TEXT;")?;
    }
    c.execute_batch("CREATE TRIGGER IF NOT EXISTS analysis_scrub_unapproved BEFORE DELETE ON suggestions WHEN OLD.approved_at IS NULL BEGIN
        DELETE FROM analysis_findings WHERE proposal_id=OLD.id;
        END;
        CREATE TRIGGER IF NOT EXISTS analysis_scrub_excluded AFTER INSERT ON analysis_exclusions BEGIN
        DELETE FROM suggestions WHERE approved_at IS NULL AND id IN (SELECT proposal_id FROM suggestion_evidence WHERE session_id=NEW.session_id);
        DELETE FROM suggestion_evidence WHERE session_id=NEW.session_id;
        END;
        CREATE TRIGGER IF NOT EXISTS analysis_scrub_revision BEFORE DELETE ON history_revisions BEGIN
        DELETE FROM suggestions WHERE approved_at IS NULL AND id IN (SELECT proposal_id FROM suggestion_evidence WHERE revision_id=OLD.id);
        END;
        CREATE TRIGGER IF NOT EXISTS analysis_scrub_forgotten AFTER DELETE ON confirmed_corrections BEGIN
        DELETE FROM suggestions WHERE approved_at IS NULL AND id IN (SELECT proposal_id FROM suggestion_evidence WHERE revision_id=OLD.revision_id);
        DELETE FROM suggestion_evidence WHERE revision_id=OLD.revision_id;
        END;
        CREATE TRIGGER IF NOT EXISTS analysis_scrub_undone AFTER UPDATE OF reverted ON history_revisions WHEN NEW.reverted=1 BEGIN
        DELETE FROM suggestions WHERE approved_at IS NULL AND id IN (SELECT proposal_id FROM suggestion_evidence WHERE revision_id=NEW.id);
        DELETE FROM suggestion_evidence WHERE revision_id=NEW.id;
        END;")?;
    // Repair copies retained by earlier schemas, after recording manual veto hashes.
    c.execute_batch("DELETE FROM suggestions WHERE approved_at IS NULL AND id IN (
        SELECT e.proposal_id FROM suggestion_evidence e
        LEFT JOIN history_revisions r ON r.id=e.revision_id
        WHERE EXISTS(SELECT 1 FROM analysis_exclusions x WHERE x.session_id=e.session_id)
        OR (e.revision_id IS NOT NULL AND (r.reverted=1 OR NOT EXISTS(SELECT 1 FROM confirmed_corrections cc WHERE cc.revision_id=e.revision_id))));
        DELETE FROM suggestion_evidence WHERE session_id IN (SELECT session_id FROM analysis_exclusions)
        OR (revision_id IS NOT NULL AND (revision_id IN (SELECT id FROM history_revisions WHERE reverted=1)
        OR revision_id NOT IN (SELECT revision_id FROM confirmed_corrections)));")?;
    Ok(())
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct FindingResult {
    pub id: i64,
    pub name: String,
    pub text: String,
    pub result_text: String,
    pub reason: String,
    pub action: String,
    pub confidence: Option<u8>,
    pub outcome: String,
    pub publication: Option<i64>,
    pub proposal_id: Option<i64>,
    pub evidence: Vec<EvidenceRef>,
}
pub(super) fn read(c: &Connection, attempt: Option<i64>) -> Result<Vec<FindingResult>> {
    let mut rows=c.prepare("SELECT id,name,excerpt,reason,action,confidence,outcome,publication,proposal_id,result_text FROM analysis_findings WHERE attempt_id=?1 ORDER BY id")?.query_map([attempt],|r|Ok(FindingResult{id:r.get(0)?,name:r.get(1)?,text:r.get(2)?,reason:r.get(3)?,action:r.get(4)?,confidence:r.get(5)?,outcome:r.get(6)?,publication:r.get(7)?,proposal_id:r.get(8)?,result_text:r.get(9)?,evidence:vec![]}))?.collect::<rusqlite::Result<Vec<_>>>()?;
    for row in &mut rows {
        row.evidence=c.prepare("SELECT session_id,revision_id,original_index FROM analysis_finding_sources WHERE finding_id=?1")?.query_map([row.id],|r|Ok(EvidenceRef{session_id:r.get(0)?,revision_id:r.get(1)?,original_index:r.get(2)?}))?.collect::<rusqlite::Result<Vec<_>>>()?;
    }
    Ok(rows)
}
