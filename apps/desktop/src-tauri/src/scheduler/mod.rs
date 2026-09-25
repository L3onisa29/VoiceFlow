//! Local launchd scheduling. Reading status never enables or executes a job.
mod coordination;
mod runtime;
#[cfg(test)]
mod tests;
use crate::analysis::AnalysisStore;
use anyhow::{Context, Result, ensure};
use chrono::{DateTime, Local, Timelike};
use coordination::Permit;
pub use coordination::{CapturePermit, Coordination};
pub use runtime::entry;
use rusqlite::{Connection, params};
use serde::{Deserialize, Serialize};
use std::{
    fs,
    io::Write,
    os::unix::fs::{DirBuilderExt, OpenOptionsExt},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::Arc,
    thread,
    time::{Duration, Instant},
};
const LABEL: &str = "com.voiceflow.desktop.analysis";
const HELPER: &str = "voiceflow-analysis";

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Schedule {
    pub enabled: bool,
    pub start_minute: u16,
    pub end_minute: u16,
}
impl Default for Schedule {
    fn default() -> Self {
        Self {
            enabled: false,
            start_minute: 150,
            end_minute: 360,
        }
    }
}
impl Schedule {
    pub fn validate(&self) -> Result<()> {
        ensure!(
            self.start_minute < 1440 && self.end_minute < 1440,
            "Orario non valido."
        );
        let duration = (self.end_minute + 1440 - self.start_minute) % 1440;
        ensure!(
            (1..=720).contains(&duration),
            "La fascia deve durare da 1 minuto a 12 ore; inizio e fine devono essere diversi."
        );
        Ok(())
    }
    pub fn contains(&self, minute: u16) -> bool {
        self.enabled
            && if self.start_minute < self.end_minute {
                (self.start_minute..self.end_minute).contains(&minute)
            } else {
                minute >= self.start_minute || minute < self.end_minute
            }
    }
    fn current(&self) -> bool {
        #[cfg(test)]
        if let Ok(path) = std::env::var("VF_SCHEDULER_CLOCK") {
            return fs::read_to_string(path)
                .ok()
                .and_then(|s| DateTime::parse_from_rfc3339(s.trim()).ok())
                .is_some_and(|t| self.contains(local_minute(t)));
        }
        self.contains(local_minute(Local::now()))
    }
}
pub fn local_minute<T: chrono::TimeZone>(now: DateTime<T>) -> u16 {
    (now.hour() * 60 + now.minute()) as u16
}
pub(crate) fn now_ms() -> i64 {
    chrono::Utc::now().timestamp_millis()
}
#[derive(Clone)]
pub(crate) struct Store {
    data: PathBuf,
}
impl Store {
    fn new(data: PathBuf) -> Self {
        Self { data }
    }
    fn connect(&self) -> Result<Connection> {
        fs::DirBuilder::new()
            .recursive(true)
            .mode(0o700)
            .create(&self.data)?;
        let c = Connection::open(self.data.join("analysis-scheduler.sqlite3"))?;
        c.busy_timeout(Duration::from_millis(100))?;
        c.execute_batch("CREATE TABLE IF NOT EXISTS scheduler(id INTEGER PRIMARY KEY CHECK(id=1), config TEXT NOT NULL, last_error TEXT, phase TEXT NOT NULL DEFAULT 'idle', lease_until INTEGER NOT NULL DEFAULT 0, next_attempt INTEGER NOT NULL DEFAULT 0, retry_count INTEGER NOT NULL DEFAULT 0, retry_day INTEGER NOT NULL DEFAULT 0);")?;
        c.execute(
            "INSERT OR IGNORE INTO scheduler(id,config) VALUES(1,?1)",
            [serde_json::to_string(&Schedule::default())?],
        )?;
        Ok(c)
    }
    fn schedule(&self) -> Result<Schedule> {
        let path = self.data.join("analysis-scheduler.sqlite3");
        let c = if path.exists() {
            let c = Connection::open_with_flags(path, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)?;
            c.busy_timeout(Duration::from_millis(100))?;
            c
        } else {
            self.connect()?
        };
        let json: String =
            c.query_row("SELECT config FROM scheduler WHERE id=1", [], |r| r.get(0))?;
        let value: Schedule = serde_json::from_str(&json)?;
        value.validate()?;
        Ok(value)
    }
    fn save(&self, config: &Schedule) -> Result<()> {
        config.validate()?;
        self.connect()?.execute(
            "UPDATE scheduler SET config=?1,last_error=NULL WHERE id=1",
            [serde_json::to_string(config)?],
        )?;
        Ok(())
    }
    fn phase(&self, phase: &str, lease: i64) -> Result<()> {
        self.connect()?.execute(
            "UPDATE scheduler SET phase=?1,lease_until=?2 WHERE id=1",
            params![phase, lease],
        )?;
        Ok(())
    }
    fn error(&self, error: Option<&str>) -> Result<()> {
        self.connect()?
            .execute("UPDATE scheduler SET last_error=?1 WHERE id=1", [error])?;
        Ok(())
    }
    fn due(&self, now: i64) -> Result<bool> {
        let (next, count, day): (i64, u32, i64) = self.connect()?.query_row(
            "SELECT next_attempt,retry_count,retry_day FROM scheduler WHERE id=1",
            [],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
        )?;
        Ok(now >= next && (day != now.div_euclid(86_400_000) || count < 3))
    }
    // Persistent backoff: at most three failed attempts per UTC day, also bounded by D1 budgets.
    fn outcome(&self, failed: bool, now: i64) -> Result<()> {
        let day = now.div_euclid(86_400_000);
        self.connect()?.execute("UPDATE scheduler SET retry_count=CASE WHEN ?1=0 THEN retry_count WHEN retry_day=?2 THEN retry_count+1 ELSE 1 END,retry_day=CASE WHEN ?1 THEN ?2 ELSE retry_day END,next_attempt=?3,phase=?4,lease_until=0 WHERE id=1", params![failed,day,now+if failed {300_000} else {60_000},if failed {"deferred"} else {"idle"}])?;
        Ok(())
    }
}

