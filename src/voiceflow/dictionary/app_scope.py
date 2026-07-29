from __future__ import annotations

from collections.abc import Callable
from typing import Any


class AppBundleResolver:
    def __init__(self, lookup: Callable[[int], Any] | None = None) -> None:
        self._lookup = lookup

    def resolve(self, pid: int) -> str | None:
        lookup = self._lookup
        if lookup is None:
            from AppKit import NSRunningApplication

            lookup = NSRunningApplication.runningApplicationWithProcessIdentifier_
        application = lookup(pid)
        if application is None:
            return None
        identifier = application.bundleIdentifier()
        return str(identifier) if identifier else None
