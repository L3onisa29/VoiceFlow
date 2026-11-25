use super::*;
use crate::analysis::{CodexProvider, Executables};
use std::{io::Read, os::fd::RawFd, os::unix::process::CommandExt};

/// The launcher never opens transcription content. One execution is one bounded attempt.
/// A pipe connects launcher and executor; another connects executor and watchdog.
/// Each side cleans the execution group when its peer exits, including abrupt crashes.
pub fn entry() -> Result<()> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    run(args)
}
pub(super) fn run(args: Vec<String>) -> Result<()> {
    let value = |name: &str| args.windows(2).find(|v| v[0] == name).map(|v| v[1].clone());
    let origin = if args.iter().any(|a| a == "--manual") {
        RunOrigin::Manual
    } else {
        RunOrigin::Automatic
    };
    let data = PathBuf::from(value("--data").context("Directory dati mancante.")?);
    ensure!(data.is_absolute(), "Directory dati non assoluta.");
    #[cfg(test)]
    if args.iter().any(|a| a == "--capture-fixture") {
        let _capture = Coordination::new(&data).capture()?;
        fs::write(data.join("capture.pid"), std::process::id().to_string())?;
        thread::sleep(Duration::from_secs(30));
        return Ok(());
    }
    if args.iter().any(|a| a == "--watchdog") {
        Permit::validate_inherited(
            value("--permit-fd").context("Permesso mancante")?.parse()?,
            &Coordination::new(&data).root.join("work.lock"),
        )?;
        #[cfg(test)]
        fs::write(data.join("watchdog.pid"), std::process::id().to_string())?;
        // This entrypoint can only kill its own dedicated execution group, never a pidfile.
        ensure!(
            unsafe { libc::getpgrp() } != unsafe { libc::getpid() },
            "Watchdog senza gruppo del worker."
        );
        return watchdog(&data, origin);
    }
    if args.iter().any(|a| a == "--execute") {
        Permit::validate_inherited(
            value("--permit-fd").context("Permesso mancante")?.parse()?,
            &Coordination::new(&data).root.join("worker.lock"),
        )?;
        #[cfg(test)]
        fs::write(data.join("executor.pid"), std::process::id().to_string())?;
        ensure!(
            unsafe { libc::getpgrp() } == unsafe { libc::getpid() },
            "Worker senza gruppo isolato."
        );
        let invocation = value("--invocation")
            .filter(|v| !v.is_empty())
            .map(|v| v.parse::<i64>())
            .transpose()?;
        let result = execute(&data, invocation, origin);
        if result.is_err() {
            let store = Store::new(data);
            let _ = store.error(Some("Analisi non completata. Consulta Analisi per gli esiti; verifica disponibilità CLI e archivio."));
            store.note(invocation, "worker-error", None);
            let _ = store.outcome(true, now_ms());
        }
        kill_execution_group();
    }
    let store = Store::new(data.clone());
    let coordination = Coordination::new(&data);
    let permit = if let Some(fd) = value("--launcher-permit-fd") {
        Some(Permit::adopt(
            fd.parse()?,
            &coordination.root.join("worker.lock"),
        )?)
    } else {
        coordination.worker()?
    };
    let Some(permit) = permit else {
        return Ok(());
    };
    let invocation = if let Some(id) = value("--invocation") {
        let id: i64 = id.parse()?;
        ensure!(origin == RunOrigin::Manual && store.connect()?.query_row("SELECT EXISTS(SELECT 1 FROM scheduler_invocations WHERE id=?1 AND origin='manual' AND reason='requested')", [id], |r| r.get::<_, bool>(0))?, "Richiesta manuale non valida.");
        Some(id)
    } else {
        store.recover_invocations()?;
        Some(store.create_invocation(origin, "requested")?)
    };
    let _observation = super::invocations::Observation {
        store: &store,
        id: invocation,
    };
    clear_scratch(&data)?;
    let schedule = store.schedule()?;
    let skip = if origin == RunOrigin::Automatic && !schedule.enabled {
        Some("disabled")
    } else if origin == RunOrigin::Automatic && !schedule.current() {
        Some("outside-window")
    } else if !store.due(now_ms())? {
        Some("backoff")
    } else if coordination.recording() {
        Some("recording")
    } else if !consented(&data) {
        Some("consent-unavailable")
    } else if budget_spent(&data) {
        // A spent budget is not a failure: no worker, no batch, no retry counted.
        Some("daily-limit")
    } else {
        None
    };
    if let Some(reason) = skip {
        store.note_skip(invocation, reason, origin);
        return Ok(());
    }
    let previous: String =
        store
            .connect()?
            .query_row("SELECT phase FROM scheduler WHERE id=1", [], |r| r.get(0))?;
    if previous == "running" {
        let _ = AnalysisStore::open(
            data.join("history.sqlite3"),
            data.join("dictionary.sqlite3"),
        )
        .recover_interrupted();
        store.note(invocation, "interrupted", None);
        store.outcome(true, now_ms())?;
        store.error(Some("Worker interrotto. Checkpoint e budget conservati; nuovo tentativo dopo almeno 5 minuti."))?;
        return Ok(());
    }
    let fd = permit.inherit()?;
    fs::DirBuilder::new()
        .mode(0o700)
        .create(data.join("analysis-runtime"))?;
    let mut child = role_command_args(
        "--execute",
        &data,
        fd,
        &[
            if origin == RunOrigin::Manual {
                "--manual"
            } else {
                "--automatic"
            },
            "--invocation",
            &invocation.map(|id| id.to_string()).unwrap_or_default(),
        ],
    )?
    .stdin(Stdio::piped())
    .stdout(Stdio::null())
    .stderr(Stdio::null())
    .process_group(0)
    .spawn()?;
    let _parent_pipe = child.stdin.take();
    let start = Instant::now();
    let mut interrupted = None;
    loop {
        if exited_without_reaping(child.id())? {
            break;
        }
        if coordination.recording()
            || !allowed(&store, origin)
            || !consented(&data)
            || start.elapsed() > Duration::from_secs(630)
        {
            let since = interrupted.get_or_insert_with(Instant::now);
            if since.elapsed() >= Duration::from_millis(350) {
                break;
            }
        } else {
            interrupted = None;
        }
        thread::sleep(Duration::from_millis(20));
    }
    // Group id belongs to an unreaped child until wait, so it cannot be recycled here.
    kill_child_group(&mut child);
    clear_scratch(&data)?;
    let phase: String =
        store
            .connect()?
            .query_row("SELECT phase FROM scheduler WHERE id=1", [], |r| r.get(0))?;
    if phase == "running" {
        let _ = AnalysisStore::open(
            data.join("history.sqlite3"),
            data.join("dictionary.sqlite3"),
        )
        .recover_interrupted();
        store.note(invocation, "interrupted", None);
        store.outcome(true, now_ms())?;
        store.error(Some("Worker interrotto. Checkpoint e budget conservati; nuovo tentativo dopo almeno 5 minuti."))?;
    }
    Ok(())
}
#[cfg(test)]
pub(super) fn role_command(role: &str, data: &Path, fd: RawFd) -> Result<Command> {
    role_command_args(role, data, fd, &[])
}
pub(super) fn role_command_args(
    role: &str,
    data: &Path,
    fd: RawFd,
    extra: &[&str],
) -> Result<Command> {
    let mut args = vec![
        role.to_string(),
        "--data".into(),
        data.to_string_lossy().into_owned(),
        "--permit-fd".into(),
        fd.to_string(),
    ];
    args.extend(extra.iter().map(|s| s.to_string()));
    let mut command = Command::new(std::env::current_exe()?);
    #[cfg(not(test))]
    command.args(args);
    #[cfg(test)]
    command
        .args([
            "--exact",
            "scheduler::tests::process_fixture",
            "--nocapture",
        ])
        .env("VF_SCHEDULER_CHILD_ARGS", serde_json::to_string(&args)?);
    Ok(command)
}
fn exited_without_reaping(pid: u32) -> Result<bool> {
    let mut info: libc::siginfo_t = unsafe { std::mem::zeroed() };
    ensure!(
        unsafe {
            libc::waitid(
                libc::P_PID,
                pid,
                &mut info,
                libc::WEXITED | libc::WNOHANG | libc::WNOWAIT,
            )
        } == 0,
        "Stato worker non disponibile."
    );
    Ok(unsafe { info.si_pid() } != 0)
}
fn kill_child_group(child: &mut std::process::Child) {
    unsafe {
        libc::kill(-(child.id() as i32), libc::SIGKILL);
    }
    let _ = child.wait();
}
fn kill_execution_group() -> ! {
    unsafe {
        libc::kill(-libc::getpgrp(), libc::SIGKILL);
    }
    std::process::abort()
}
fn nonblocking(fd: RawFd) -> Result<()> {
    let flags = unsafe { libc::fcntl(fd, libc::F_GETFL) };
    ensure!(
        flags >= 0 && unsafe { libc::fcntl(fd, libc::F_SETFL, flags | libc::O_NONBLOCK) } >= 0,
        "Pipe di sorveglianza non disponibile."
    );
    Ok(())
}
fn pipe_closed() -> bool {
    let mut byte = [0u8];
    match std::io::stdin().read(&mut byte) {
        Ok(0) => true,
        Ok(_) => false,
        Err(e) => {
            e.kind() != std::io::ErrorKind::WouldBlock
                && e.kind() != std::io::ErrorKind::Interrupted
        }
    }
}
fn consented(data: &Path) -> bool {
    (|| -> Result<bool> {
        let c = Connection::open_with_flags(
            data.join("history.sqlite3"),
            rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY,
        )?;
        c.busy_timeout(Duration::from_millis(500))?;
        let json: String =
            c.query_row("SELECT json FROM analysis_settings WHERE id=1", [], |r| {
                r.get(0)
            })?;
        Ok(serde_json::from_str::<crate::analysis::AnalysisSettings>(&json)?.enabled)
    })()
    .unwrap_or(false)
}
/// Metadata only. An unreadable archive is left to the worker, which reports it properly.
fn budget_spent(data: &Path) -> bool {
    (|| -> Result<bool> {
        let c = Connection::open_with_flags(
            data.join("history.sqlite3"),
            rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY,
        )?;
        c.busy_timeout(Duration::from_millis(500))?;
        let json: String =
            c.query_row("SELECT json FROM analysis_settings WHERE id=1", [], |r| {
                r.get(0)
            })?;
        let config = serde_json::from_str::<crate::analysis::AnalysisSettings>(&json)?;
        Ok(crate::analysis::budget_left(&c, &config, now_ms())?.is_none())
    })()
    .unwrap_or(false)
}
fn watchdog(data: &Path, origin: RunOrigin) -> Result<()> {
    nonblocking(0)?;
    let store = Store::new(data.to_path_buf());
    let coordination = Coordination::new(data);
    let started = Instant::now();
    let mut stop_since = None;
    loop {
        if pipe_closed() {
            kill_execution_group();
        }
        if coordination.recording()
            || !allowed(&store, origin)
            || !consented(data)
            || started.elapsed() >= Duration::from_secs(620)
        {
            let since = stop_since.get_or_insert_with(Instant::now);
            if since.elapsed() >= Duration::from_millis(250) {
                kill_execution_group();
            }
        } else {
            stop_since = None;
        }
        thread::sleep(Duration::from_millis(20));
    }
}
fn execute(data: &Path, invocation: Option<i64>, origin: RunOrigin) -> Result<()> {
    let store = Store::new(data.to_path_buf());
    let coordination = Coordination::new(data);
    let Some(work) = coordination.work()? else {
        store.note(invocation, "recording", None);
        return Ok(());
    };
    if coordination.recording() {
        store.note(invocation, "recording", None);
        return Ok(());
    }
    let work_fd = work.inherit()?;
    // No provider may start until watchdog spawn succeeds. It inherits the open
    // worker/work descriptions, as do all providers. A dead process never leaves a stale lock.
    let mut watchdog = role_command_args(
        "--watchdog",
        data,
        work_fd,
        &[if origin == RunOrigin::Manual {
            "--manual"
        } else {
            "--automatic"
        }],
    )?
    .stdin(Stdio::piped())
    .stdout(Stdio::null())
    .stderr(Stdio::null())
    .spawn()?;
    let _watchdog_pipe = watchdog.stdin.take();
    // If launchd/launcher disappears, this thread kills the entire execution group.
    thread::spawn(|| {
        let mut byte = [0u8];
        loop {
            match std::io::stdin().read(&mut byte) {
                Ok(0) | Err(_) => kill_execution_group(),
                Ok(_) => (),
            }
        }
    });
    if !allowed(&store, origin) || !consented(data) || !store.due(now_ms())? {
        store.note(invocation, "conditions-changed", None);
        return Ok(());
    }
    store.phase("running", now_ms() + 630_000)?;
    let analysis = AnalysisStore::open(
        data.join("history.sqlite3"),
        data.join("dictionary.sqlite3"),
    );
    // Kernel lock inherited from the launcher proves all previous execution descendants gone.
    analysis.recover_interrupted()?;
    let started = Instant::now();
    let cancelled = || {
        coordination.recording()
            || !allowed(&store, origin)
            || !consented(data)
            || started.elapsed() >= Duration::from_secs(600)
    };
    if cancelled() {
        store.note(invocation, "cancelled", None);
        return Ok(());
    }
    let Some(batch) = analysis.prepare()? else {
        store.phase("idle", 0)?;
        store.note(invocation, "no-work", None);
        return Ok(());
    };
    if cancelled() {
        store.note(invocation, "cancelled", None);
        return Ok(());
    }
    let batch_id = batch.id();
    if let Some(id) = invocation {
        let _ = store.invocation_batch(id, batch_id);
    }
    let executables = Executables::discover().inspect_err(|_| {
        store.note(invocation, "provider-unavailable", Some(batch.id()));
    })?;
    let provider = CodexProvider::in_worker_group(executables, data.join("analysis-runtime"))?;
    if let Some(id) = invocation {
        store.connect()?.execute(
            "UPDATE scheduler_invocations SET reason='running' WHERE id=?1 AND reason='requested'",
            [id],
        )?;
    }
    let outcome =
        match analysis.run_invoked(batch, &provider, &cancelled, now_ms(), origin, invocation) {
            Ok(outcome) => outcome,
            // The launcher checks the budget first; this covers a request racing past it.
            // Nothing was sent, so it must not consume a retry or raise an error.
            Err(error) if error.is::<crate::analysis::DailyLimit>() => {
                store.note(invocation, "daily-limit", Some(batch_id));
                store.phase("idle", 0)?;
                return Ok(());
            }
            Err(error) => return Err(error),
        };
    store.note(invocation, &outcome.state, Some(outcome.batch_id));
    store.error(None)?;
    store.outcome(outcome.state == "failed", now_ms())?;
    Ok(())
}

fn allowed(store: &Store, origin: RunOrigin) -> bool {
    origin == RunOrigin::Manual || store.schedule().is_ok_and(|s| s.current())
}
