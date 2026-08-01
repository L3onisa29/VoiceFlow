from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, TypedDict, Unpack

from voiceflow.config import PostprocessConfig
from voiceflow.postprocess.base import PostProcessor
from voiceflow.state import PostprocessState


class PostprocessorFactoryOptions(TypedDict, total=False):
    schema_path: Path | None
    environ: dict[str, str] | None
    state_callback: Callable[[PostprocessState, str | None], None] | None
    popen: Any
    run: Any
    clock: Callable[[], float]
    session: Any | None


def create_postprocessor(
    config: PostprocessConfig,
    **kwargs: Unpack[PostprocessorFactoryOptions],
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
