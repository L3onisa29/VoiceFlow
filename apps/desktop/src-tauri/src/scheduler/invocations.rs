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
    pub attempt_id: Option<i64>,
    pub origin: Option<String>,
}
impl Store {
    // Caller must hold the worker lock for this reconciliation.
    pub(super) fn recover_invocations(&self) -> Result<()> {
        let c = self.connect()?;
        let interrupted: bool = c.query_row(
            "SELECT EXISTS(SELECT 1 FROM scheduler_invocations WHERE reason IN ('requested','running'))",
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
                "UPDATE scheduler_invocations SET reason='interrupted' WHERE reason IN ('requested','running')",
                [],
            )?;
        }
        Ok(())
    }
    #[cfg(test)]
    pub(super) fn begin_invocation(&self) -> Result<i64> {
        self.recover_invocations()?;
        self.create_invocation(RunOrigin::Automatic, "running")
    }
    pub(super) fn create_invocation(&self, origin: RunOrigin, reason: &str) -> Result<i64> {
        let mut c = self.connect()?;
        let tx = c.transaction()?;
        tx.execute(
            "INSERT INTO scheduler_invocations(started_at,reason,origin) VALUES(?1,?2,?3)",
            params![now_ms(), reason, origin.as_str()],
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
    /// A wakeup that did nothing. Launchd fires every five minutes inside the window, so a
    /// run of identical automatic skips is kept as one row whose end time moves forward,
    /// instead of pushing real outcomes out of the bounded history.
    pub(super) fn note_skip(&self, id: Option<i64>, reason: &str, origin: RunOrigin) {
        let merged = (|| -> Result<bool> {
            let Some(id) = id.filter(|_| origin == RunOrigin::Automatic) else {
                return Ok(false);
            };
            let mut c = self.connect()?;
            let tx = c.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
            let merged = tx.execute(
                "UPDATE scheduler_invocations SET ended_at=?1 WHERE id=(SELECT MAX(id) FROM scheduler_invocations WHERE id<?2) AND reason=?3 AND origin='automatic' AND batch_id IS NULL AND ended_at IS NOT NULL",
                params![now_ms(), id, reason],
            )? == 1;
            if merged {
                tx.execute(
                    "DELETE FROM scheduler_invocations WHERE id=?1 AND reason='requested'",
                    [id],
                )?;
            }
            tx.commit()?;
            Ok(merged)
        })()
        .unwrap_or(false);
        if !merged {
            self.note(id, reason, None);
        }
    }
    pub(super) fn finish_invocation(
        &self,
        id: i64,
        reason: &str,
        batch: Option<i64>,
    ) -> Result<()> {
        self.connect()?.execute("UPDATE scheduler_invocations SET ended_at=?1,reason=?2,batch_id=COALESCE(?3,batch_id) WHERE id=?4 AND reason IN ('requested','running')",params![now_ms(),reason,batch,id])?;
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
        let mut records = self.connect()?.prepare("SELECT id,started_at,ended_at,reason,batch_id,origin FROM scheduler_invocations ORDER BY id DESC LIMIT 100")?.query_map([],|r|Ok(Invocation{id:r.get(0)?,started_at:r.get(1)?,ended_at:r.get(2)?,reason:r.get(3)?,batch_id:r.get(4)?,origin:r.get(5)?,attempt_id:None}))?.collect::<rusqlite::Result<Vec<_>>>()?;
        // The attempt owns this durable link, written atomically at attempt start.
        // Legacy history databases may not have the additive metadata migration yet.
        if let Ok(c) = Connection::open_with_flags(
            self.data.join("history.sqlite3"),
            rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY,
        ) {
            c.busy_timeout(Duration::from_millis(100))?;
            if let Ok(mut query) =
                c.prepare("SELECT id,batch_id FROM analysis_attempts WHERE invocation_id=?1")
            {
                use rusqlite::OptionalExtension;
                for record in &mut records {
                    if let Some((attempt, batch)) = query
                        .query_row([record.id], |r| Ok((r.get(0)?, r.get(1)?)))
                        .optional()?
                    {
                        record.attempt_id = Some(attempt);
                        record.batch_id = Some(batch);
                    }
                }
            }
        }
        Ok(records)
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

impl Scheduler {
    /// Transfer the worker lock to the same bundled launcher used by launchd.
    /// The request remains observable if spawning fails or the app closes.
    pub fn run_now(&self) -> Result<Invocation> {
        let permit = Coordination::new(&self.store.data).worker()?;
        if permit.is_some() {
            self.store.recover_invocations()?;
        }
        let id = self
            .store
            .create_invocation(RunOrigin::Manual, "requested")?;
        let reason = if permit.is_none() {
            Some("worker-busy")
        } else if !self.bundled_helper() {
            Some("helper-unavailable")
        } else {
            None
        };
        if let Some(reason) = reason {
            self.store.finish_invocation(id, reason, None)?;
        } else if let Some(permit) = permit {
            let spawn = (|| -> Result<std::process::Child> {
                let fd = permit.inherit()?;
                let mut command = Command::new(&self.helper);
                command.args([
                    "--manual",
                    "--data",
                    &self.store.data.to_string_lossy(),
                    "--invocation",
                    &id.to_string(),
                    "--launcher-permit-fd",
                    &fd.to_string(),
                ]);
                #[cfg(test)]
                {
                    command = runtime::role_command_args(
                        "--manual",
                        &self.store.data,
                        -1,
                        &[
                            "--invocation",
                            &id.to_string(),
                            "--launcher-permit-fd",
                            &fd.to_string(),
                        ],
                    )?;
                    command.env(
                        "PATH",
                        format!("{}:/usr/bin:/bin", self.store.data.display()),
                    );
                }
                Ok(command
                    .stdin(Stdio::null())
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .spawn()?)
            })();
            match spawn {
                Ok(mut child) => {
                    let store = self.store.clone();
                    thread::spawn(move || {
                        let _ = child.wait();
                        store.note(Some(id), "interrupted", None);
                    });
                }
                Err(_) => self
                    .store
                    .finish_invocation(id, "helper-unavailable", None)?,
            }
        }
        self.store
            .invocations()?
            .into_iter()
            .find(|r| r.id == id)
            .context("Richiesta non disponibile.")
    }
}
#[tauri::command]
pub async fn scheduler_run_now(state: tauri::State<'_, Scheduler>) -> Result<Invocation, String> {
    let state = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || state.run_now())
        .await
        .map_err(|e| e.to_string())?
        .map_err(|e| e.to_string())
}
