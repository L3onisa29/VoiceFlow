//! Local, incremental analysis. The provider receives RAM-only payloads and cannot publish rules.
mod provider;
mod schema;
use crate::{
    dictionary::{DictionarySnapshot, DictionaryStore},
    history::HistoryStore,
    suggestions::{CandidateRejection, EvidenceRef, IngestFailure, IngestRequest, SuggestionStore},
};
use anyhow::{Context, Result, ensure};
pub use provider::{
    Cancellation, CodexProvider, Executables, Provider, ProviderError, ProviderReply,
};
use rusqlite::{Connection, OptionalExtension, TransactionBehavior, params};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    path::PathBuf,
    time::{Duration, Instant},
};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct AnalysisSettings {
    pub enabled: bool,
    pub max_sources: u32,
    pub max_input_bytes: u32,
    pub max_requests_per_day: u32,
    pub request_timeout_seconds: u32,
    pub max_seconds_per_day: u32,
}
impl Default for AnalysisSettings {
    fn default() -> Self {
        Self {
            enabled: false,
            max_sources: 20,
            max_input_bytes: 65_536,
            max_requests_per_day: 2,
            request_timeout_seconds: 120,
            max_seconds_per_day: 300,
        }
    }
}
impl AnalysisSettings {
    fn validate(&self) -> Result<()> {
        ensure!(
            (1..=100).contains(&self.max_sources)
                && (4096..=262_144).contains(&self.max_input_bytes)
                && (1..=20).contains(&self.max_requests_per_day)
                && (1..=600).contains(&self.request_timeout_seconds)
                && (1..=3600).contains(&self.max_seconds_per_day),
            "Limiti analisi non validi."
        );
        Ok(())
    }
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AnalysisStatus {
    pub settings: AnalysisSettings,
    pub requests_today: u32,
    pub input_bytes_today: i64,
    pub elapsed_seconds_today: i64,
    pub reserved_seconds_today: i64,
    pub pending_sources: i64,
    pub excluded_sessions: i64,
    pub last_state: Option<String>,
    pub last_error: Option<String>,
    pub accepted_candidates: i64,
    pub rejected_candidates: i64,
}
#[derive(Clone)]
pub struct AnalysisStore {
    history: HistoryStore,
    dictionary: DictionaryStore,
}
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct Source {
    event_id: i64,
    session_id: i64,
    revision_id: Option<i64>,
    language: String,
    original_asr: Option<Vec<String>>,
    produced_text: Option<String>,
    before_text: Option<String>,
    after_text: Option<String>,
    category: Option<String>,
    processing: Option<serde_json::Value>,
}
impl Source {
    fn refs(&self) -> Vec<EvidenceRef> {
        let mut refs = vec![EvidenceRef {
            session_id: self.session_id,
            revision_id: self.revision_id,
            original_index: None,
        }];
        if let Some(raw) = &self.original_asr {
            refs.extend((0..raw.len()).map(|i| EvidenceRef {
                session_id: self.session_id,
                revision_id: None,
                original_index: Some(i as u32),
            }));
        }
        refs
    }
    fn fingerprint(&self) -> Result<String> {
        Ok(format!("{:x}", Sha256::digest(serde_json::to_vec(self)?)))
    }
}
/// Opaque batch capability: only this store can prepare sources. Text is never persisted here.
#[derive(Debug)]
pub struct PreparedBatch {
    id: i64,
    privacy_version: i64,
    sources: Vec<Source>,
    context: Vec<serde_json::Value>,
}
impl PreparedBatch {
    pub fn id(&self) -> i64 {
        self.id
    }
    pub fn source_count(&self) -> usize {
        self.sources.len()
    }
    pub fn prompt(&self) -> Result<String> {
        Ok(format!(
            "{}\nUNTRUSTED_DATA_JSON\n{}",
            include_str!("prompt.txt"),
            serde_json::to_string(
                &serde_json::json!({"sources":self.sources,"relevantRulesAndHypotheses":self.context})
            )?
        ))
    }
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BatchOutcome {
    pub batch_id: i64,
    pub state: String,
    pub accepted: usize,
    pub rejected: usize,
}

pub(crate) fn migrate(c: &Connection) -> Result<()> {
    c.execute_batch(
        "CREATE TABLE IF NOT EXISTS analysis_schema(version INTEGER NOT NULL);
        INSERT INTO analysis_schema SELECT 1 WHERE NOT EXISTS(SELECT 1 FROM analysis_schema);",
    )?;
    ensure!(
        c.query_row("SELECT version FROM analysis_schema", [], |r| r
            .get::<_, i64>(0))?
            == 1,
        "Analisi creata da una versione più recente."
    );
    c.execute_batch("CREATE TABLE IF NOT EXISTS analysis_settings(id INTEGER PRIMARY KEY CHECK(id=1),json TEXT NOT NULL,privacy_version INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS analysis_scan(id INTEGER PRIMARY KEY CHECK(id=1),cursor INTEGER NOT NULL);
        INSERT OR IGNORE INTO analysis_scan VALUES(1,0);
        CREATE TABLE IF NOT EXISTS analysis_exclusions(session_id INTEGER PRIMARY KEY REFERENCES transcriptions(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS analysis_events(id INTEGER PRIMARY KEY AUTOINCREMENT,session_id INTEGER NOT NULL REFERENCES transcriptions(id) ON DELETE CASCADE,revision_id INTEGER REFERENCES history_revisions(id) ON DELETE CASCADE,done INTEGER NOT NULL DEFAULT 0);
        CREATE INDEX IF NOT EXISTS analysis_pending ON analysis_events(done,id);
        CREATE UNIQUE INDEX IF NOT EXISTS analysis_event_source ON analysis_events(session_id,IFNULL(revision_id,0));
        CREATE TABLE IF NOT EXISTS analysis_batches(id INTEGER PRIMARY KEY AUTOINCREMENT,privacy_version INTEGER NOT NULL,state TEXT NOT NULL,error TEXT,accepted INTEGER NOT NULL DEFAULT 0,rejected INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS analysis_batch_sources(batch_id INTEGER NOT NULL REFERENCES analysis_batches(id) ON DELETE CASCADE,event_id INTEGER NOT NULL REFERENCES analysis_events(id) ON DELETE CASCADE,fingerprint TEXT NOT NULL,PRIMARY KEY(batch_id,event_id));
        CREATE TABLE IF NOT EXISTS analysis_attempts(id INTEGER PRIMARY KEY AUTOINCREMENT,batch_id INTEGER NOT NULL REFERENCES analysis_batches(id),started_at INTEGER NOT NULL,input_bytes INTEGER NOT NULL,elapsed_ms INTEGER NOT NULL DEFAULT 0,reserved_ms INTEGER NOT NULL,state TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS analysis_new_session AFTER INSERT ON transcriptions BEGIN INSERT INTO analysis_events(session_id) VALUES(NEW.id); END;
        CREATE TRIGGER IF NOT EXISTS analysis_remember AFTER INSERT ON confirmed_corrections BEGIN INSERT OR IGNORE INTO analysis_events(session_id,revision_id) SELECT transcription_id,id FROM history_revisions WHERE id=NEW.revision_id; END;
        CREATE TRIGGER IF NOT EXISTS analysis_recategorize AFTER UPDATE OF category ON confirmed_corrections WHEN OLD.category<>NEW.category BEGIN UPDATE analysis_events SET done=0 WHERE revision_id=NEW.revision_id; END;
        CREATE TRIGGER IF NOT EXISTS analysis_forget AFTER DELETE ON confirmed_corrections BEGIN DELETE FROM analysis_events WHERE revision_id=OLD.revision_id; END;
        CREATE TRIGGER IF NOT EXISTS analysis_undo AFTER UPDATE OF reverted ON history_revisions WHEN NEW.reverted=1 BEGIN DELETE FROM analysis_events WHERE revision_id=NEW.id; END;")?;
    let inserted = c.execute(
        "INSERT OR IGNORE INTO analysis_settings(id,json) VALUES(1,?1)",
        [serde_json::to_string(&AnalysisSettings::default())?],
    )?;
    if inserted > 0 {
        c.execute_batch("INSERT OR IGNORE INTO analysis_events(session_id) SELECT id FROM transcriptions;
            INSERT OR IGNORE INTO analysis_events(session_id,revision_id) SELECT r.transcription_id,r.id FROM history_revisions r JOIN confirmed_corrections c ON c.revision_id=r.id WHERE r.reverted=0;")?;
    }
    c.prepare(
        "SELECT id,privacy_version,state,error,accepted,rejected FROM analysis_batches LIMIT 0",
    )?;
    c.prepare("SELECT id,session_id,revision_id,done FROM analysis_events LIMIT 0")?;
    Ok(())
}
fn settings(c: &Connection) -> Result<(AnalysisSettings, i64)> {
    let (json, version): (String, i64) = c.query_row(
        "SELECT json,privacy_version FROM analysis_settings WHERE id=1",
        [],
        |r| Ok((r.get(0)?, r.get(1)?)),
    )?;
    let settings: AnalysisSettings = serde_json::from_str(&json)?;
    settings.validate()?;
    Ok((settings, version))
}
fn source(c: &Connection, id: i64, max: usize) -> Result<Source> {
    let (sid,rid,lang,raw,produced,processing):(i64,Option<i64>,String,Option<String>,Option<String>,Option<String>)=c.query_row("SELECT e.session_id,e.revision_id,t.language,CASE WHEN e.revision_id IS NULL THEN t.original_asr_json END,CASE WHEN e.revision_id IS NULL THEN t.produced_text END,t.processing_json FROM analysis_events e JOIN transcriptions t ON t.id=e.session_id WHERE e.id=?1 AND NOT EXISTS(SELECT 1 FROM analysis_exclusions x WHERE x.session_id=e.session_id) AND length(CAST(IFNULL(t.original_asr_json,'')||t.produced_text||IFNULL(t.processing_json,'') AS BLOB))<=?2",params![id,max as i64],|r|Ok((r.get(0)?,r.get(1)?,r.get(2)?,r.get(3)?,r.get(4)?,r.get(5)?))).context("Sorgente esclusa, eliminata o oltre il limite.")?;
    let (before, after, category) = if let Some(rid) = rid {
        c.query_row("SELECT r.before_text,r.text,c.category FROM history_revisions r JOIN confirmed_corrections c ON c.revision_id=r.id WHERE r.id=?1 AND r.reverted=0 AND length(CAST(r.before_text||r.text AS BLOB))<=?2",params![rid,max as i64],|r|Ok((Some(r.get::<_,String>(0)?),Some(r.get::<_,String>(1)?),Some(r.get::<_,String>(2)?))))?
    } else {
        (None, None, None)
    };
    Ok(Source {
        event_id: id,
        session_id: sid,
        revision_id: rid,
        language: lang,
        original_asr: raw.map(|s| serde_json::from_str(&s)).transpose()?,
        produced_text: produced,
        before_text: before,
        after_text: after,
        category,
        processing: processing.map(|s| serde_json::from_str(&s)).transpose()?,
    })
}
/// Called inside C2's candidate transaction. A global session ID alone grants no access.
pub(crate) fn validate_scope(c: &Connection, batch_id: i64, refs: &[EvidenceRef]) -> Result<()> {
    let (config, version) = settings(c)?;
    let valid:bool=c.query_row("SELECT EXISTS(SELECT 1 FROM analysis_batches WHERE id=?1 AND privacy_version=?2 AND state='running')",params![batch_id,version],|r|r.get(0))?;
    ensure!(
        config.enabled && valid,
        CandidateRejection(anyhow::anyhow!("Consenso revocato o lotto non attivo."))
    );
    for reference in refs {
        let row:Option<(i64,String)>=c.query_row("SELECT e.id,b.fingerprint FROM analysis_batch_sources b JOIN analysis_events e ON e.id=b.event_id WHERE b.batch_id=?1 AND e.session_id=?2 AND e.revision_id IS ?3",params![batch_id,reference.session_id,reference.revision_id],|r|Ok((r.get(0)?,r.get(1)?))).optional()?;
        let (id, hash) = row.ok_or_else(|| {
            CandidateRejection(anyhow::anyhow!("Evidenza fuori dal lotto preparato."))
        })?;
        let current = source(c, id, 262_144).map_err(|error| {
            // Missing/removed source is a domain rejection; a query or decoding fault is not.
            if matches!(
                error.downcast_ref::<rusqlite::Error>(),
                Some(rusqlite::Error::QueryReturnedNoRows)
            ) {
                anyhow::Error::from(CandidateRejection(error))
            } else {
                error
            }
        })?;
        ensure!(
            current.fingerprint()? == hash && current.refs().contains(reference),
            CandidateRejection(anyhow::anyhow!("Sorgente cambiata dopo la preparazione."))
        );
    }
    Ok(())
}
impl AnalysisStore {
    pub fn open(history: PathBuf, dictionary: PathBuf) -> Self {
        Self {
            history: HistoryStore::new(history),
            dictionary: DictionaryStore::new(dictionary),
        }
    }
    pub(crate) fn new(history: HistoryStore, dictionary: DictionaryStore) -> Self {
        Self {
            history,
            dictionary,
        }
    }
    pub fn status(&self, now_ms: i64) -> Result<AnalysisStatus> {
        self.history.access(|c|{
        let (settings,_)=settings(c)?;
        let (requests,bytes,ms,reserved):(u32,i64,i64,i64)=c.query_row("SELECT COUNT(*),COALESCE(SUM(input_bytes),0),COALESCE(SUM(elapsed_ms),0),COALESCE(SUM(CASE WHEN state IN ('running','interrupted') THEN reserved_ms ELSE 0 END),0) FROM analysis_attempts WHERE started_at>=?1",[now_ms-now_ms.rem_euclid(86_400_000)],|r|Ok((r.get(0)?,r.get(1)?,r.get(2)?,r.get(3)?)))?;
        let last:Option<(String,Option<String>)>=c.query_row("SELECT state,error FROM analysis_batches ORDER BY id DESC LIMIT 1",[],|r|Ok((r.get(0)?,r.get(1)?))).optional()?;
        let (accepted,rejected):(i64,i64)=c.query_row("SELECT COALESCE(SUM(accepted),0),COALESCE(SUM(rejected),0) FROM analysis_batches",[],|r|Ok((r.get(0)?,r.get(1)?)))?;
        Ok(AnalysisStatus{settings,requests_today:requests,input_bytes_today:bytes,elapsed_seconds_today:(ms+999)/1000,reserved_seconds_today:(reserved+999)/1000,pending_sources:c.query_row("SELECT COUNT(*) FROM analysis_events e WHERE done=0 AND NOT EXISTS(SELECT 1 FROM analysis_exclusions x WHERE x.session_id=e.session_id)",[],|r|r.get(0))?,excluded_sessions:c.query_row("SELECT COUNT(*) FROM analysis_exclusions",[],|r|r.get(0))?,last_state:last.as_ref().map(|r|r.0.clone()),last_error:last.and_then(|r|r.1),accepted_candidates:accepted,rejected_candidates:rejected})
    })
    }
    pub fn configure(&self, config: AnalysisSettings, consent: bool) -> Result<()> {
        config.validate()?;
        ensure!(
            !config.enabled || consent,
            "Conferma l'invio dei testi selezionati a OpenAI."
        );
        self.history.access(|c|{
        let tx=c.transaction_with_behavior(TransactionBehavior::Immediate)?;
        if settings(&tx)?.0 == config { return Ok(()); }
        tx.execute("UPDATE analysis_settings SET json=?1,privacy_version=privacy_version+1 WHERE id=1",[serde_json::to_string(&config)?])?;
        tx.execute("UPDATE analysis_batches SET state='cancelled',error='privacy-changed' WHERE state IN ('prepared','running','failed')",[])?;tx.commit()?;Ok(())
    })
    }
    pub fn excluded(&self, id: i64) -> Result<bool> {
        self.history.access(|c| {
            Ok(c.query_row(
                "SELECT EXISTS(SELECT 1 FROM analysis_exclusions WHERE session_id=?1)",
                [id],
                |r| r.get(0),
            )?)
        })
    }
    pub fn exclude(&self, id: i64, excluded: bool) -> Result<()> {
        ensure!(id > 0, "Sessione non valida.");
        self.history.access(|c|{
        let tx=c.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let exists:bool=tx.query_row("SELECT EXISTS(SELECT 1 FROM transcriptions WHERE id=?1)",[id],|r|r.get(0))?;ensure!(exists,"Sessione eliminata.");
        let changed=if excluded {tx.execute("INSERT OR IGNORE INTO analysis_exclusions VALUES(?1)",[id])?}else{tx.execute("DELETE FROM analysis_exclusions WHERE session_id=?1",[id])?};
        if changed>0 {tx.execute("UPDATE analysis_settings SET privacy_version=privacy_version+1",[])?;tx.execute("UPDATE analysis_batches SET state='cancelled',error='privacy-changed' WHERE state IN ('prepared','running','failed')",[])?;}
        tx.commit()?;Ok(())
    })
    }
    /// Local preparation only. Call from a worker, never the UI thread.
    pub fn prepare(&self) -> Result<Option<PreparedBatch>> {
        let dictionary = self.dictionary.snapshot()?;
        self.history.access(|c|{
            let tx=c.transaction_with_behavior(TransactionBehavior::Immediate)?;
            let (config,version)=settings(&tx)?;ensure!(config.enabled,"Analisi disattivata.");
            let pending:Option<i64>=tx.query_row("SELECT id FROM analysis_batches WHERE state IN ('prepared','running','failed') ORDER BY id LIMIT 1",[],|r|r.get(0)).optional()?;
            if let Some(id)=pending {
                let batch=self.reconstruct(&tx,id,&config,version,&dictionary)?;
                if !batch.sources.is_empty() { return Ok(Some(batch)); }
                tx.execute("UPDATE analysis_batches SET state='cancelled',error='source-changed' WHERE id=?1",[id])?;
            }
            let cursor:i64=tx.query_row("SELECT cursor FROM analysis_scan WHERE id=1",[],|r|r.get(0))?;
            let scan=|after:i64|->Result<Vec<i64>> {Ok(tx.prepare("SELECT id FROM analysis_events e WHERE done=0 AND id>?1 AND NOT EXISTS(SELECT 1 FROM analysis_exclusions x WHERE x.session_id=e.session_id) ORDER BY id LIMIT 128")?.query_map([after],|r|r.get::<_,i64>(0))?.collect::<rusqlite::Result<Vec<_>>>()?)};
            let mut ids=scan(cursor)?;
            if ids.is_empty() && cursor>0 {ids=scan(0)?;}
            let mut scanned=cursor;
            let mut batch=PreparedBatch{id:0,privacy_version:version,sources:vec![],context:vec![]};
            for id in ids {
                scanned=id;
                // Oversized sources are left pending, never silently truncated or marked analyzed.
                if let Ok(item)=source(&tx,id,config.max_input_bytes as usize) {
                    batch.sources.push(item);
                    if batch.prompt()?.len()>config.max_input_bytes as usize {batch.sources.pop();continue;}
                    if batch.sources.len()>=config.max_sources as usize {break;}
                }
            }
            tx.execute("UPDATE analysis_scan SET cursor=?1 WHERE id=1",[scanned])?;
            if batch.sources.is_empty(){tx.commit()?;return Ok(None);}
            tx.execute("INSERT INTO analysis_batches(privacy_version,state) VALUES(?1,'prepared')",[version])?;batch.id=tx.last_insert_rowid();
            for source in &batch.sources {tx.execute("INSERT INTO analysis_batch_sources VALUES(?1,?2,?3)",params![batch.id,source.event_id,source.fingerprint()?])?;}
            add_context(&tx,&mut batch,config.max_input_bytes as usize,&dictionary)?;
            tx.commit()?;Ok(Some(batch))
        })
    }
    fn reconstruct(
        &self,
        c: &Connection,
        id: i64,
        config: &AnalysisSettings,
        version: i64,
        dictionary: &DictionarySnapshot,
    ) -> Result<PreparedBatch> {
        let privacy: i64 = c.query_row(
            "SELECT privacy_version FROM analysis_batches WHERE id=?1",
            [id],
            |r| r.get(0),
        )?;
        ensure!(privacy == version, "Lotto revocato.");
        let entries=c.prepare("SELECT event_id,fingerprint FROM analysis_batch_sources WHERE batch_id=?1 ORDER BY event_id")?.query_map([id],|r|Ok((r.get::<_,i64>(0)?,r.get::<_,String>(1)?)))?.collect::<rusqlite::Result<Vec<_>>>()?;
        let mut batch = PreparedBatch {
            id,
            privacy_version: privacy,
            sources: vec![],
            context: vec![],
        };
        for (event, hash) in entries {
            if let Ok(s) = source(c, event, config.max_input_bytes as usize)
                && s.fingerprint()? == hash
            {
                batch.sources.push(s);
            }
        }
        add_context(c, &mut batch, config.max_input_bytes as usize, dictionary)?;
        Ok(batch)
    }
    /// D2 calls this only after proving the previous worker lease expired. No automatic retry loop.
    pub fn recover_interrupted(&self) -> Result<()> {
        self.history.access(|c|{
        let tx=c.transaction_with_behavior(TransactionBehavior::Immediate)?;
        tx.execute("UPDATE analysis_batches SET state='failed',error='interrupted' WHERE state='running'",[])?;
        // Unknown elapsed time after a crash consumes the reserved timeout, never free retries.
        tx.execute("UPDATE analysis_attempts SET state='interrupted' WHERE state='running'",[])?;
        tx.commit()?;Ok(())
    })
    }
    /// One bounded request. Caller owns scheduling/worker lease. Providers run outside all DB locks.
    pub fn run(
        &self,
        batch: PreparedBatch,
        provider: &dyn Provider,
        cancel: &dyn Cancellation,
        now_ms: i64,
    ) -> Result<BatchOutcome> {
        ensure!(!cancel.cancelled(), "Analisi annullata.");
        let dictionary = self.dictionary.snapshot()?;
        let (prompt,timeout,attempt)=self.history.access(|c|{
            let tx=c.transaction_with_behavior(TransactionBehavior::Immediate)?;
            let (config,version)=settings(&tx)?;ensure!(config.enabled&&version==batch.privacy_version,"Consenso revocato.");
            let current=self.reconstruct(&tx,batch.id,&config,version,&dictionary)?;
            ensure!(!current.sources.is_empty(),"Nessuna sorgente ancora valida nel lotto.");
            let prompt=current.prompt()?;ensure!(prompt.len()<=config.max_input_bytes as usize,"Lotto oltre il limite.");
            let (requests,ms):(u32,i64)=tx.query_row("SELECT COUNT(*),COALESCE(SUM(CASE WHEN state IN ('running','interrupted') THEN reserved_ms ELSE elapsed_ms END),0) FROM analysis_attempts WHERE started_at>=?1",[now_ms-now_ms.rem_euclid(86_400_000)],|r|Ok((r.get(0)?,r.get(1)?)))?;
            ensure!(requests<config.max_requests_per_day&&ms<(config.max_seconds_per_day as i64)*1000,"Limite giornaliero raggiunto.");
            let timeout=Duration::from_millis(((config.request_timeout_seconds as u64)*1000).min((config.max_seconds_per_day as i64*1000-ms) as u64));
            ensure!(tx.execute("UPDATE analysis_batches SET state='running',error=NULL WHERE id=?1 AND state IN ('prepared','failed')",[batch.id])?==1,"Lotto già avviato o concluso.");
            tx.execute("INSERT INTO analysis_attempts(batch_id,started_at,input_bytes,reserved_ms,state) VALUES(?1,?2,?3,?4,'running')",params![batch.id,now_ms,prompt.len() as i64,timeout.as_millis() as i64])?;let attempt=tx.last_insert_rowid();tx.commit()?;Ok((prompt,timeout,attempt))
        })?;
        let stop = || {
            cancel.cancelled()
                || self
                    .history
                    .access(|c| {
                        let (config, version) = settings(c)?;
                        Ok(!config.enabled || version != batch.privacy_version)
                    })
                    .unwrap_or(true)
        };
        let start = Instant::now();
        let response = provider.analyze(&prompt, timeout, &stop);
        let elapsed = start.elapsed().as_millis().min(i64::MAX as u128) as i64;
        let mut accepted = 0;
        let mut rejected = 0;
        let (state, error) = if stop() {
            ("cancelled", Some("cancelled"))
        } else {
            match response {
                Err(ref error) => ("failed", Some(error.code())),
                Ok(reply) if reply.candidates.len() > 16 => ("failed", Some("output-limit")),
                Ok(reply) => {
                    let mut infrastructure_failure = false;
                    let suggestions =
                        SuggestionStore::new(self.history.clone(), self.dictionary.clone());
                    for value in reply.candidates {
                        if stop() {
                            break;
                        }
                        if !schema::candidate(&value) {
                            rejected += 1;
                            continue;
                        }
                        let request = match serde_json::from_value::<IngestRequest>(value) {
                            Ok(request) => request,
                            Err(_) => {
                                rejected += 1;
                                continue;
                            }
                        };
                        match suggestions.ingest_prepared(request, batch.id) {
                            Ok(()) => accepted += 1,
                            Err(IngestFailure::InvalidCandidate) => rejected += 1,
                            Err(IngestFailure::Infrastructure) => {
                                infrastructure_failure = true;
                                break;
                            }
                        }
                    }
                    if stop() {
                        ("cancelled", Some("cancelled"))
                    } else if infrastructure_failure {
                        ("failed", Some("ingest-storage"))
                    } else if rejected > 0 {
                        ("partial", Some("candidate-rejected"))
                    } else {
                        ("completed", None)
                    }
                }
            }
        };
        self.history.access(|c| {
            let tx = c.transaction_with_behavior(TransactionBehavior::Immediate)?;
            tx.execute(
                "UPDATE analysis_attempts SET elapsed_ms=?1,state=?2 WHERE id=?3",
                params![elapsed, state, attempt],
            )?;
            tx.execute(
                "UPDATE analysis_batches SET state=?1,error=?2,accepted=?3,rejected=?4 WHERE id=?5",
                params![state, error, accepted as i64, rejected as i64, batch.id],
            )?;
            if state == "completed" || state == "partial" {
                // Only unchanged, still available sources advance the checkpoint.
                for s in &batch.sources {
                    if source(&tx, s.event_id, 262_144)
                        .is_ok_and(|current| current.fingerprint().ok() == s.fingerprint().ok())
                    {
                        tx.execute(
                            "UPDATE analysis_events SET done=1 WHERE id=?1",
                            [s.event_id],
                        )?;
                    }
                }
            }
            tx.commit()?;
            Ok(())
        })?;
        Ok(BatchOutcome {
            batch_id: batch.id,
            state: state.into(),
            accepted,
            rejected,
        })
    }
}
fn add_context(
    c: &Connection,
    batch: &mut PreparedBatch,
    max: usize,
    dictionary: &DictionarySnapshot,
) -> Result<()> {
    // No examples/reasons from another session. Excluded evidence excludes the entire proposal.
    let rows = c.prepare(
        "SELECT s.id,
         CASE WHEN s.state='approved' AND NOT EXISTS(
           SELECT 1 FROM suggestion_state v JOIN suggestion_publications p ON p.version=v.version,
           json_each(p.rules_json) rule WHERE json_extract(rule.value,'$.id')=s.id
         ) THEN 'disabled' ELSE s.state END,
         s.candidate_json,s.approved_at FROM suggestions s
         WHERE (
           EXISTS(SELECT 1 FROM suggestion_evidence e JOIN analysis_events a ON a.session_id=e.session_id
             JOIN analysis_batch_sources b ON b.event_id=a.id WHERE e.proposal_id=s.id AND b.batch_id=?1)
           OR EXISTS(SELECT 1 FROM json_each(s.candidate_json,'$.positiveExamples') example,
             analysis_batch_sources b JOIN analysis_events a ON a.id=b.event_id
             JOIN transcriptions t ON t.id=a.session_id LEFT JOIN history_revisions r ON r.id=a.revision_id
             WHERE b.batch_id=?1 AND (
               instr(COALESCE(r.before_text,t.produced_text),json_extract(example.value,'$.input'))>0
               OR instr(COALESCE(r.before_text,t.produced_text),json_extract(example.value,'$.output'))>0))
         ) AND NOT EXISTS(SELECT 1 FROM suggestion_evidence e JOIN analysis_exclusions x
             ON x.session_id=e.session_id WHERE e.proposal_id=s.id)
         ORDER BY s.id DESC LIMIT 32"
    )?.query_map([batch.id],|r|Ok((r.get::<_,i64>(0)?,r.get::<_,String>(1)?,r.get::<_,String>(2)?,r.get::<_,Option<i64>>(3)?)))?.collect::<rusqlite::Result<Vec<_>>>()?;
    for (id, state, json, approved_at) in rows {
        let candidate: crate::suggestions::Candidate = serde_json::from_str(&json)?;
        // Definitions are reviewable hypotheses or explicitly approved rules. Prior example text
        // and reasons are omitted, even if their old source was deleted or is outside this batch.
        batch.context.push(serde_json::json!({"proposalId":id,"state":state,"approvedAt":approved_at,"definition":candidate.definition,"language":candidate.language,"groupIds":candidate.group_ids}));
        if batch.prompt()?.len() > max {
            batch.context.pop();
            break;
        }
    }
    for term in dictionary
        .terms
        .iter()
        .filter(|term| {
            batch.sources.iter().any(|source| {
                source
                    .before_text
                    .as_ref()
                    .is_some_and(|before| before.contains(&term.text))
                    && source
                        .after_text
                        .as_ref()
                        .is_some_and(|after| !after.contains(&term.text))
            })
        })
        .take(32)
    {
        batch.context.push(serde_json::json!({"manualDictionaryRule":term,"dictionaryVersion":dictionary.version,"status":"configured-rule-not-human-evidence"}));
        if batch.prompt()?.len() > max {
            batch.context.pop();
            break;
        }
    }
    Ok(())
}
fn now() -> Result<i64> {
    Ok(std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)?
        .as_millis() as i64)
}
#[tauri::command]
pub async fn analysis_status(
    store: tauri::State<'_, AnalysisStore>,
) -> Result<AnalysisStatus, String> {
    let store = store.inner().clone();
    tauri::async_runtime::spawn_blocking(move || store.status(now()?))
        .await
        .map_err(|e| e.to_string())?
        .map_err(|e| e.to_string())
}
#[tauri::command]
pub async fn analysis_configure(
    store: tauri::State<'_, AnalysisStore>,
    settings: AnalysisSettings,
    consent: bool,
) -> Result<AnalysisStatus, String> {
    let store = store.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        store.configure(settings, consent)?;
        store.status(now()?)
    })
    .await
    .map_err(|e| e.to_string())?
    .map_err(|e| e.to_string())
}
#[tauri::command]
pub async fn analysis_exclude(
    store: tauri::State<'_, AnalysisStore>,
    id: i64,
    excluded: bool,
) -> Result<(), String> {
    let store = store.inner().clone();
    tauri::async_runtime::spawn_blocking(move || store.exclude(id, excluded))
        .await
        .map_err(|e| e.to_string())?
        .map_err(|e| e.to_string())
}
#[tauri::command]
pub async fn analysis_excluded(
    store: tauri::State<'_, AnalysisStore>,
    id: i64,
) -> Result<bool, String> {
    let store = store.inner().clone();
    tauri::async_runtime::spawn_blocking(move || store.excluded(id))
        .await
        .map_err(|e| e.to_string())?
        .map_err(|e| e.to_string())
}
#[cfg(test)]
mod tests;
