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
        return watchdog(&data);
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
        let result = execute(&data);
        if let Err(error) = result {
            let store = Store::new(data);
            let _ = store.error(Some(&format!("Analisi non completata: {error}")));
            let _ = store.outcome(true, now_ms());
        }
        kill_execution_group();
    }
    let store = Store::new(data.clone());
    let coordination = Coordination::new(&data);
    let Some(permit) = coordination.worker()? else {
        return Ok(());
    };
    clear_scratch(&data)?;
    if !store.schedule()?.current() || !store.due(now_ms())? {
        return Ok(());
    }
    if coordination.recording() || !consented(&data) {
        return Ok(());
    }
    let previous: String =
        store
            .connect()?
            .query_row("SELECT phase FROM scheduler WHERE id=1", [], |r| r.get(0))?;
    if previous == "running" {
        store.outcome(true, now_ms())?;
        store.error(Some("Worker interrotto. Checkpoint e budget conservati; nuovo tentativo dopo almeno 5 minuti."))?;
        return Ok(());
    }
    let fd = permit.inherit()?;
    fs::DirBuilder::new()
        .mode(0o700)
        .create(data.join("analysis-runtime"))?;
    let mut child = role_command("--execute", &data, fd)?
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
            || !store.schedule().is_ok_and(|s| s.current())
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
        store.outcome(true, now_ms())?;
        store.error(Some("Worker interrotto. Checkpoint e budget conservati; nuovo tentativo dopo almeno 5 minuti."))?;
    }
    Ok(())
}
pub(super) fn role_command(role: &str, data: &Path, fd: RawFd) -> Result<Command> {
    let args = vec![
        role.to_string(),
        "--data".into(),
        data.to_string_lossy().into_owned(),
        "--permit-fd".into(),
        fd.to_string(),
    ];
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
fn watchdog(data: &Path) -> Result<()> {
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
            || !store.schedule().is_ok_and(|s| s.current())
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
fn execute(data: &Path) -> Result<()> {
    let coordination = Coordination::new(data);
    let Some(work) = coordination.work()? else {
        return Ok(());
    };
    if coordination.recording() {
        return Ok(());
    }
    let work_fd = work.inherit()?;
    // No provider may start until watchdog spawn succeeds. It inherits the open
    // worker/work descriptions, as do all providers. A dead process never leaves a stale lock.
    let mut watchdog = role_command("--watchdog", data, work_fd)?
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
    let store = Store::new(data.to_path_buf());
    if !store.schedule()?.current() || !consented(data) || !store.due(now_ms())? {
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
            || !store.schedule().is_ok_and(|s| s.current())
            || !consented(data)
            || started.elapsed() >= Duration::from_secs(600)
    };
    if cancelled() {
        return Ok(());
    }
    let Some(batch) = analysis.prepare()? else {
        store.phase("idle", 0)?;
        return Ok(());
    };
    if cancelled() {
        return Ok(());
    }
    let provider =
        CodexProvider::in_worker_group(Executables::discover()?, data.join("analysis-runtime"))?;
    let outcome = analysis.run(batch, &provider, &cancelled, now_ms())?;
    store.error(None)?;
    store.outcome(outcome.state == "failed", now_ms())?;
    Ok(())
}
