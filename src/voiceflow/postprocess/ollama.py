from __future__ import annotations

from typing import Any

import requests

from voiceflow.config import OllamaConfig
from voiceflow.postprocess.base import PostprocessResult


class OllamaPostProcessor:
    name = "ollama"

    def __init__(
        self,
        config: OllamaConfig,
        *,
        min_words: int = 1,
        session: Any | None = None,
        **_kwargs: object,
    ) -> None:
        self.config = config
        self.min_words = min_words
        self.session = session or requests.Session()

    def preflight(self) -> bool:
        try:
            response = self.session.get(
                f"{self.config.base_url.rstrip('/')}/api/tags",
                timeout=min(5, self.config.timeout_seconds),
            )
            response.raise_for_status()
        except requests.RequestException:
            return False
        return True

    def process(self, text: str) -> PostprocessResult:
        if not text or len(text.split()) < self.min_words:
            return PostprocessResult(text)
        try:
            response = self.session.post(
                f"{self.config.base_url.rstrip('/')}/api/generate",
                json={
                    "model": self.config.model,
                    "prompt": text,
                    "stream": False,
                    "options": {"temperature": self.config.temperature},
                },
                timeout=self.config.timeout_seconds,
            )
            response.raise_for_status()
            output = str(response.json().get("response", "")).strip()
            if not output:
                raise ValueError("Ollama returned empty text")
            return PostprocessResult(output)
        except (requests.RequestException, ValueError, TypeError) as exc:
            return PostprocessResult(text, used_fallback=True, error=str(exc))

    def retry(self) -> bool:
        return self.preflight()

    def close(self) -> None:
        self.session.close()

