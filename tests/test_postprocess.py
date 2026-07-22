from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from voiceflow.config import AppConfig
from voiceflow.postprocess.codex import CodexPostProcessor
from voiceflow.postprocess.none import NoPostProcessor
from voiceflow.postprocess.ollama import OllamaPostProcessor


class FakeProcess:
    def __init__(self, stdout: str = '{"text":"clean"}', returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.pid = 999999
        self.input: str | None = None
        self.timeout: float | None = None

    def communicate(self, *, input: str, timeout: float) -> tuple[str, str]:
        self.input = input
        self.timeout = timeout
        return self.stdout, "private stderr"

    def poll(self) -> int | None:
        return self.returncode


def executable(tmp_path: Path) -> Path:
    path = tmp_path / "codex"
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def processor(
    app_config: AppConfig,
    tmp_path: Path,
    *,
    process_factory: Any | None = None,
    run: Any | None = None,
    clock: Any | None = None,
) -> CodexPostProcessor:
    app_config.postprocess.codex.executable = executable(tmp_path)
    schema = tmp_path / "schema.json"
    schema.write_text(
        json.dumps(
            {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            }
        ),
        encoding="utf-8",
    )
    kwargs: dict[str, Any] = {
        "schema_path": schema,
        "run": run or (lambda *_args, **_kwargs: SimpleNamespace(returncode=0)),
    }
    if process_factory is not None:
        kwargs["popen"] = process_factory
    if clock is not None:
        kwargs["clock"] = clock
    result = CodexPostProcessor(app_config.postprocess.codex, **kwargs)
    assert result.preflight()
    return result


def test_preflight_authentication_failure_falls_back(
    app_config: AppConfig, tmp_path: Path
) -> None:
    app_config.postprocess.codex.executable = executable(tmp_path)
    codex = CodexPostProcessor(
        app_config.postprocess.codex,
        schema_path=tmp_path / "schema.json",
        run=lambda *_args, **_kwargs: SimpleNamespace(returncode=1),
    )
    assert not codex.preflight()
    result = codex.process("raw text")
    assert result.text == "raw text" and result.used_fallback


def test_argv_stdin_schema_and_ephemeral_read_only(
    app_config: AppConfig, tmp_path: Path
) -> None:
    created: list[tuple[list[str], dict[str, Any], FakeProcess]] = []

    def popen(argv: list[str], **kwargs: Any) -> FakeProcess:
        process = FakeProcess()
        created.append((argv, kwargs, process))
        return process

    codex = processor(app_config, tmp_path, process_factory=popen)
    result = codex.process("raw transcript")
    assert result.text == "clean"
    argv, kwargs, child = created[0]
    assert argv[1:4] == ["exec", "--ephemeral", "--ignore-user-config"]
    assert argv[argv.index("--sandbox") :][:2] == ["--sandbox", "read-only"]
    assert argv[argv.index("--model") + 1] == "gpt-5.6-luna"
    assert 'model_reasoning_effort="low"' in argv
    assert argv[-1] == "-"
    assert child.input is not None and child.input.endswith("Transcript:\nraw transcript")
    assert "raw transcript" not in argv
    assert kwargs["start_new_session"] is True
    assert Path(kwargs["cwd"]).name.startswith("voiceflow-codex-")


def test_invalid_json_and_nonzero_exit_fall_back(
    app_config: AppConfig, tmp_path: Path
) -> None:
    processes = iter([FakeProcess("not-json"), FakeProcess("{}", returncode=2)])
    codex = processor(
        app_config, tmp_path, process_factory=lambda *_args, **_kwargs: next(processes)
    )
    assert codex.process("first text").used_fallback
    assert codex.process("second text").used_fallback
    assert codex.process("circuit text").error == "Codex circuit is cooling down"


def test_timeout_terminates_group_and_falls_back(
    app_config: AppConfig, tmp_path: Path
) -> None:
    class TimeoutProcess(FakeProcess):
        def communicate(self, *, input: str, timeout: float) -> tuple[str, str]:
            raise subprocess.TimeoutExpired("codex", timeout)

    codex = processor(
        app_config, tmp_path, process_factory=lambda *_args, **_kwargs: TimeoutProcess()
    )
    terminated: list[int] = []
    codex._terminate_process_group = lambda child: terminated.append(child.pid)  # type: ignore[method-assign]
    result = codex.process("keep original")
    assert result.text == "keep original" and result.used_fallback
    assert terminated == [999999]


def test_none_provider_is_identity() -> None:
    provider = NoPostProcessor()
    assert provider.preflight()
    assert provider.process("same").text == "same"


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, Any]:
        return self.payload


class FakeSession:
    def __init__(self, output: str = "cleaned") -> None:
        self.output = output
        self.posts: list[tuple[str, dict[str, Any]]] = []

    def get(self, *_args: Any, **_kwargs: Any) -> FakeResponse:
        return FakeResponse({"models": []})

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.posts.append((url, kwargs))
        return FakeResponse({"response": self.output})

    def close(self) -> None:
        pass


def test_ollama_success_and_short_bypass(app_config: AppConfig) -> None:
    session = FakeSession()
    provider = OllamaPostProcessor(
        app_config.postprocess.ollama,
        min_words=2,
        session=session,
    )
    assert provider.preflight()
    assert provider.process("short").text == "short"
    assert not session.posts
    assert provider.process("two words").text == "cleaned"
    assert session.posts[0][1]["timeout"] == app_config.postprocess.ollama.timeout_seconds


def test_ollama_empty_output_falls_back(app_config: AppConfig) -> None:
    provider = OllamaPostProcessor(
        app_config.postprocess.ollama,
        session=FakeSession(output=""),
    )
    result = provider.process("original")
    assert result.text == "original" and result.used_fallback
