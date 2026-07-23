from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class PermissionStatus:
    input_monitoring: bool
    accessibility: bool
    microphone: bool

    @property
    def ready(self) -> bool:
        return self.input_monitoring and self.accessibility and self.microphone

    @property
    def missing(self) -> tuple[str, ...]:
        missing: list[str] = []
        if not self.input_monitoring:
            missing.append("Input Monitoring")
        if not self.accessibility:
            missing.append("Accessibility")
        if not self.microphone:
            missing.append("Microphone")
        return tuple(missing)

    @property
    def message(self) -> str:
        return "Permessi mancanti: " + ", ".join(self.missing)


def check_permissions(
    *,
    quartz: Any | None = None,
    accessibility_check: Callable[[], bool] | None = None,
    microphone_check: Callable[[], bool] | None = None,
    request: bool = False,
    microphone_callback: Callable[[bool], None] | None = None,
) -> PermissionStatus:
    if quartz is None:
        import Quartz as quartz
    input_monitoring = bool(quartz.CGPreflightListenEventAccess())
    if not input_monitoring and request:
        request_input = getattr(quartz, "CGRequestListenEventAccess", None)
        if request_input is not None:
            input_monitoring = bool(request_input())
    if accessibility_check is None:

        def default_accessibility_check() -> bool:
            return accessibility_is_trusted(request=request)

        accessibility_check = default_accessibility_check
    if microphone_check is None:

        def default_microphone_check() -> bool:
            return microphone_is_authorized(
                request=request,
                callback=microphone_callback,
            )

        microphone_check = default_microphone_check
    return PermissionStatus(
        input_monitoring=input_monitoring,
        accessibility=bool(accessibility_check()),
        microphone=bool(microphone_check()),
    )


def accessibility_is_trusted(*, request: bool = False) -> bool:
    from ApplicationServices import (
        AXIsProcessTrusted,
        AXIsProcessTrustedWithOptions,
        kAXTrustedCheckOptionPrompt,
    )

    if request:
        return bool(
            AXIsProcessTrustedWithOptions(
                {kAXTrustedCheckOptionPrompt: True},
            )
        )
    return bool(AXIsProcessTrusted())


def microphone_is_authorized(
    *,
    request: bool = False,
    callback: Callable[[bool], None] | None = None,
    avfoundation: Any | None = None,
) -> bool:
    if avfoundation is None:
        import AVFoundation as avfoundation

    status = avfoundation.AVCaptureDevice.authorizationStatusForMediaType_(
        avfoundation.AVMediaTypeAudio
    )
    if status == avfoundation.AVAuthorizationStatusAuthorized:
        return True
    if status == avfoundation.AVAuthorizationStatusNotDetermined and request:
        avfoundation.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
            avfoundation.AVMediaTypeAudio,
            callback or (lambda _granted: None),
        )
    return False
