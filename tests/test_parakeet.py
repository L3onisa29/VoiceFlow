from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from voiceflow.config import AppConfig
from voiceflow.state import ModelState
from voiceflow.stt.model_locator import MODEL_FILES, ModelLocator, ModelResolution
from voiceflow.stt.parakeet import AudioTooLongError, ParakeetTranscriber


class FakeModel:
    def __init__(self, result: Any = " hello ") -> None:
        self.result = result
        self.recognize_calls: list[tuple[Any, dict[str, Any]]] = []

    def recognize(self, audio: Any, **kwargs: Any) -> Any:
        self.recognize_calls.append((audio, kwargs))
        return self.result

    def with_vad(self, _vad: Any) -> FakeModel:
        return FakeModel([SimpleNamespace(text="one"), SimpleNamespace(text="two")])


class FakeOnnx:
    def __init__(self, *, vad_error: Exception | None = None) -> None:
        self.model = FakeModel()
        self.vad_error = vad_error
        self.model_calls: list[tuple[Any, ...]] = []
        self.vad_calls: list[tuple[Any, ...]] = []

    def load_model(self, *args: Any, **kwargs: Any) -> FakeModel:
        self.model_calls.append((*args, kwargs))
        return self.model

    def load_vad(self, *args: Any, **kwargs: Any) -> object:
        self.vad_calls.append((*args, kwargs))
        if self.vad_error:
            raise self.vad_error
        return object()


def make_transcriber(
    app_config: AppConfig, valid_model: Path, onnx: FakeOnnx
) -> ParakeetTranscriber:
    app_config.stt.model_path = valid_model
    locator = ModelLocator(app_config.stt, environ={})
    return ParakeetTranscriber(app_config.stt, locator=locator, onnx_module=onnx)


def test_exact_model_and_cpu_provider_arguments(app_config: AppConfig, valid_model: Path) -> None:
    onnx = FakeOnnx()
    transcriber = make_transcriber(app_config, valid_model, onnx)
    transcriber.load()
    assert onnx.model_calls == [
        (
            "nemo-parakeet-tdt-0.6b-v3",
            valid_model,
            {"quantization": "int8", "providers": ["CPUExecutionProvider"]},
        )
    ]
    assert onnx.vad_calls == [("silero", {"providers": ["CPUExecutionProvider"]})]
    assert transcriber.transcribe(np.ones(160, dtype=np.float32)).text == "one two"


def test_empty_audio_skips_recognition(app_config: AppConfig, valid_model: Path) -> None:
    onnx = FakeOnnx()
    transcriber = make_transcriber(app_config, valid_model, onnx)
    transcriber.load()
    assert transcriber.transcribe(np.array([], dtype=np.float32)).text == ""
    assert onnx.model.recognize_calls == []


def test_vad_failure_allows_short_and_rejects_long(
    app_config: AppConfig, valid_model: Path
) -> None:
    app_config.stt.max_direct_audio_seconds = 0.01
    onnx = FakeOnnx(vad_error=RuntimeError("offline"))
    transcriber = make_transcriber(app_config, valid_model, onnx)
    transcriber.load()
    assert transcriber.transcribe(np.ones(80, dtype=np.float32)).text == "hello"
    with pytest.raises(AudioTooLongError, match="without Silero VAD|Silero VAD is unavailable"):
        transcriber.transcribe(np.ones(320, dtype=np.float32))


def test_loading_emits_visible_states(app_config: AppConfig, valid_model: Path) -> None:
    states: list[ModelState] = []
    app_config.stt.model_path = valid_model
    transcriber = ParakeetTranscriber(
        app_config.stt,
        locator=ModelLocator(app_config.stt, environ={}),
        onnx_module=FakeOnnx(),
        state_callback=lambda state, _detail: states.append(state),
    )
    transcriber.load()
    assert states == [ModelState.RESOLVING, ModelState.LOADING, ModelState.READY]


def test_corrupt_cache_is_backed_up_then_downloaded(app_config: AppConfig, tmp_path: Path) -> None:
    cache = tmp_path / "parakeet-tdt-0.6b-v3-int8"
    cache.mkdir()
    (cache / "broken").write_text("bad", encoding="utf-8")

    class Locator:
        def resolve(self) -> ModelResolution:
            return ModelResolution(cache, "repair-download", needs_download=True)

    class DownloadingOnnx(FakeOnnx):
        def load_model(self, *args: Any, **kwargs: Any) -> FakeModel:
            path = Path(args[1])
            path.mkdir()
            for filename in MODEL_FILES:
                content = (
                    '{"model_type":"nemo-conformer-tdt"}' if filename == "config.json" else "data"
                )
                (path / filename).write_text(content, encoding="utf-8")
            return super().load_model(*args, **kwargs)

    states: list[ModelState] = []
    transcriber = ParakeetTranscriber(
        app_config.stt,
        locator=Locator(),  # type: ignore[arg-type]
        onnx_module=DownloadingOnnx(),
        state_callback=lambda state, _detail: states.append(state),
    )
    transcriber.load()
    assert transcriber.corrupt_cache_backup is not None
    assert (transcriber.corrupt_cache_backup / "broken").is_file()
    assert states == [
        ModelState.RESOLVING,
        ModelState.DOWNLOADING,
        ModelState.LOADING,
        ModelState.READY,
    ]


def test_nonempty_but_unloadable_voiceflow_cache_is_repaired(
    app_config: AppConfig,
    tmp_path: Path,
) -> None:
    app_config.stt.model_path = None
    app_config.stt.reuse_handy_model = False
    cache_root = tmp_path / "cache"
    cache = cache_root / "parakeet-tdt-0.6b-v3-int8"
    cache.mkdir(parents=True)
    for filename in MODEL_FILES:
        content = (
            '{"model_type":"nemo-conformer-tdt"}'
            if filename == "config.json"
            else "not-empty-but-invalid"
        )
        (cache / filename).write_text(content, encoding="utf-8")

    class RepairingOnnx(FakeOnnx):
        calls = 0

        def load_model(self, *args: Any, **kwargs: Any) -> FakeModel:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("invalid ONNX")
            path = Path(args[1])
            path.mkdir()
            for filename in MODEL_FILES:
                content = (
                    '{"model_type":"nemo-conformer-tdt"}'
                    if filename == "config.json"
                    else "downloaded"
                )
                (path / filename).write_text(content, encoding="utf-8")
            return super().load_model(*args, **kwargs)

    transcriber = ParakeetTranscriber(
        app_config.stt,
        locator=ModelLocator(
            app_config.stt,
            environ={},
            handy_path=tmp_path / "handy",
            cache_root=cache_root,
        ),
        onnx_module=RepairingOnnx(),
    )
    result = transcriber.load()

    assert result.source == "repair-download"
    assert transcriber.corrupt_cache_backup is not None
    assert transcriber.corrupt_cache_backup.is_dir()
    assert "nemo128.onnx" not in MODEL_FILES
