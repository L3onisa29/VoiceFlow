from __future__ import annotations

from voiceflow.postprocess.base import PostprocessResult


class NoPostProcessor:
    name = "none"

    def preflight(self) -> bool:
        return True

    def process(self, text: str) -> PostprocessResult:
        return PostprocessResult(text)

    def retry(self) -> bool:
        return True

    def close(self) -> None:
        pass

