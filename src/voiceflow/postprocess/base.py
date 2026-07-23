from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from voiceflow.config import PostprocessConfig


@dataclass(frozen=True, slots=True)
class PostprocessResult:
    text: str
    used_fallback: bool = False
    error: str | None = None


class PostProcessor(Protocol):
    name: str

    def preflight(self) -> bool: ...

    def process(self, text: str) -> PostprocessResult: ...

    def retry(self) -> bool: ...

    def close(self) -> None: ...


def create_postprocessor(
    config: PostprocessConfig,
    **kwargs: object,
) -> PostProcessor:
    if config.provider == "codex":
        from voiceflow.postprocess.codex import CodexPostProcessor

        return CodexPostProcessor(
            config.codex,
            min_words=config.min_words,
            system_prompt=config.system_prompt,
            **kwargs,
        )
    if config.provider == "ollama":
        from voiceflow.postprocess.ollama import OllamaPostProcessor

        return OllamaPostProcessor(
            config.ollama,
            min_words=config.min_words,
            system_prompt=config.system_prompt,
            **kwargs,
        )
    from voiceflow.postprocess.none import NoPostProcessor

    return NoPostProcessor(**kwargs)
