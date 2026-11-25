//! Read models contain references and operational metadata, never copies of provider/source text.
use super::*;

pub(super) fn migrate(c: &Connection) -> Result<()> {
    let version: i64 = c.query_row("SELECT version FROM analysis_schema", [], |r| r.get(0))?;
    if version == 1 {
        c.execute_batch("ALTER TABLE analysis_batches ADD COLUMN created_at INTEGER;
            ALTER TABLE analysis_batches ADD COLUMN source_count INTEGER;
            ALTER TABLE analysis_attempts ADD COLUMN ended_at INTEGER;
            ALTER TABLE analysis_attempts ADD COLUMN source_count INTEGER;
            ALTER TABLE analysis_attempts ADD COLUMN error TEXT;
            ALTER TABLE analysis_attempts ADD COLUMN accepted INTEGER;
            ALTER TABLE analysis_attempts ADD COLUMN rejected INTEGER;
            CREATE TABLE analysis_attempt_sources(attempt_id INTEGER NOT NULL REFERENCES analysis_attempts(id),session_id INTEGER NOT NULL REFERENCES transcriptions(id) ON DELETE CASCADE,revision_id INTEGER REFERENCES history_revisions(id) ON DELETE CASCADE);
            CREATE INDEX analysis_attempt_sources_run ON analysis_attempt_sources(attempt_id);
            CREATE TABLE analysis_attempt_proposals(id INTEGER PRIMARY KEY AUTOINCREMENT,attempt_id INTEGER NOT NULL REFERENCES analysis_attempts(id),proposal_id INTEGER REFERENCES suggestions(id) ON DELETE SET NULL,relation TEXT NOT NULL,revision INTEGER NOT NULL,UNIQUE(attempt_id,proposal_id));
            CREATE INDEX analysis_attempts_batch ON analysis_attempts(batch_id,id);
            UPDATE analysis_schema SET version=2;")?;
    }
    Ok(())
}
pub(super) fn migrate_metadata(c: &Connection) -> Result<()> {
    let version: i64 = c.query_row("SELECT version FROM analysis_schema", [], |r| r.get(0))?;
    if version == 3 {
        c.execute_batch("ALTER TABLE analysis_attempts ADD COLUMN origin TEXT;
            ALTER TABLE analysis_attempts ADD COLUMN model TEXT;
            ALTER TABLE analysis_attempts ADD COLUMN reasoning_effort TEXT;
            ALTER TABLE analysis_attempts ADD COLUMN invocation_id INTEGER;
            CREATE UNIQUE INDEX analysis_attempt_invocation ON analysis_attempts(invocation_id) WHERE invocation_id IS NOT NULL;
            UPDATE analysis_schema SET version=4;")?;
    }
    Ok(())
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RunKey {
    pub batch_id: i64,
    pub attempt_id: Option<i64>,
}
impl RunKey {
    fn validate(&self) -> Result<()> {
        ensure!(
            self.batch_id > 0 && self.attempt_id.is_none_or(|id| id > 0),
            "Identificatore analisi non valido."
        );
        Ok(())
    }
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RunSummary {
    pub key: RunKey,
    pub state: String,
    pub created_at: Option<i64>,
    pub started_at: Option<i64>,
    pub ended_at: Option<i64>,
    pub elapsed_ms: Option<i64>,
    pub source_count: Option<i64>,
    pub accepted: Option<i64>,
    pub rejected: Option<i64>,
    pub error: Option<String>,
    pub input_bytes: Option<i64>,
    pub origin: Option<String>,
    pub model: Option<String>,
    pub reasoning_effort: Option<String>,
    pub invocation_id: Option<i64>,
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RunPage {
    pub entries: Vec<RunSummary>,
    pub next_cursor: Option<RunKey>,
}
#[derive(Clone, Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RunFilters {
    pub state: Option<String>,
    pub origin: Option<String>,
}
impl RunFilters {
    fn validate(&self) -> Result<()> {
        ensure!(
            self.state.as_deref().is_none_or(|s| matches!(
                s,
                "prepared"
                    | "running"
                    | "completed"
                    | "partial"
                    | "failed"
                    | "cancelled"
                    | "interrupted"
            )),
            "Filtro stato non valido."
        );
        ensure!(
            self.origin
                .as_deref()
                .is_none_or(|s| matches!(s, "manual" | "automatic" | "unknown")),
            "Filtro origine non valido."
        );
        Ok(())
    }
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RunSource {
    pub session_id: i64,
    pub revision_id: Option<i64>,
    pub excluded: bool,
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RunProposal {
    pub proposal_id: Option<i64>,
    pub relation: String,
    pub revision: i64,
    pub current_state: Option<String>,
    pub current_revision: Option<i64>,
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RunDetail {
    pub run: RunSummary,
    pub sources: Vec<RunSource>,
    pub proposals: Vec<RunProposal>,
    pub findings: Vec<findings::FindingResult>,
    /// One code per rejected candidate of this attempt. No provider text.
    pub rejections: Vec<rejections::RejectionResult>,
    /// Spelling pairs this attempt remembered, with what was decided for each.
    pub spellings: Vec<crate::suggestions::spellings::SpellingResult>,
    pub attempts: Vec<RunSummary>,
}
const SELECT: &str =
    "SELECT b.id,a.id,COALESCE(a.state,b.state),b.created_at,a.started_at,a.ended_at,
    CASE WHEN a.ended_at IS NOT NULL THEN a.elapsed_ms END,
    CASE WHEN a.id IS NULL THEN b.source_count ELSE a.source_count END,
    a.accepted,a.rejected,CASE WHEN a.id IS NULL THEN b.error ELSE a.error END,a.input_bytes,a.origin,a.model,a.reasoning_effort,a.invocation_id
    FROM analysis_batches b LEFT JOIN analysis_attempts a ON a.batch_id=b.id";
fn row(r: &rusqlite::Row<'_>) -> rusqlite::Result<RunSummary> {
    Ok(RunSummary {
        key: RunKey {
            batch_id: r.get(0)?,
            attempt_id: r.get(1)?,
        },
        state: r.get(2)?,
        created_at: r.get(3)?,
        started_at: r.get(4)?,
        ended_at: r.get(5)?,
        elapsed_ms: r.get(6)?,
        source_count: r.get(7)?,
        accepted: r.get(8)?,
        rejected: r.get(9)?,
        error: r.get(10)?,
        input_bytes: r.get(11)?,
        origin: r.get(12)?,
        model: r.get(13)?,
        reasoning_effort: r.get(14)?,
        invocation_id: r.get(15)?,
    })
}
impl AnalysisStore {
    pub fn runs(&self, before: Option<RunKey>, limit: u32) -> Result<RunPage> {
        self.filtered_runs(before, limit, RunFilters::default())
    }
    pub fn filtered_runs(
        &self,
        before: Option<RunKey>,
        limit: u32,
        filters: RunFilters,
    ) -> Result<RunPage> {
        ensure!((1..=100).contains(&limit), "Dimensione pagina non valida.");
        filters.validate()?;
        if let Some(key) = &before {
            key.validate()?;
        }
        self.history.access(|c| {
            let mut entries = c.prepare(&format!("{SELECT} WHERE (?1 IS NULL OR b.id<?1 OR (b.id=?1 AND COALESCE(a.id,0)<?2)) AND (?4 IS NULL OR COALESCE(a.state,b.state)=?4) AND (?5 IS NULL OR COALESCE(a.origin,'unknown')=?5) ORDER BY b.id DESC,COALESCE(a.id,0) DESC LIMIT ?3"))?
                .query_map(params![before.as_ref().map(|k|k.batch_id),before.as_ref().and_then(|k|k.attempt_id).unwrap_or(0),limit+1,filters.state,filters.origin],row)?.collect::<rusqlite::Result<Vec<_>>>()?;
            let next_cursor = if entries.len()>limit as usize {entries.pop();entries.last().map(|r|r.key.clone())} else {None};
            Ok(RunPage{entries,next_cursor})
        })
    }
    pub fn run_detail(&self, key: RunKey) -> Result<RunDetail> {
        key.validate()?;
        self.history.access(|c| {
            let tx=c.transaction()?;
            let run=tx.query_row(&format!("{SELECT} WHERE b.id=?1 AND a.id IS ?2"),params![key.batch_id,key.attempt_id],row).optional()?.context("Analisi non disponibile. Aggiorna l'elenco.")?;
            let sources=if let Some(id)=key.attempt_id {
                tx.prepare("SELECT s.session_id,s.revision_id,EXISTS(SELECT 1 FROM analysis_exclusions x WHERE x.session_id=s.session_id) FROM analysis_attempt_sources s WHERE s.attempt_id=?1 ORDER BY s.session_id,COALESCE(s.revision_id,0)")?.query_map([id],source_row)?.collect::<rusqlite::Result<Vec<_>>>()?
            } else {
                tx.prepare("SELECT e.session_id,e.revision_id,EXISTS(SELECT 1 FROM analysis_exclusions x WHERE x.session_id=e.session_id) FROM analysis_batch_sources s JOIN analysis_events e ON e.id=s.event_id WHERE s.batch_id=?1 ORDER BY e.session_id,COALESCE(e.revision_id,0)")?.query_map([key.batch_id],source_row)?.collect::<rusqlite::Result<Vec<_>>>()?
            };
            let active_proposals=crate::suggestions::active_proposal_ids(&tx)?;
            let proposals=tx.prepare("SELECT p.proposal_id,p.relation,p.revision,s.state,s.revision,s.approved_at FROM analysis_attempt_proposals p LEFT JOIN suggestions s ON s.id=p.proposal_id WHERE p.attempt_id=?1 ORDER BY p.id")?.query_map([key.attempt_id],|r|Ok(RunProposal{proposal_id:r.get(0)?,relation:r.get(1)?,revision:r.get(2)?,current_state:if r.get::<_,Option<i64>>(5)?.is_some() { Some(if active_proposals.contains(&r.get::<_,i64>(0)?) { "approved" } else { "disabled" }.into()) } else { r.get(3)? },current_revision:r.get(4)?}))?.collect::<rusqlite::Result<Vec<_>>>()?;
            let findings=findings::read(&tx,key.attempt_id)?;
            let rejections=rejections::read(&tx,key.attempt_id)?;
            let spellings=crate::suggestions::spellings::read(&tx,key.attempt_id)?;
            let attempts=tx.prepare(&format!("{SELECT} WHERE b.id=?1 AND a.id IS NOT NULL ORDER BY a.id DESC"))?.query_map([key.batch_id],row)?.collect::<rusqlite::Result<Vec<_>>>()?;
            tx.commit()?;
            Ok(RunDetail{run,sources,proposals,findings,rejections,spellings,attempts})
        })
    }
}
fn source_row(r: &rusqlite::Row<'_>) -> rusqlite::Result<RunSource> {
    Ok(RunSource {
        session_id: r.get(0)?,
        revision_id: r.get(1)?,
        excluded: r.get(2)?,
    })
}
#[tauri::command]
pub async fn analysis_runs(
    store: tauri::State<'_, AnalysisStore>,
    before: Option<RunKey>,
    limit: u32,
    filters: Option<RunFilters>,
) -> Result<RunPage, String> {
    let store = store.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        store.filtered_runs(before, limit, filters.unwrap_or_default())
    })
    .await
    .map_err(|e| e.to_string())?
    .map_err(|e| e.to_string())
}
#[tauri::command]
pub async fn analysis_run_detail(
    store: tauri::State<'_, AnalysisStore>,
    key: RunKey,
) -> Result<RunDetail, String> {
    let store = store.inner().clone();
    tauri::async_runtime::spawn_blocking(move || store.run_detail(key))
        .await
        .map_err(|e| e.to_string())?
        .map_err(|e| e.to_string())
}
