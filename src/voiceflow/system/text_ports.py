from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class FocusTarget:
    pid: int
    element: Any


class FocusAccess(Protocol):
    def trusted(self) -> bool: ...

    def capture(self) -> FocusTarget: ...

    def same(self, left: FocusTarget, right: FocusTarget) -> bool: ...


class ClipboardPort(Protocol):
    def write(self, text: str) -> int | None: ...

    def read(self) -> str | None: ...

    def change_count(self) -> int: ...

    def clear(self) -> None: ...


class KeyboardEmitter(Protocol):
    def paste(self) -> None: ...

    def type_unicode(self, text: str) -> None: ...
