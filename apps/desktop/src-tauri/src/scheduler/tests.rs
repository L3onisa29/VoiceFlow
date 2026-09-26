use super::*;
use crate::{
    analysis::AnalysisSettings,
    history::{CompletedDictation, HistoryStore},
};
use std::{
    os::unix::fs::PermissionsExt,
    sync::{
        Mutex,
        atomic::{AtomicU64, Ordering},
    },
};
static NEXT: AtomicU64 = AtomicU64::new(0);
struct Fixture {
    data: PathBuf,
}
impl Fixture {
    fn new() -> Self {
        let data = std::env::temp_dir().join(format!(
            "vf-scheduler-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir_all(&data).unwrap();
        Self { data }
    }
    fn store(&self) -> Store {
        Store::new(self.data.clone())
    }
    fn analysis(&self) -> AnalysisStore {
        AnalysisStore::open(
            self.data.join("history.sqlite3"),
            self.data.join("dictionary.sqlite3"),
        )
    }
    fn ready(&self, body: &str) {
        self.analysis()
            .configure(
                AnalysisSettings {
                    enabled: true,
                    max_requests_per_day: 20,
                    request_timeout_seconds: 10,
                    ..Default::default()
                },
                true,
            )
            .unwrap();
        HistoryStore::new(self.data.join("history.sqlite3"))
            .record(&CompletedDictation {
                session_key: "fixture",
                created_at: 1,
                text: "voice flow fixture",
                duration_seconds: 1.0,
                language: "it",
                original_asr: Some(&["voice flow fixture".into()]),
                processing: None,
            })
            .unwrap();
        let minute = local_minute(Local::now());
        self.store()
            .save(&Schedule {
                enabled: true,
                start_minute: (minute + 1439) % 1440,
                end_minute: (minute + 60) % 1440,
            })
            .unwrap();
        fs::write(
            self.data.join("codex"),
            format!(
                "#!/bin/sh\necho $$ > '{}/provider.pid'\ncat >/dev/null\n{body}\n",
                self.data.display()
            ),
        )
        .unwrap();
        fs::set_permissions(self.data.join("codex"), fs::Permissions::from_mode(0o700)).unwrap();
        std::os::unix::fs::symlink("/usr/bin/true", self.data.join("node")).unwrap();
    }
    fn launch(&self) -> std::process::Child {
        runtime::role_command("--launch", &self.data, -1)
            .unwrap()
            .env("PATH", format!("{}:/usr/bin:/bin", self.data.display()))
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .unwrap()
    }
    fn pid(&self, name: &str) -> i32 {
        let start = Instant::now();
        while !self.data.join(name).is_file() {
            assert!(
                start.elapsed() < Duration::from_secs(8),
                "Missing {name}; analysis={:?}",
                self.analysis().status(now_ms())
            );
            thread::sleep(Duration::from_millis(10));
        }
        fs::read_to_string(self.data.join(name))
            .unwrap()
            .trim()
            .parse()
            .unwrap()
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.data);
    }
}
fn wait_for(mut predicate: impl FnMut() -> bool) {
    let start = Instant::now();
    while !predicate() {
        assert!(start.elapsed() < Duration::from_secs(8), "timed out");
        thread::sleep(Duration::from_millis(10));
    }
}
fn dead(pid: i32) -> bool {
    unsafe { libc::kill(pid, 0) != 0 }
}
fn signal(pid: i32, signal: i32) {
    assert_eq!(unsafe { libc::kill(pid, signal) }, 0);
}
#[test]
fn process_fixture() {
    if let Ok(args) = std::env::var("VF_SCHEDULER_CHILD_ARGS") {
        runtime::run(serde_json::from_str(&args).unwrap()).unwrap();
    }
}
#[test]
fn windows_defaults_overnight_boundaries_and_clock_transitions() {
    let mut s = Schedule {
        enabled: true,
        ..Default::default()
    };
    assert!(!s.contains(149));
    assert!(s.contains(150));
    assert!(s.contains(359));
    assert!(!s.contains(360));
    for (time, expected) in [
        ("2026-03-29T01:59:59+01:00", false),
        ("2026-03-29T03:00:00+02:00", true), // missing 02:30
        ("2026-10-25T02:40:00+02:00", true),
        ("2026-10-25T02:40:00+01:00", true), // repeated hour
        ("2026-09-25T06:00:00+02:00", false),
        ("2026-09-25T04:00:00+00:00", true), // timezone switch, same instant
        ("2026-09-25T11:00:00+02:00", false), // wake outside window / forward jump
    ] {
        assert_eq!(
            s.contains(local_minute(DateTime::parse_from_rfc3339(time).unwrap())),
            expected,
            "{time}"
        );
    }
    s.start_minute = 180;
    s.end_minute = 300;
    assert!(s.validate().is_ok());
    assert!(!s.contains(179));
    assert!(!s.contains(300));
    s.start_minute = 1380;
    s.end_minute = 60;
    assert!(s.validate().is_ok());
    assert!(s.contains(1439));
    assert!(s.contains(0));
    assert!(!s.contains(60));
    s.end_minute = 1380;
    assert!(s.validate().is_err());
    s.start_minute = 1440;
    assert!(s.validate().is_err());
    s.start_minute = 0;
    s.end_minute = 721;
    assert!(s.validate().is_err());
}
#[derive(Default)]
struct MockLaunch {
    state: Mutex<(bool, Vec<Vec<String>>, Option<String>)>,
}
impl Launchctl for MockLaunch {
    fn run(&self, args: &[&str]) -> Result<bool> {
        let mut state = self.state.lock().unwrap();
        state.1.push(args.iter().map(|s| s.to_string()).collect());
        if state.2.as_deref() == Some(args[0]) {
            return Ok(false);
        }
        match args[0] {
            "print" => Ok(state.0),
            "bootstrap" => {
                state.0 = true;
                Ok(true)
            }
            "bootout" => {
                state.0 = false;
                Ok(true)
            }
            _ => Ok(true),
        }
    }
}
fn manager(f: &Fixture, mock: Arc<MockLaunch>) -> Scheduler {
    let helper = f
        .data
        .join("VoiceFlow.app/Contents/MacOS/voiceflow-analysis");
    fs::create_dir_all(helper.parent().unwrap()).unwrap();
    fs::write(&helper, "").unwrap();
    Scheduler {
        store: f.store(),
        agents: f.data.join("LaunchAgents"),
        helper,
        domain: "gui/501".into(),
        launchctl: mock,
    }
}
#[test]
fn installation_update_disable_twice_and_recover_partial_failure() {
    let f = Fixture::new();
    f.ready("exit 0");
    let mock = Arc::new(MockLaunch::default());
    let m = manager(&f, mock.clone());
    let mut config = Schedule {
        enabled: true,
        ..Default::default()
    };
    assert!(m.configure(config.clone(), false).is_err());
    for _ in 0..2 {
        let state = m.configure(config.clone(), true).unwrap();
        assert!(state.loaded && state.installed && state.matches_configuration);
    }
    assert_eq!(
        mock.state
            .lock()
            .unwrap()
            .1
            .iter()
            .filter(|v| v[0] == "bootstrap")
            .count(),
        1
    );
    config.start_minute = 180;
    config.end_minute = 300;
    mock.state.lock().unwrap().2 = Some("bootstrap".into());
    let state = m.configure(config.clone(), true).unwrap();
    assert!(state.schedule.enabled && !state.loaded && state.last_error.is_some());
    mock.state.lock().unwrap().2 = None;
    assert!(m.configure(config.clone(), true).unwrap().loaded);
    let plist = fs::read_to_string(m.plist_path()).unwrap();
    assert!(plist.contains("StartCalendarInterval"));
    assert!(!plist.contains("StartInterval"));
    assert!(
        plist.contains("<key>Hour</key><integer>3</integer><key>Minute</key><integer>0</integer>")
    );
    config.enabled = false;
    mock.state.lock().unwrap().2 = Some("bootout".into());
    let state = m.configure(config.clone(), false).unwrap();
    assert!(!state.schedule.enabled && state.loaded && state.last_error.is_some());
    mock.state.lock().unwrap().2 = None;
    for _ in 0..2 {
        let state = m.configure(config.clone(), false).unwrap();
        assert!(!state.loaded && !state.installed && !state.schedule.enabled);
    }
}
#[test]
fn persistent_backoff_expired_lease_and_clock_jump_do_not_grant_ownership() {
    let f = Fixture::new();
    let store = f.store();
    assert!(!store.schedule().unwrap().enabled);
    let t = 1_800_000_000_000;
    for n in 0..3 {
        assert!(store.due(t + n * 300_000).unwrap());
        store.outcome(true, t + n * 300_000).unwrap();
        assert!(!store.due(t + n * 300_000 + 1).unwrap());
    }
    assert!(!store.due(t + 900_000).unwrap());
    assert!(store.due(t + 86_400_000).unwrap());
    assert!(!store.due(t - 1000).unwrap());
    let c = Coordination::new(&f.data);
    let _lock = c.worker().unwrap().unwrap();
    store.phase("running", 1).unwrap();
    assert!(c.worker().unwrap().is_none());
}
#[test]
fn multiprocess_single_worker_capture_and_crash_cleanup() {
    for mode in [
        "capture",
        "kill-launcher",
        "kill-executor",
        "stop-launcher",
        "stop-executor",
        "stop-watchdog",
    ] {
        let f = Fixture::new();
        f.ready(&format!(
            "/bin/sleep 30 &\necho $! > '{}/descendant.pid'\nwait",
            f.data.display()
        ));
        let mut launcher = f.launch();
        let provider = f.pid("provider.pid");
        let descendant = f.pid("descendant.pid");
        let executor = f.pid("executor.pid");
        let watchdog = f.pid("watchdog.pid");
        let c = Coordination::new(&f.data);
        assert!(c.worker().unwrap().is_none());
        assert!(c.work().unwrap().is_none());
        f.store().phase("running", 1).unwrap(); // An expired timestamp cannot steal live ownership.
        // A second OS process exits without a second provider attempt.
        assert!(f.launch().wait().unwrap().success());
        assert_eq!(f.analysis().status(now_ms()).unwrap().requests_today, 1);
        match mode {
            "kill-launcher" => signal(launcher.id() as i32, libc::SIGKILL),
            "kill-executor" => signal(executor, libc::SIGKILL),
            "stop-launcher" => signal(launcher.id() as i32, libc::SIGSTOP),
            "stop-executor" => signal(executor, libc::SIGSTOP),
            "stop-watchdog" => signal(watchdog, libc::SIGSTOP),
            _ => (),
        }
        let start = Instant::now();
        let capture = c.capture().unwrap();
        assert!(!capture.degraded(), "{mode}");
        assert!(start.elapsed() < Duration::from_millis(1500), "{mode}");
        wait_for(|| dead(provider) && dead(descendant));
        assert!(c.recording());
        assert!(c.work().unwrap().is_none());
        if mode == "stop-launcher" {
            signal(launcher.id() as i32, libc::SIGCONT);
        }
        launcher.wait().unwrap();
        drop(capture);
        wait_for(|| c.worker().unwrap().is_some());
        assert!(!c.recording());
        manager(&f, Arc::new(MockLaunch::default()))
            .status()
            .unwrap();
        assert!(!f.data.join("analysis-runtime").exists());
        // An expired lease never substitutes for the kernel lock. Only now is D1 recovery valid.
        f.analysis().recover_interrupted().unwrap();
        assert_eq!(f.analysis().status(now_ms()).unwrap().requests_today, 1);
        assert!(f.analysis().prepare().unwrap().is_some());
    }
}
#[test]
fn provider_timeout_consent_revoke_and_outside_window_do_not_overlap_capture() {
    for mode in ["timeout", "revoke", "window"] {
        let f = Fixture::new();
        f.ready("/bin/sleep 30");
        if mode == "timeout" {
            f.analysis()
                .configure(
                    AnalysisSettings {
                        enabled: true,
                        request_timeout_seconds: 1,
                        ..Default::default()
                    },
                    true,
                )
                .unwrap();
        }
        let mut launcher = f.launch();
        if mode == "timeout" {
            // Under a busy full-suite run the request may expire before the shell gets
            // CPU to write its first marker. That is also a valid bounded cancellation.
            let start = Instant::now();
            wait_for(|| launcher.try_wait().unwrap().is_some());
            assert!(start.elapsed() < Duration::from_secs(5));
            if f.data.join("provider.pid").exists() {
                wait_for(|| dead(f.pid("provider.pid")));
            }
            assert_eq!(f.analysis().status(now_ms()).unwrap().requests_today, 1);
            assert!(!Coordination::new(&f.data).capture().unwrap().degraded());
            continue;
        }
        let provider = f.pid("provider.pid");
        if mode == "revoke" {
            f.analysis()
                .configure(AnalysisSettings::default(), false)
                .unwrap();
        }
        if mode == "window" {
            let mut schedule = f.store().schedule().unwrap();
            schedule.start_minute = (local_minute(Local::now()) + 10) % 1440;
            schedule.end_minute = (schedule.start_minute + 30) % 1440;
            f.store().save(&schedule).unwrap();
        }
        wait_for(|| dead(provider));
        launcher.wait().unwrap();
        assert!(Coordination::new(&f.data).capture().is_ok());
    }
    let f = Fixture::new();
    f.ready("exit 99");
    let mut s = f.store().schedule().unwrap();
    s.enabled = false;
    f.store().save(&s).unwrap();
    assert!(f.launch().wait().unwrap().success());
    assert!(!f.data.join("provider.pid").exists());
}
#[test]
fn real_provider_faults_are_persisted_and_retry_is_bounded() {
    for body in [
        "exit 23",
        "printf 'malformed'",
        "printf '%s\\n' '{\"type\":\"turn.failed\"}'",
    ] {
        let f = Fixture::new();
        f.ready(body);
        assert!(f.launch().wait().unwrap().success());
        let status = f.analysis().status(now_ms()).unwrap();
        assert_eq!(status.requests_today, 1);
        assert_eq!(status.last_state.as_deref(), Some("failed"));
        assert!(status.last_error.is_some());
        assert!(f.launch().wait().unwrap().success());
        assert_eq!(f.analysis().status(now_ms()).unwrap().requests_today, 1);
        assert!(!f.store().due(now_ms()).unwrap());
    }
}
#[test]
fn broken_scheduler_database_does_not_block_capture_and_stale_capture_releases() {
    let f = Fixture::new();
    fs::write(f.data.join("analysis-scheduler.sqlite3"), b"broken").unwrap();
    let c = Coordination::new(&f.data);
    let capture = c.capture().unwrap();
    assert!(!capture.degraded());
    assert!(f.store().schedule().is_err());
    drop(capture);
    assert!(!c.recording());
}
#[test]
fn capture_process_crash_and_start_races_release_kernel_state() {
    let f = Fixture::new();
    f.ready("/bin/sleep 30");
    let mut capture = runtime::role_command("--capture-fixture", &f.data, -1)
        .unwrap()
        .stdout(Stdio::null())
        .spawn()
        .unwrap();
    f.pid("capture.pid");
    assert!(f.launch().wait().unwrap().success());
    assert!(!f.data.join("provider.pid").exists());
    capture.kill().unwrap();
    capture.wait().unwrap();
    assert!(!Coordination::new(&f.data).recording());
    for _ in 0..12 {
        let mut launcher = f.launch();
        let permit = Coordination::new(&f.data).capture().unwrap();
        assert!(!permit.degraded());
        launcher.wait().unwrap();
        assert!(Coordination::new(&f.data).work().unwrap().is_none());
        if f.data.join("provider.pid").exists() {
            assert!(dead(f.pid("provider.pid")));
        }
        drop(permit);
    }
}
#[test]
fn controlled_clock_drives_actual_worker_deadline_dst_timezone_and_wake() {
    let cases = [
        ("2026-03-29T03:00:00+02:00", "2026-03-29T06:00:00+02:00"),
        ("2026-10-25T02:40:00+02:00", "2026-10-25T06:00:00+01:00"),
        ("2026-09-25T04:00:00+00:00", "2026-09-25T06:00:00+02:00"),
        ("2026-09-25T03:30:00+02:00", "2026-09-25T01:30:00+02:00"), // backward jump out
    ];
    for (initial, final_time) in cases {
        let f = Fixture::new();
        f.ready("/bin/sleep 30");
        f.store()
            .save(&Schedule {
                enabled: true,
                ..Default::default()
            })
            .unwrap();
        let clock = f.data.join("clock");
        fs::write(&clock, initial).unwrap();
        let mut launcher = runtime::role_command("--launch", &f.data, -1)
            .unwrap()
            .env("PATH", format!("{}:/usr/bin:/bin", f.data.display()))
            .env("VF_SCHEDULER_CLOCK", &clock)
            .stdout(Stdio::null())
            .spawn()
            .unwrap();
        let provider = f.pid("provider.pid");
        fs::write(&clock, final_time).unwrap();
        wait_for(|| dead(provider));
        launcher.wait().unwrap();
        fs::remove_file(f.data.join("provider.pid")).unwrap();
        let mut wake = runtime::role_command("--launch", &f.data, -1)
            .unwrap()
            .env("PATH", format!("{}:/usr/bin:/bin", f.data.display()))
            .env("VF_SCHEDULER_CLOCK", &clock)
            .stdout(Stdio::null())
            .spawn()
            .unwrap();
        assert!(wake.wait().unwrap().success());
        assert!(!f.data.join("provider.pid").exists());
    }
}
#[test]
fn completed_checkpoint_does_not_repeat_in_fall_hour_or_after_restart() {
    let f = Fixture::new();
    f.ready("printf '%s\\n' '{\"type\":\"item.completed\",\"item\":{\"type\":\"agent_message\",\"text\":\"{\\\"candidates\\\":[]}\"}}' '{\"type\":\"turn.completed\"}'");
    f.store()
        .save(&Schedule {
            enabled: true,
            ..Default::default()
        })
        .unwrap();
    let clock = f.data.join("clock");
    for time in ["2026-10-25T02:40:00+02:00", "2026-10-25T02:40:00+01:00"] {
        fs::write(&clock, time).unwrap();
        f.store()
            .connect()
            .unwrap()
            .execute("UPDATE scheduler SET next_attempt=0", [])
            .unwrap();
        assert!(
            runtime::role_command("--launch", &f.data, -1)
                .unwrap()
                .env("PATH", format!("{}:/usr/bin:/bin", f.data.display()))
                .env("VF_SCHEDULER_CLOCK", &clock)
                .stdout(Stdio::null())
                .spawn()
                .unwrap()
                .wait()
                .unwrap()
                .success()
        );
        assert_eq!(f.analysis().status(now_ms()).unwrap().requests_today, 1);
        assert_eq!(
            f.analysis().status(now_ms()).unwrap().last_state.as_deref(),
            Some("completed")
        );
    }
}

#[test]
fn invocation_history_is_bounded_and_preserves_unknown_crash_end() {
    let f = Fixture::new();
    let store = f.store();
    store.begin_invocation().unwrap();
    let id = store.begin_invocation().unwrap();
    store.finish_invocation(id, "no-work", None).unwrap();
    let records = store.invocations().unwrap();
    assert_eq!(records[0].reason, "no-work");
    assert!(records[0].ended_at.is_some());
    assert_eq!(records[1].reason, "interrupted");
    assert!(records[1].ended_at.is_none());
    for _ in 0..105 {
        let id = store.begin_invocation().unwrap();
        store.finish_invocation(id, "outside-window", None).unwrap();
    }
    let records = Store::new(f.data.clone()).invocations().unwrap();
    assert_eq!(records.len(), 100);
    assert!(records.windows(2).all(|p| p[0].id > p[1].id));
}
#[test]
fn launcher_skips_are_persistent_without_provider_calls() {
    let f = Fixture::new();
    runtime::run(vec!["--data".into(), f.data.to_string_lossy().into_owned()]).unwrap();
    assert_eq!(f.store().invocations().unwrap()[0].reason, "disabled");
    let minute = local_minute(Local::now());
    f.store()
        .save(&Schedule {
            enabled: true,
            start_minute: (minute + 60) % 1440,
            end_minute: (minute + 120) % 1440,
        })
        .unwrap();
    runtime::run(vec!["--data".into(), f.data.to_string_lossy().into_owned()]).unwrap();
    assert_eq!(f.store().invocations().unwrap()[0].reason, "outside-window");
    assert!(f.analysis().runs(None, 20).unwrap().entries.is_empty());
}
#[test]
fn background_no_work_and_failure_have_durable_diagnostics() {
    let f = Fixture::new();
    f.ready("exit 7");
    let mut child = f.launch();
    child.wait().unwrap();
    let runs = f.analysis().runs(None, 20).unwrap();
    assert_eq!(runs.entries[0].state, "failed");
    assert_eq!(runs.entries[0].error.as_deref(), Some("provider-exit"));
    assert_eq!(f.store().invocations().unwrap()[0].reason, "failed");
    let f = Fixture::new();
    f.ready("exit 7");
    HistoryStore::new(f.data.join("history.sqlite3"))
        .delete(1)
        .unwrap();
    let mut child = f.launch();
    child.wait().unwrap();
    assert_eq!(f.store().invocations().unwrap()[0].reason, "no-work");
    assert!(!f.data.join("provider.pid").exists());
}
