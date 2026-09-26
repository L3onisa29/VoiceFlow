//! Bounded diagnostics for launcher wakeups, without provider output or source content.
use super::*;
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Invocation {
    pub id: i64,
    pub started_at: i64,
    pub ended_at: Option<i64>,
    pub reason: String,
    pub batch_id: Option<i64>,
}
impl Store {
    // Caller must hold the worker lock for this reconciliation.
    fn recover_invocations(&self) -> Result<()> {
        let c = self.connect()?;
        let interrupted: bool = c.query_row(
            "SELECT EXISTS(SELECT 1 FROM scheduler_invocations WHERE reason='running')",
            [],
            |r| r.get(0),
        )?;
        if interrupted {
            AnalysisStore::open(
                self.data.join("history.sqlite3"),
                self.data.join("dictionary.sqlite3"),
            )
            .recover_interrupted()?;
            c.execute(
                "UPDATE scheduler_invocations SET reason='interrupted' WHERE reason='running'",
                [],
            )?;
        }
        Ok(())
    }
    pub(super) fn begin_invocation(&self) -> Result<i64> {
        self.recover_invocations()?;
        let mut c = self.connect()?;
        let tx = c.transaction()?;
        tx.execute(
            "INSERT INTO scheduler_invocations(started_at,reason) VALUES(?1,'running')",
            [now_ms()],
        )?;
        let id = tx.last_insert_rowid();
        tx.execute("DELETE FROM scheduler_invocations WHERE id NOT IN (SELECT id FROM scheduler_invocations ORDER BY id DESC LIMIT 100)",[])?;
        tx.commit()?;
        Ok(id)
    }
    pub(super) fn note(&self, id: Option<i64>, reason: &str, batch: Option<i64>) {
        if let Some(id) = id {
            let _ = self.finish_invocation(id, reason, batch);
        }
    }
    pub(super) fn finish_invocation(
        &self,
        id: i64,
        reason: &str,
        batch: Option<i64>,
    ) -> Result<()> {
        self.connect()?.execute("UPDATE scheduler_invocations SET ended_at=?1,reason=?2,batch_id=COALESCE(?3,batch_id) WHERE id=?4 AND reason='running'",params![now_ms(),reason,batch,id])?;
        Ok(())
    }
    pub(super) fn invocation_batch(&self, id: i64, batch: i64) -> Result<()> {
        self.connect()?.execute(
            "UPDATE scheduler_invocations SET batch_id=?1 WHERE id=?2",
            params![batch, id],
        )?;
        Ok(())
    }
    pub(super) fn invocations(&self) -> Result<Vec<Invocation>> {
        Ok(self.connect()?.prepare("SELECT id,started_at,ended_at,reason,batch_id FROM scheduler_invocations ORDER BY id DESC LIMIT 100")?.query_map([],|r|Ok(Invocation{id:r.get(0)?,started_at:r.get(1)?,ended_at:r.get(2)?,reason:r.get(3)?,batch_id:r.get(4)?}))?.collect::<rusqlite::Result<Vec<_>>>()?)
    }
}
/// A normal error or early return must not leave a wakeup looking successful.
pub(super) struct Observation<'a> {
    pub store: &'a Store,
    pub id: Option<i64>,
}
impl Drop for Observation<'_> {
    fn drop(&mut self) {
        self.store.note(self.id, "worker-error", None);
    }
}
#[tauri::command]
pub async fn scheduler_invocations(
    state: tauri::State<'_, Scheduler>,
) -> Result<Vec<Invocation>, String> {
    let state = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        let permit = Coordination::new(&state.store.data).worker()?;
        if permit.is_some() {
            state.store.recover_invocations()?;
        }
        state.store.invocations()
    })
    .await
    .map_err(|e| e.to_string())?
    .map_err(|e| e.to_string())
}
