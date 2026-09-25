use anyhow::{Context, Result, ensure};
use serde::Deserialize;
use std::{
    fs,
    io::{Read, Write},
    os::unix::{
        fs::{DirBuilderExt, PermissionsExt},
        process::CommandExt,
    },
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::{
        Arc,
        atomic::{AtomicBool, AtomicU64, Ordering},
    },
    thread,
    time::{Duration, Instant},
};

pub trait Cancellation: Send + Sync {
    fn cancelled(&self) -> bool;
}
impl<F: Fn() -> bool + Send + Sync> Cancellation for F {
    fn cancelled(&self) -> bool {
        self()
    }
}
impl Cancellation for AtomicBool {
    fn cancelled(&self) -> bool {
        self.load(Ordering::Acquire)
    }
}
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProviderReply {
    pub candidates: Vec<serde_json::Value>,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProviderError {
    Unavailable,
    Cancelled,
    Timeout,
    OutputLimit,
    Exit,
    Malformed,
    Io,
}
impl ProviderError {
    pub fn code(&self) -> &'static str {
        match self {
            Self::Unavailable => "provider-unavailable",
            Self::Cancelled => "cancelled",
            Self::Timeout => "timeout",
            Self::OutputLimit => "output-limit",
            Self::Exit => "provider-exit",
            Self::Malformed => "invalid-output",
            Self::Io => "provider-io",
        }
    }
}
pub trait Provider: Send + Sync {
    fn analyze(
        &self,
        prompt: &str,
        timeout: Duration,
        cancel: &dyn Cancellation,
    ) -> std::result::Result<ProviderReply, ProviderError>;
}
#[derive(Debug, Clone)]
pub struct Executables {
    pub codex: PathBuf,
    pub node: PathBuf,
}
fn executable(path: &Path) -> bool {
    fs::metadata(path).is_ok_and(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
}
impl Executables {
    /// Works under launchd without an interactive shell or a version-pinned path.
    /// Only executable paths are inspected. No account/config/credential files are read.
    pub fn discover() -> Result<Self> {
        let path = std::env::var_os("PATH").unwrap_or_default();
        let mut dirs: Vec<PathBuf> = std::env::split_paths(&path).collect();
        if let Some(home) = std::env::var_os("HOME") {
            let versions = PathBuf::from(home).join(".nvm/versions/node");
            let mut nvm = fs::read_dir(versions)
                .into_iter()
                .flatten()
                .flatten()
                .map(|e| e.path())
                .filter(|p| {
                    p.file_name()
                        .is_some_and(|n| n.to_string_lossy().starts_with("v24."))
                })
                .collect::<Vec<_>>();
            nvm.sort_by_key(|p| {
                p.file_name()
                    .unwrap_or_default()
                    .to_string_lossy()
                    .split('.')
                    .skip(1)
                    .filter_map(|s| s.parse::<u32>().ok())
                    .collect::<Vec<_>>()
            });
            dirs.extend(nvm.into_iter().rev().map(|p| p.join("bin")));
        }
        dirs.extend([
            PathBuf::from("/opt/homebrew/bin"),
            PathBuf::from("/usr/local/bin"),
            PathBuf::from("/usr/bin"),
        ]);
        let codex = dirs
            .iter()
            .map(|d| d.join("codex"))
            .find(|p| executable(p))
            .context("Codex CLI non trovato. Installa Codex e Node, poi riprova.")?;
        let node = codex
            .parent()
            .map(|p| p.join("node"))
            .filter(|p| executable(p))
            .or_else(|| dirs.iter().map(|d| d.join("node")).find(|p| executable(p)))
            .context("Node non trovato per Codex CLI.")?;
        Ok(Self { codex, node })
    }
}
pub struct CodexProvider {
    executables: Executables,
    worker_group: bool,
    scratch_parent: Option<PathBuf>,
}
impl CodexProvider {
    pub fn new(executables: Executables) -> Result<Self> {
        ensure!(
            executable(&executables.codex) && executable(&executables.node),
            "Eseguibili Codex/Node non disponibili."
        );
        Ok(Self {
            executables,
            worker_group: false,
            scratch_parent: None,
        })
    }
    /// Only for the isolated scheduler execution group, which has an independent watchdog.
    pub(crate) fn in_worker_group(
        executables: Executables,
        scratch_parent: PathBuf,
    ) -> Result<Self> {
        let mut provider = Self::new(executables)?;
        provider.worker_group = true;
        provider.scratch_parent = Some(scratch_parent);
        Ok(provider)
    }
}
static NEXT: AtomicU64 = AtomicU64::new(0);
struct Scratch(PathBuf);
impl Scratch {
    fn new() -> std::io::Result<Self> {
        Self::within(&std::env::temp_dir())
    }
    fn within(base: &Path) -> std::io::Result<Self> {
        loop {
            let path = base.join(format!(
                "voiceflow-analysis-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            match fs::DirBuilder::new().mode(0o700).create(&path) {
                Ok(()) => return Ok(Self(path)),
                Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(e) => return Err(e),
            }
        }
    }
}
impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}
const OUTPUT_CAP: usize = 524_288;
// Capability switches verified against installed CLI 0.157.0. Unsupported future flags fail closed.
const DISABLED: &[&str] = &[
    "artifact",
    "agent_message_board",
    "standalone_web_search",
    "tool_call_mcp_elicitation",
    "realtime_conversation",
    "in_app_chat",
    "in_app_dictation",
    "in_app_updates",
    "plugin_sharing",
    "external_agent_memory_import",
    "recommended_plugins",
    "request_permissions_tool",
    "default_mode_request_user_input",
    "shell_tool",
    "unified_exec",
    "multi_agent",
    "multi_agent_v2",
    "apps",
    "plugins",
    "remote_plugin",
    "hooks",
    "skill_search",
    "skill_mcp_dependency_install",
    "in_app_browser",
    "browser_use",
    "browser_use_external",
    "browser_use_full_cdp_access",
    "computer_use",
    "image_generation",
    "view_image",
    "code_mode",
    "code_mode_host",
    "code_mode_only",
    "in_app_local_automation",
    "workspace_dependencies",
    "memories",
    "shell_snapshot",
    "goals",
    "sleep_tool",
    "tool_suggest",
    "auth_elicitation",
    "daemon_auto_start",
    "unbounded_connection_retries",
];
fn command(executables: &Executables, scratch: &Path, schema: &Path) -> Command {
    let mut cmd = Command::new(&executables.codex);
    cmd.current_dir(scratch).env_clear();
    for name in ["HOME", "CODEX_HOME"] {
        if let Some(value) = std::env::var_os(name) {
            cmd.env(name, value);
        }
    }
    cmd.env(
        "PATH",
        format!(
            "{}:/usr/bin:/bin",
            executables
                .node
                .parent()
                .unwrap_or(Path::new("/usr/bin"))
                .display()
        ),
    )
    .env("RUST_LOG", "off")
    .env("NO_COLOR", "1");
    cmd.args([
        "exec",
        "--ephemeral",
        "--strict-config",
        "--ignore-user-config",
        "--ignore-rules",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "--json",
        "--color",
        "never",
        "--output-schema",
    ])
    .arg(schema);
    for feature in DISABLED {
        cmd.args(["--disable", feature]);
    }
    cmd.args(["--enable", "skip_host_skill_discovery"]);
    for setting in [
        "model_provider=\"openai\"",
        "tools.view_image=false",
        "memories.use_memories=false",
        "approval_policy=\"never\"",
        "web_search=\"disabled\"",
        "project_doc_max_bytes=0",
        "mcp_servers={}",
        "apps._default.enabled=false",
        "history.persistence=\"none\"",
        "feedback.enabled=false",
        "analytics.enabled=false",
        "otel.log_user_prompt=false",
    ] {
        cmd.args(["-c", setting]);
    }
    for key in ["log_dir", "sqlite_home"] {
        cmd.arg("-c").arg(format!(
            "{key}={}",
            serde_json::to_string(&scratch.to_string_lossy()).expect("path serializes")
        ));
    }
    cmd.arg("-")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .process_group(0);
    cmd
}
fn reader(
    mut stream: impl Read + Send + 'static,
    overflow: Arc<AtomicBool>,
) -> thread::JoinHandle<std::io::Result<Vec<u8>>> {
    thread::spawn(move || {
        let mut result = Vec::new();
        let mut chunk = [0u8; 8192];
        loop {
            let n = stream.read(&mut chunk)?;
            if n == 0 {
                break;
            }
            if result.len() + n > OUTPUT_CAP {
                overflow.store(true, Ordering::Release);
                break;
            }
            result.extend_from_slice(&chunk[..n]);
        }
        Ok(result)
    })
}
fn terminate(child: &mut std::process::Child) {
    // Kill the isolated process group, including the Node launcher and its native child.
    let _ = Command::new("/bin/kill")
        .args(["-KILL", "--", &format!("-{}", child.id())])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
    let _ = child.kill();
    let _ = child.wait();
}
impl Provider for CodexProvider {
    fn analyze(
        &self,
        prompt: &str,
        timeout: Duration,
        cancel: &dyn Cancellation,
    ) -> std::result::Result<ProviderReply, ProviderError> {
        if cancel.cancelled() {
            return Err(ProviderError::Cancelled);
        }
        let scratch = match &self.scratch_parent {
            Some(parent) => Scratch::within(parent),
            None => Scratch::new(),
        }
        .map_err(|_| ProviderError::Io)?;
        let schema = scratch.0.join("output-schema.json");
        fs::write(
            &schema,
            include_str!("../../../../../docs/schemas/analysis-output-v1.json"),
        )
        .map_err(|_| ProviderError::Io)?;
        let mut cmd = command(&self.executables, &scratch.0, &schema);
        if self.worker_group {
            cmd.process_group(unsafe { libc::getpgrp() });
        }
        let mut child = cmd.spawn().map_err(|_| ProviderError::Unavailable)?;
        let overflow = Arc::new(AtomicBool::new(false));
        let stdout = reader(
            child.stdout.take().ok_or(ProviderError::Io)?,
            overflow.clone(),
        );
        let stderr = reader(
            child.stderr.take().ok_or(ProviderError::Io)?,
            overflow.clone(),
        );
        let mut stdin = child.stdin.take().ok_or(ProviderError::Io)?;
        let input = prompt.as_bytes().to_vec();
        let writer = thread::spawn(move || stdin.write_all(&input));
        let start = Instant::now();
        let status = loop {
            let error = if cancel.cancelled() {
                Some(ProviderError::Cancelled)
            } else if overflow.load(Ordering::Acquire) {
                Some(ProviderError::OutputLimit)
            } else if start.elapsed() >= timeout {
                Some(ProviderError::Timeout)
            } else {
                None
            };
            if let Some(error) = error {
                if self.worker_group {
                    // The worker reserves its budget before the request. Kill the entire
                    // supervised execution group; recovery retains that reservation.
                    unsafe {
                        libc::kill(-libc::getpgrp(), libc::SIGKILL);
                    }
                }
                terminate(&mut child);
                break Err(error);
            }
            match child.try_wait() {
                Ok(Some(status)) => {
                    terminate(&mut child);
                    break Ok(status);
                }
                Ok(None) => thread::sleep(Duration::from_millis(25)),
                Err(_) => {
                    terminate(&mut child);
                    break Err(ProviderError::Io);
                }
            }
        };
        while !writer.is_finished() || !stdout.is_finished() || !stderr.is_finished() {
            if cancel.cancelled() || start.elapsed() >= timeout {
                if self.worker_group {
                    unsafe {
                        libc::kill(-libc::getpgrp(), libc::SIGKILL);
                    }
                }
                terminate(&mut child);
                break;
            }
            thread::sleep(Duration::from_millis(10));
        }
        let written = writer.join();
        let out = stdout.join();
        let err = stderr.join();
        let status = status?;
        if overflow.load(Ordering::Acquire) {
            return Err(ProviderError::OutputLimit);
        }
        if !status.success() {
            return Err(ProviderError::Exit);
        }
        written
            .map_err(|_| ProviderError::Io)?
            .map_err(|_| ProviderError::Io)?;
        err.map_err(|_| ProviderError::Io)?
            .map_err(|_| ProviderError::Io)?;
        parse_events(
            &out.map_err(|_| ProviderError::Io)?
                .map_err(|_| ProviderError::Io)?,
        )
    }
}
fn parse_events(bytes: &[u8]) -> std::result::Result<ProviderReply, ProviderError> {
    let text = std::str::from_utf8(bytes).map_err(|_| ProviderError::Malformed)?;
    let mut final_text = None;
    let mut completed = false;
    for line in text.lines().filter(|l| !l.trim().is_empty()) {
        let event: serde_json::Value =
            serde_json::from_str(line).map_err(|_| ProviderError::Malformed)?;
        match event["type"].as_str() {
            Some("turn.completed") => completed = true,
            Some("turn.failed" | "error") => return Err(ProviderError::Exit),
            Some(lifecycle @ ("item.started" | "item.updated" | "item.completed")) => {
                let item = &event["item"];
                // Check every lifecycle event. This detects activity after it happened;
                // the CLI capability flags remain the preventive control.
                if !matches!(item["type"].as_str(), Some("agent_message" | "reasoning")) {
                    return Err(ProviderError::Malformed);
                }
                let text = item["text"].as_str().ok_or(ProviderError::Malformed)?;
                if lifecycle == "item.completed" && item["type"] == "agent_message" {
                    if final_text.is_some() {
                        return Err(ProviderError::Malformed);
                    }
                    final_text = Some(text.to_owned());
                }
            }
            Some("thread.started" | "turn.started") => (),
            _ => return Err(ProviderError::Malformed),
        }
    }
    if !completed {
        return Err(ProviderError::Malformed);
    }
    let reply: ProviderReply = serde_json::from_str(&final_text.ok_or(ProviderError::Malformed)?)
        .map_err(|_| ProviderError::Malformed)?;
    if reply.candidates.len() > 16 {
        return Err(ProviderError::OutputLimit);
    }
    Ok(reply)
}
#[cfg(test)]
mod tests;
