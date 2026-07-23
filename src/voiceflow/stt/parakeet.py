from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import numpy as np

from voiceflow.config import SttConfig
from voiceflow.state import ModelState
from voiceflow.stt.model_locator import ModelLocator, ModelResolution, validate_model

LOGGER = logging.getLogger(__name__)


class AudioTooLongError(RuntimeError):
    pass


class ParakeetTranscriber:
    def __init__(
        self,
        config: SttConfig,
        *,
        locator: ModelLocator | None = None,
        state_callback: Callable[[ModelState, str | None], None] | None = None,
        onnx_module: Any | None = None,
    ) -> None:
        self.config = config
        self.locator = locator or ModelLocator(config)
        self._state_callback = state_callback or (lambda _state, _message=None: None)
        self._onnx_module = onnx_module
        self._direct_model: Any | None = None
        self._vad_model: Any | None = None
        self.model_path: Path | None = None
        self.corrupt_cache_backup: Path | None = None
        self.vad_error: Exception | None = None

    def _onnx(self) -> Any:
        if self._onnx_module is None:
            import onnx_asr

            self._onnx_module = onnx_asr
        return self._onnx_module

    def load(self) -> ModelResolution:
        self._state_callback(ModelState.RESOLVING, None)
        try:
            resolution = self.locator.resolve()
            model, resolution = self._load_with_recovery(resolution)
        except Exception as exc:
            self._state_callback(ModelState.ERROR, str(exc))
            raise

        self._direct_model = model
        self.model_path = resolution.path
        if self.config.vad.enabled and self.config.vad.auto_download:
            try:
                vad = self._onnx().load_vad(
                    self.config.vad.model,
                    providers=[self.config.provider],
                )
                self._vad_model = model.with_vad(vad)
            except Exception as exc:
                self.vad_error = exc
                LOGGER.warning(
                    "Silero VAD unavailable; recordings up to %.1f seconds remain enabled: %s",
                    self.config.max_direct_audio_seconds,
                    exc,
                )
        elif self.config.vad.enabled:
            self.vad_error = RuntimeError("Silero VAD download is disabled")
        self._state_callback(ModelState.READY, str(resolution.path))
        return resolution

    def _load_with_recovery(self, resolution: ModelResolution) -> tuple[Any, ModelResolution]:
        try:
            return self._load_resolution(resolution), resolution
        except Exception:
            if resolution.source == "handy":
                LOGGER.warning(
                    "The Handy model could not be loaded; falling back to the VoiceFlow cache",
                    exc_info=True,
                )
                return self._load_with_recovery(self.locator.resolve(skip_handy=True))
            if resolution.source == "voiceflow-cache" and self.config.auto_download:
                LOGGER.warning(
                    "The VoiceFlow model cache could not be loaded; repairing it",
                    exc_info=True,
                )
                repair = ModelResolution(
                    resolution.path,
                    "repair-download",
                    needs_download=True,
                )
                return self._load_resolution(repair), repair
            raise

    def _load_resolution(self, resolution: ModelResolution) -> Any:
        if resolution.needs_download:
            self._state_callback(ModelState.DOWNLOADING, None)
            if resolution.source == "repair-download" and resolution.path.exists():
                backup = resolution.path.with_name(
                    f"{resolution.path.name}.corrupt-{time.time_ns()}"
                )
                resolution.path.rename(backup)
                self.corrupt_cache_backup = backup
                LOGGER.warning("Moved corrupt model cache to %s", backup)
            resolution.path.parent.mkdir(parents=True, exist_ok=True)
        else:
            self._state_callback(ModelState.LOADING, None)
        model = self._onnx().load_model(
            self.config.model,
            resolution.path,
            quantization=self.config.quantization,
            providers=[self.config.provider],
        )
        if resolution.needs_download:
            validate_model(resolution.path)
            self._state_callback(ModelState.LOADING, None)
        return model

    def transcribe(self, audio: np.ndarray, *, sample_rate: int = 16000) -> str:
        if self._direct_model is None:
            raise RuntimeError("Parakeet model is not loaded")
        waveform = np.asarray(audio, dtype=np.float32).reshape(-1)
        if waveform.size == 0:
            return ""
        duration = waveform.size / sample_rate
        model = self._vad_model
        if model is None:
            if duration > self.config.max_direct_audio_seconds:
                raise AudioTooLongError(
                    f"Recording is {duration:.1f}s, but Silero VAD is unavailable; "
                    f"maximum direct audio is {self.config.max_direct_audio_seconds:.1f}s"
                )
            model = self._direct_model

        kwargs: dict[str, Any] = {"sample_rate": sample_rate}
        if self.config.language is not None:
            kwargs["language"] = self.config.language
        result = model.recognize(waveform, **kwargs)
        return _result_text(result)


def _result_text(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return result.strip()
    if hasattr(result, "text"):
        return str(result.text).strip()
    if isinstance(result, Iterable):
        return " ".join(part for item in result if (part := _result_text(item))).strip()
    return str(result).strip()
