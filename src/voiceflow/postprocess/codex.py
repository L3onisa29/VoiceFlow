from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from voiceflow.config import CodexConfig
from voiceflow.postprocess.base import PostprocessResult
from voiceflow.state import PostprocessState


class CodexPostProcessor:
    name = "codex"

    def __init__(
        self,
        config: CodexConfig,
        *,
        min_words: int = 1,
        schema_path: Path | None = None,
        environ: dict[str, str] | None = None,
        state_callback: Callable[[PostprocessState, str | None], None] | None = None,
        popen: Any = subprocess.Popen,
        run: Any = subprocess.run,
        clock: Callable[[], float] = time.monotonic,
        **_kwargs: object,
    ) -> None:
        self.config = config
        self.min_words = min_words
        self.schema_path = (schema_path or _default_schema_path()).resolve()
        self.environ = os.environ if environ is None else environ
        self._state_callback = state_callback or (lambda _state, _message=None: None)
        self._popen = popen
        self._run = run
        self._clock = clock
        self._lock = threading.RLock()
        self._process: Any | None = None
        self._failures = 0
        self._circuit_until = 0.0
        self._hard_error = False
        self.executable: Path | None = None
        self.last_error: str | None = None

    def _resolve_executable(self) -> Path:
        explicit = self.config.executable
        if explicit is not None:
            candidate = explicit.expanduser()
            if not candidate.is_file() or not os.access(candidate, os.X_OK):
                raise FileNotFoundError(f"Configured Codex executable is invalid: {candidate}")
            return candidate.resolve()
        env_bin = self.environ.get("VOICEFLOW_CODEX_BIN")
        if env_bin:
            candidate = Path(env_bin).expanduser()
            if not candidate.is_file() or not os.access(candidate, os.X_OK):
                raise FileNotFoundError(f"VOICEFLOW_CODEX_BIN is invalid: {candidate}")
            return candidate.resolve()
        found = shutil.which("codex", path=self.environ.get("PATH"))
        if found:
            return Path(found).resolve()
        homebrew = Path("/opt/homebrew/bin/codex")
        if homebrew.is_file() and os.access(homebrew, os.X_OK):
            return homebrew.resolve()
        raise FileNotFoundError("Codex CLI was not found")

    def preflight(self) -> bool:
        self._state_callback(PostprocessState.CHECKING, None)
        try:
            executable = self._resolve_executable()
            result = self._run(
                [str(executable), "login", "status"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError("Codex CLI is not authenticated")
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            with self._lock:
                self.executable = None
                self._hard_error = True
                self.last_error = str(exc)
            self._state_callback(PostprocessState.ERROR, str(exc))
            return False
        with self._lock:
            self.executable = executable
            self._hard_error = False
            self._failures = 0
            self._circuit_until = 0
            self.last_error = None
        self._state_callback(PostprocessState.READY, None)
        return True

    def retry(self) -> bool:
        return self.preflight()

    def argv(self) -> list[str]:
        if self.executable is None:
            raise RuntimeError("Codex preflight has not succeeded")
        return [
            str(self.executable),
            "exec",
            "--ephemeral",
            "--ignore-user-config",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--model",
            self.config.model,
            "-c",
            f'model_reasoning_effort="{self.config.reasoning_effort}"',
            "--output-schema",
            str(self.schema_path),
            "-",
        ]

    def process(self, text: str) -> PostprocessResult:
        if not text or len(text.split()) < self.min_words:
            return PostprocessResult(text)
        with self._lock:
            if self._hard_error or self.executable is None:
                return PostprocessResult(text, used_fallback=True, error=self.last_error)
            if self._clock() < self._circuit_until:
                return PostprocessResult(
                    text, used_fallback=True, error="Codex circuit is cooling down"
                )

        self._state_callback(PostprocessState.RUNNING, None)
        prompt = f"{self.config.system_prompt}\n\nTranscript:\n{text}"
        try:
            with tempfile.TemporaryDirectory(prefix="voiceflow-codex-") as cwd:
                process = self._popen(
                    self.argv(),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    cwd=cwd,
                    start_new_session=True,
                )
                with self._lock:
                    self._process = process
                try:
                    stdout, _stderr = process.communicate(
                        input=prompt,
                        timeout=self.config.timeout_seconds,
                    )
                except subprocess.TimeoutExpired:
                    self._terminate_process_group(process)
                    raise TimeoutError(
                        f"Codex timed out after {self.config.timeout_seconds:g} seconds"
                    ) from None
                finally:
                    with self._lock:
                        self._process = None
            if process.returncode != 0:
                raise RuntimeError(f"Codex exited with status {process.returncode}")
            payload = json.loads(stdout)
            output = payload.get("text") if isinstance(payload, dict) else None
            if not isinstance(output, str) or not output.strip():
                raise ValueError("Codex returned invalid or empty JSON text")
        except (OSError, TimeoutError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
            self._record_failure(str(exc))
            return PostprocessResult(text, used_fallback=True, error=str(exc))

        with self._lock:
            self._failures = 0
            self._circuit_until = 0
            self.last_error = None
        self._state_callback(PostprocessState.READY, None)
        return PostprocessResult(output.strip())

    def _record_failure(self, message: str) -> None:
        with self._lock:
            self._failures += 1
            self.last_error = message
            if self._failures >= self.config.failure_threshold:
                self._circuit_until = self._clock() + self.config.cooldown_seconds
        self._state_callback(PostprocessState.DEGRADED, message)

    @staticmethod
    def _terminate_process_group(process: Any) -> None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            with suppress(OSError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()

    def close(self) -> None:
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            self._terminate_process_group(process)


def _default_schema_path() -> Path:
    source_schema = Path(__file__).resolve().parents[3] / "schemas/codex-postprocess.schema.json"
    if source_schema.is_file():
        return source_schema
    return Path(__file__).resolve().parents[1] / "data/codex-postprocess.schema.json"
