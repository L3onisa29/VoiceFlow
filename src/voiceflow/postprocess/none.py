from __future__ import annotations

from voiceflow.postprocess.base import PostprocessRequest, PostprocessResult, coerce_request
from voiceflow.state import PostprocessState


class NoPostProcessor:
    name = "none"

    def __init__(self, *, state_callback=None, **_kwargs: object) -> None:
        self._state_callback = state_callback or (lambda _state, _message=None: None)

    def preflight(self) -> bool:
        self._state_callback(PostprocessState.READY, None)
        return True

    def process(self, request: PostprocessRequest | str) -> PostprocessResult:
        return PostprocessResult(coerce_request(request).text)

    def retry(self) -> bool:
        return self.preflight()

    def close(self) -> None:
        pass