pub trait Launchctl: Send + Sync {
    fn run(&self, args: &[&str]) -> Result<bool>;
}
struct SystemLaunchctl;
impl Launchctl for SystemLaunchctl {
    fn run(&self, args: &[&str]) -> Result<bool> {
        let mut child = Command::new("/bin/launchctl")
            .args(args)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()?;
        let start = Instant::now();
        loop {
            if let Some(status) = child.try_wait()? {
                return Ok(status.success());
            }
            if start.elapsed() >= Duration::from_secs(2) {
                let _ = child.kill();
                let _ = child.wait();
                anyhow::bail!(
                    "launchctl non risponde. La preferenza resta salvata; usa Ripara pianificazione."
                );
            }
            thread::sleep(Duration::from_millis(20));
        }
    }
}
#[derive(Clone)]
pub struct Scheduler {
    store: Store,
    agents: PathBuf,
    helper: PathBuf,
    domain: String,
    launchctl: Arc<dyn Launchctl>,
}
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SchedulerStatus {
    pub schedule: Schedule,
    pub installed: bool,
    pub loaded: bool,
    pub matches_configuration: bool,
    pub helper_available: bool,
    pub worker_running: bool,
    pub recording: bool,
    pub phase: String,
    pub lease_until: i64,
    pub next_attempt: i64,
    pub retry_count: u32,
    pub last_error: Option<String>,
}
impl Scheduler {
    pub fn for_app(data: PathBuf, home: &Path) -> Result<Self> {
        let executable = std::env::current_exe()?;
        let parent = executable
            .parent()
            .context("Percorso applicazione non disponibile.")?;
        Ok(Self {
            store: Store::new(data),
            agents: home.join("Library/LaunchAgents"),
            helper: parent.join(HELPER),
            domain: format!("gui/{}", unsafe { libc::getuid() }),
            launchctl: Arc::new(SystemLaunchctl),
        })
    }
    fn plist_path(&self) -> PathBuf {
        self.agents.join(format!("{LABEL}.plist"))
    }
    fn target(&self) -> String {
        format!("{}/{LABEL}", self.domain)
    }
    pub fn status(&self) -> Result<SchedulerStatus> {
        let schedule = self.store.schedule()?;
        let path = self.plist_path();
        let installed = path.is_file();
        let mut inspection_error = None;
        let loaded = match self.launchctl.run(&["print", &self.target()]) {
            Ok(value) => value,
            Err(e) => {
                inspection_error = Some(e.to_string());
                false
            }
        };
        let matches_configuration =
            fs::read_to_string(path).is_ok_and(|s| s == self.plist(&schedule));
        let coordination = Coordination::new(&self.store.data);
        let idle_permit = coordination.worker()?;
        let worker_running = idle_permit.is_none();
        if idle_permit.is_some() {
            clear_scratch(&self.store.data)?;
        }
        drop(idle_permit);
        let (phase,lease_until,next_attempt,retry_count,retry_day,last_error) = self.store.connect()?.query_row("SELECT phase,lease_until,next_attempt,retry_count,retry_day,last_error FROM scheduler WHERE id=1", [], |r| Ok((r.get::<_,String>(0)?,r.get(1)?,r.get(2)?,r.get(3)?,r.get::<_,i64>(4)?,r.get::<_,Option<String>>(5)?)))?;
        Ok(SchedulerStatus {
            schedule,
            installed,
            loaded,
            matches_configuration,
            helper_available: self.bundled_helper(),
            worker_running,
            recording: coordination.recording(),
            phase: if !worker_running && phase == "running" {
                "interrupted".into()
            } else {
                phase
            },
            lease_until,
            next_attempt,
            retry_count: if retry_day == now_ms().div_euclid(86_400_000) {
                retry_count
            } else {
                0
            },
            last_error: inspection_error.or(last_error),
        })
    }
    fn bundled_helper(&self) -> bool {
        self.helper.is_file()
            && self.helper.parent().is_some_and(|p| {
                p.file_name().is_some_and(|n| n == "MacOS")
                    && p.parent()
                        .is_some_and(|p| p.file_name().is_some_and(|n| n == "Contents"))
            })
    }
    fn plist(&self, config: &Schedule) -> String {
        // Every five minutes within the window plus its exact start. Missing spring-time
        // instants cannot strand the rest of a window. launchd coalesces sleeping firings.
        let times = (0..1440u16).filter(|m| config.contains(*m) && (*m % 5 == 0 || *m == config.start_minute)).map(|m| format!("<dict><key>Hour</key><integer>{}</integer><key>Minute</key><integer>{}</integer></dict>",m/60,m%60)).collect::<String>();
        format!(
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n<plist version=\"1.0\"><dict><key>Label</key><string>{LABEL}</string><key>ProgramArguments</key><array><string>{}</string><string>--data</string><string>{}</string></array><key>StartCalendarInterval</key><array>{times}</array><key>ProcessType</key><string>Background</string><key>LowPriorityIO</key><true/><key>Nice</key><integer>10</integer><key>RunAtLoad</key><false/><key>KeepAlive</key><false/><key>ThrottleInterval</key><integer>60</integer><key>ExitTimeOut</key><integer>2</integer></dict></plist>\n",
            xml(&self.helper.to_string_lossy()),
            xml(&self.store.data.to_string_lossy())
        )
    }
    pub fn configure(&self, schedule: Schedule, consent: bool) -> Result<SchedulerStatus> {
        schedule.validate()?;
        ensure!(
            !schedule.enabled || consent,
            "Conferma l'attivazione della pianificazione nell'app."
        );
        if schedule.enabled {
            ensure!(
                self.bundled_helper(),
                "Helper non disponibile nel bundle. Usa la build firmata di VoiceFlow."
            );
            ensure!(
                AnalysisStore::open(
                    self.store.data.join("history.sqlite3"),
                    self.store.data.join("dictionary.sqlite3")
                )
                .status(now_ms())?
                .settings
                .enabled,
                "Salva prima il consenso all'analisi con OpenAI."
            );
        }
        // Serialize reconciliation without holding a database transaction across launchctl.
        fs::create_dir_all(&self.store.data)?;
        let waiting = Instant::now();
        let _permit = loop {
            if let Some(permit) =
                Permit::try_acquire(&self.store.data.join("scheduler-config.lock"))?
            {
                break permit;
            }
            ensure!(
                waiting.elapsed() < Duration::from_secs(2),
                "Configurazione della pianificazione già in corso."
            );
            thread::sleep(Duration::from_millis(10));
        };
        self.store.save(&schedule)?; // Revocation precedes any fallible launchctl operation.
        if let Err(error) = self.reconcile(&schedule) {
            self.store.error(Some(&error.to_string()))?;
        }
        self.status()
    }
    fn reconcile(&self, schedule: &Schedule) -> Result<()> {
        let target = self.target();
        let path = self.plist_path();
        if !schedule.enabled {
            ensure!(
                self.launchctl.run(&["disable", &target])?,
                "Disattivazione launchd non riuscita. Il worker rispetta comunque la revoca salvata."
            );
            if self.launchctl.run(&["print", &target])? {
                ensure!(
                    self.launchctl.run(&["bootout", &target])?,
                    "Rimozione del job non riuscita. Riprova la disattivazione."
                );
            }
            if path.exists() {
                fs::remove_file(path)?;
            }
            return Ok(());
        }
        let plist = self.plist(schedule);
        let changed = fs::read_to_string(&path).ok().as_deref() != Some(&plist);
        let loaded = self.launchctl.run(&["print", &target])?;
        if changed && loaded {
            ensure!(
                self.launchctl.run(&["bootout", &target])?,
                "Aggiornamento del job non riuscito. Usa Ripara pianificazione."
            );
        }
        fs::create_dir_all(&self.agents)?;
        if changed {
            let temp = path.with_extension("plist.tmp");
            let mut file = fs::OpenOptions::new()
                .create(true)
                .write(true)
                .truncate(true)
                .mode(0o600)
                .custom_flags(libc::O_NOFOLLOW)
                .open(&temp)?;
            file.write_all(plist.as_bytes())?;
            file.sync_all()?;
            fs::rename(temp, &path)?;
        }
        ensure!(
            self.launchctl.run(&["enable", &target])?,
            "Abilitazione launchd non riuscita. Usa Ripara pianificazione."
        );
        if changed || !loaded {
            ensure!(
                self.launchctl
                    .run(&["bootstrap", &self.domain, &path.to_string_lossy()])?,
                "Installazione launchd non riuscita. Preferenza salvata; usa Ripara pianificazione."
            );
        }
        Ok(())
    }
}
fn xml(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&apos;")
}
fn clear_scratch(data: &Path) -> Result<()> {
    let path = data.join("analysis-runtime");
    if path.exists() {
        fs::remove_dir_all(path).context("File temporanei dell'analisi non rimossi.")?;
    }
    Ok(())
}
#[tauri::command]
pub async fn scheduler_status(
    state: tauri::State<'_, Scheduler>,
) -> Result<SchedulerStatus, String> {
    let state = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || state.status())
        .await
        .map_err(|e| e.to_string())?
        .map_err(|e| e.to_string())
}
#[tauri::command]
pub async fn scheduler_configure(
    state: tauri::State<'_, Scheduler>,
    schedule: Schedule,
    consent: bool,
) -> Result<SchedulerStatus, String> {
    let state = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || state.configure(schedule, consent))
        .await
        .map_err(|e| e.to_string())?
        .map_err(|e| e.to_string())
}
