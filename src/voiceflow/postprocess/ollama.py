from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from typing import Any

import requests

from voiceflow.config import OllamaConfig
from voiceflow.postprocess.base import PostprocessResult
from voiceflow.state import PostprocessState


class OllamaPostProcessor:
    name = "ollama"

    def __init__(
        self,
        config: OllamaConfig,
        *,
        min_words: int = 1,
        system_prompt: str,
        session: Any | None = None,
        state_callback: Callable[[PostprocessState, str | None], None] | None = None,
        **_kwargs: object,
    ) -> None:
        self.config = config
        self.min_words = min_words
        self.system_prompt = system_prompt
        self.session = session or requests.Session()
        self._state_callback = state_callback or (lambda _state, _message=None: None)
        self._lock = threading.RLock()
        self._execution_lock = threading.Lock()
        self._available = False
        self._closed = False
        self.last_error: str | None = None

    def preflight(self) -> bool:
        with self._lock:
            if self._closed:
                self.last_error = "Ollama post-processor is closed"
                self._state_callback(PostprocessState.ERROR, self.last_error)
                return False
        self._state_callback(PostprocessState.CHECKING, None)
        try:
            with self._execution_lock:
                response = self.session.get(
                    f"{self.config.base_url.rstrip('/')}/api/tags",
                    timeout=min(5, self.config.timeout_seconds),
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, Mapping):
                    raise ValueError("Ollama returned an invalid model inventory")
                models = payload.get("models")
                if not isinstance(models, list):
                    raise ValueError("Ollama returned an invalid model inventory")
                names = {
                    str(model.get("name", "")).removesuffix(":latest")
                    for model in models
                    if isinstance(model, dict)
                }
                configured = self.config.model.removesuffix(":latest")
                if configured not in names:
                    raise RuntimeError(f"Ollama model is not installed: {self.config.model}")
        except (requests.RequestException, RuntimeError, TypeError, ValueError) as exc:
            with self._lock:
                self._available = False
                self.last_error = str(exc)
            self._state_callback(PostprocessState.ERROR, str(exc))
            return False
        with self._lock:
            if self._closed:
                return False
            self._available = True
            self.last_error = None
        self._state_callback(PostprocessState.READY, None)
        return True

    def process(self, text: str) -> PostprocessResult:
        if not text or len(text.split()) < self.min_words:
            return PostprocessResult(text)
        with self._lock:
            if self._closed or not self._available:
                return PostprocessResult(
                    text,
                    used_fallback=True,
                    error=self.last_error or "Ollama is unavailable",
                )
        try:
            self._state_callback(PostprocessState.RUNNING, None)
            with self._execution_lock:
                with self._lock:
                    if self._closed or not self._available:
                        return PostprocessResult(
                            text,
                            used_fallback=True,
                            error=self.last_error or "Ollama is unavailable",
                        )
                response = self.session.post(
                    f"{self.config.base_url.rstrip('/')}/api/generate",
                    json={
                        "model": self.config.model,
                        "system": self.system_prompt,
                        "prompt": text,
                        "stream": False,
                        "options": {"temperature": self.config.temperature},
                    },
                    timeout=self.config.timeout_seconds,
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, Mapping):
                    raise ValueError("Ollama returned an invalid response")
                output = payload.get("response")
                if not isinstance(output, str) or not output.strip():
                    raise ValueError("Ollama returned empty text")
            with self._lock:
                if self._closed:
                    return PostprocessResult(
                        text,
                        used_fallback=True,
                        error="Ollama post-processor is closed",
                    )
                self._available = True
                self.last_error = None
            self._state_callback(PostprocessState.READY, None)
            return PostprocessResult(output.strip())
        except (requests.RequestException, ValueError, TypeError) as exc:
            with self._lock:
                self._available = False
                self.last_error = str(exc)
            self._state_callback(PostprocessState.DEGRADED, str(exc))
            return PostprocessResult(text, used_fallback=True, error=str(exc))

    def retry(self) -> bool:
        return self.preflight()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._available = False
        if self._execution_lock.acquire(blocking=False):
            try:
                self.session.close()
            finally:
                self._execution_lock.release()
        else:
            threading.Thread(
                target=self._close_when_idle,
                name="voiceflow-ollama-close",
                daemon=True,
            ).start()

    def _close_when_idle(self) -> None:
        with self._execution_lock:
            self.session.close()
