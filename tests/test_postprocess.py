from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from voiceflow.config import AppConfig, load_config
from voiceflow.postprocess.codex import CodexPostProcessor
from voiceflow.postprocess.none import NoPostProcessor
from voiceflow.postprocess.ollama import OllamaPostProcessor


class FakeProcess:
    def __init__(
        self,
        stdout: str = '{"text":"clean transcript","operations":[]}',
        returncode: int = 0,
    ) -> None:
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
    app_config.postprocess.codex.validate_model_on_startup = False
    schema = tmp_path / "schema.json"
    schema.write_text(
        json.dumps(
            {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "operations": {"type": "array"},
                },
                "required": ["text", "operations"],
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
    result = CodexPostProcessor(
        app_config.postprocess.codex,
        system_prompt=app_config.postprocess.system_prompt,
        **kwargs,
    )
    assert result.preflight()
    return result


def test_preflight_authentication_failure_falls_back(app_config: AppConfig, tmp_path: Path) -> None:
    app_config.postprocess.codex.executable = executable(tmp_path)
    codex = CodexPostProcessor(
        app_config.postprocess.codex,
        system_prompt=app_config.postprocess.system_prompt,
        schema_path=tmp_path / "schema.json",
        run=lambda *_args, **_kwargs: SimpleNamespace(returncode=1),
    )
    assert not codex.preflight()
    result = codex.process("raw text")
    assert result.text == "raw text" and result.used_fallback


def test_argv_stdin_schema_and_ephemeral_read_only(app_config: AppConfig, tmp_path: Path) -> None:
    created: list[tuple[list[str], dict[str, Any], FakeProcess]] = []

    def popen(argv: list[str], **kwargs: Any) -> FakeProcess:
        process = FakeProcess()
        created.append((argv, kwargs, process))
        return process

    codex = processor(app_config, tmp_path, process_factory=popen)
    result = codex.process("raw transcript")
    assert result.text == "clean transcript"
    argv, kwargs, child = created[0]
    assert argv[1:4] == ["exec", "--ephemeral", "--ignore-user-config"]
    assert argv[argv.index("--sandbox") :][:2] == ["--sandbox", "read-only"]
    assert argv[argv.index("--model") + 1] == "gpt-5.6-luna"
    assert 'model_reasoning_effort="low"' in argv
    assert argv[-1] == "-"
    assert child.input is not None and "<voicebud_data>" in child.input
    assert "raw transcript" in child.input
    assert "raw transcript" not in argv
    assert kwargs["start_new_session"] is True
    assert Path(kwargs["cwd"]).name.startswith("voiceflow-codex-")


def test_invalid_json_and_nonzero_exit_fall_back(app_config: AppConfig, tmp_path: Path) -> None:
    processes = iter([FakeProcess("not-json"), FakeProcess("{}", returncode=2)])
    codex = processor(
        app_config, tmp_path, process_factory=lambda *_args, **_kwargs: next(processes)
    )
    assert codex.process("first text").used_fallback
    assert codex.process("second text").used_fallback
    assert codex.process("circuit text").error == "Codex circuit is cooling down"


def test_timeout_terminates_group_and_falls_back(app_config: AppConfig, tmp_path: Path) -> None:
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


def test_preflight_can_validate_configured_model(app_config: AppConfig, tmp_path: Path) -> None:
    app_config.postprocess.codex.executable = executable(tmp_path)
    app_config.postprocess.codex.validate_model_on_startup = True
    schema = tmp_path / "schema.json"
    schema.write_text('{"type":"object"}', encoding="utf-8")
    processes: list[FakeProcess] = []

    def popen(*_args: Any, **_kwargs: Any) -> FakeProcess:
        child = FakeProcess()
        processes.append(child)
        return child

    codex = CodexPostProcessor(
        app_config.postprocess.codex,
        system_prompt=app_config.postprocess.system_prompt,
        schema_path=schema,
        popen=popen,
        run=lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
    )
    assert codex.preflight()
    assert len(processes) == 1
    assert processes[0].input is not None
    assert "preflight check" in processes[0].input


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
    def __init__(
        self,
        output: Any = '{"text":"cleaned words","operations":[]}',
        *,
        models: list[dict[str, Any]] | None = None,
    ) -> None:
        self.output = output
        self.models = [{"name": "qwen3:4b-instruct"}] if models is None else models
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.closed = False

    def get(self, *_args: Any, **_kwargs: Any) -> FakeResponse:
        return FakeResponse({"models": self.models})

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.posts.append((url, kwargs))
        return FakeResponse({"response": self.output})

    def close(self) -> None:
        self.closed = True


def test_ollama_success_and_short_bypass(app_config: AppConfig) -> None:
    session = FakeSession()
    provider = OllamaPostProcessor(
        app_config.postprocess.ollama,
        min_words=2,
        system_prompt=app_config.postprocess.system_prompt,
        session=session,
    )
    assert provider.preflight()
    assert provider.process("short").text == "short"
    assert not session.posts
    assert provider.process("two words").text == "cleaned words"
    assert session.posts[0][1]["timeout"] == app_config.postprocess.ollama.timeout_seconds
    assert session.posts[0][1]["json"]["system"] == app_config.postprocess.system_prompt


def test_ollama_empty_output_falls_back(app_config: AppConfig) -> None:
    session = FakeSession(output="")
    provider = OllamaPostProcessor(
        app_config.postprocess.ollama,
        system_prompt=app_config.postprocess.system_prompt,
        session=session,
    )
    assert provider.preflight()
    result = provider.process("original")
    assert result.text == "original" and result.used_fallback


def test_ollama_requires_configured_model_and_then_falls_back_immediately(
    app_config: AppConfig,
) -> None:
    session = FakeSession(models=[])
    provider = OllamaPostProcessor(
        app_config.postprocess.ollama,
        system_prompt=app_config.postprocess.system_prompt,
        session=session,
    )
    assert not provider.preflight()
    result = provider.process("original text")
    assert result.text == "original text" and result.used_fallback
    assert session.posts == []


@pytest.mark.parametrize("payload", [{"response": None}, [], {"response": 42}])
def test_ollama_rejects_malformed_payload(app_config: AppConfig, payload: Any) -> None:
    class MalformedSession(FakeSession):
        def post(self, url: str, **kwargs: Any) -> FakeResponse:
            self.posts.append((url, kwargs))
            return FakeResponse(payload)

    provider = OllamaPostProcessor(
        app_config.postprocess.ollama,
        system_prompt=app_config.postprocess.system_prompt,
        session=MalformedSession(),
    )
    assert provider.preflight()
    result = provider.process("original text")
    assert result.text == "original text" and result.used_fallback
    assert result.text != "None"


def test_ollama_close_prevents_new_requests(app_config: AppConfig) -> None:
    session = FakeSession()
    provider = OllamaPostProcessor(
        app_config.postprocess.ollama,
        system_prompt=app_config.postprocess.system_prompt,
        session=session,
    )
    assert provider.preflight()
    provider.close()
    assert session.closed
    assert provider.process("original text").used_fallback
    assert not provider.retry()


def test_ollama_close_waits_for_active_request_without_closing_under_it(
    app_config: AppConfig,
) -> None:
    request_started = threading.Event()
    release_request = threading.Event()
    session_closed = threading.Event()

    class BlockingSession(FakeSession):
        def post(self, url: str, **kwargs: Any) -> FakeResponse:
            self.posts.append((url, kwargs))
            request_started.set()
            assert release_request.wait(timeout=1)
            return FakeResponse({"response": self.output})

        def close(self) -> None:
            super().close()
            session_closed.set()

    session = BlockingSession()
    provider = OllamaPostProcessor(
        app_config.postprocess.ollama,
        system_prompt=app_config.postprocess.system_prompt,
        session=session,
    )
    assert provider.preflight()
    results: list[Any] = []
    worker = threading.Thread(target=lambda: results.append(provider.process("original text")))
    worker.start()
    assert request_started.wait(timeout=1)

    provider.close()
    assert not session.closed
    release_request.set()
    worker.join(timeout=1)

    assert not worker.is_alive()
    assert session_closed.wait(timeout=1)
    assert results[0].used_fallback


def test_codex_nonzero_exit_does_not_expose_stderr(
    app_config: AppConfig,
    tmp_path: Path,
) -> None:
    codex = processor(
        app_config,
        tmp_path,
        process_factory=lambda *_args, **_kwargs: FakeProcess(
            "ignored",
            returncode=2,
        ),
    )
    result = codex.process("sensitive transcript")
    assert result.used_fallback
    assert result.error == "Codex exited with status 2"


@pytest.mark.skipif(
    os.environ.get("VOICEFLOW_RUN_CODEX_INTEGRATION") != "1",
    reason="real Codex integration is opt-in",
)
def test_real_codex_integration() -> None:
    config = load_config(root=Path(__file__).resolve().parents[1], environ={})
    provider = CodexPostProcessor(
        config.postprocess.codex,
        system_prompt=config.postprocess.system_prompt,
    )
    try:
        assert provider.preflight()
        result = provider.process("Ciao cioè cioè questo è un test")
        assert result.text and not result.used_fallback
    finally:
        provider.close()


@pytest.mark.skipif(
    os.environ.get("VOICEFLOW_RUN_OLLAMA_INTEGRATION") != "1",
    reason="real Ollama integration is opt-in",
)
def test_real_ollama_integration() -> None:
    config = load_config(root=Path(__file__).resolve().parents[1], environ={})
    provider = OllamaPostProcessor(
        config.postprocess.ollama,
        system_prompt=config.postprocess.system_prompt,
    )
    try:
        assert provider.preflight()
        result = provider.process("Ciao cioè cioè questo è un test")
        assert result.text and not result.used_fallback
    finally:
        provider.close()
