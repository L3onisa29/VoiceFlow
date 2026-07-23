from __future__ import annotations

from typing import Any

READY_WIDTH = 176
ERROR_WIDTH = 320
PANEL_HEIGHT = 44
SCREEN_MARGIN = 16
CORNER_RADIUS = 14
FADE_SECONDS = 0.18


class ModelNotification:
    """A transient, non-activating notification for model availability."""

    def __init__(self) -> None:
        from AppKit import (
            NSBackingStoreBuffered,
            NSColor,
            NSFont,
            NSLineBreakByTruncatingTail,
            NSPanel,
            NSTextField,
            NSView,
            NSWindowStyleMaskBorderless,
            NSWindowStyleMaskNonactivatingPanel,
        )
        from Quartz import CALayer, CAShapeLayer

        self._timer: Any | None = None
        self._visible = False
        self._closed = False
        self._width = READY_WIDTH

        self.panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (self._width, PANEL_HEIGHT)),
            NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
            NSBackingStoreBuffered,
            False,
        )
        self.panel.setLevel_(25)
        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(NSColor.clearColor())
        self.panel.setHasShadow_(True)
        self.panel.setIgnoresMouseEvents_(True)
        self.panel.setHidesOnDeactivate_(False)
        self.panel.setCollectionBehavior_(1)

        self.view = NSView.alloc().initWithFrame_(((0, 0), (self._width, PANEL_HEIGHT)))
        self.view.setWantsLayer_(True)
        self.root_layer = CALayer.layer()
        self.root_layer.setFrame_(((0, 0), (self._width, PANEL_HEIGHT)))
        self.view.setLayer_(self.root_layer)
        self.panel.setContentView_(self.view)

        self.background_layer = CALayer.layer()
        self.background_layer.setFrame_(((0, 0), (self._width, PANEL_HEIGHT)))
        self.background_layer.setCornerRadius_(CORNER_RADIUS)
        self.background_layer.setBackgroundColor_(
            NSColor.colorWithCalibratedWhite_alpha_(0.02, 0.92).CGColor()
        )
        self.background_layer.setBorderWidth_(0.5)
        self.background_layer.setBorderColor_(
            NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.22).CGColor()
        )
        self.root_layer.addSublayer_(self.background_layer)

        self.icon_layer = CAShapeLayer.layer()
        self.icon_layer.setFrame_(((0, 0), (44, PANEL_HEIGHT)))
        self.icon_layer.setFillColor_(None)
        self.icon_layer.setLineWidth_(2.3)
        self.icon_layer.setLineCap_("round")
        self.icon_layer.setLineJoin_("round")
        self.root_layer.addSublayer_(self.icon_layer)

        self.label = NSTextField.labelWithString_("")
        self.label.setFrame_(((43, 11), (self._width - 55, 22)))
        self.label.setFont_(NSFont.boldSystemFontOfSize_(13))
        self.label.setTextColor_(NSColor.whiteColor())
        self.label.setLineBreakMode_(NSLineBreakByTruncatingTail)
        self.label.setMaximumNumberOfLines_(1)
        self.view.addSubview_(self.label)

    def show_ready(self, *, duration: float) -> None:
        self._show(
            text="VoiceBud pronto",
            error=False,
            duration=duration,
        )

    def show_error(self, detail: str | None, *, duration: float) -> None:
        message = (
            detail.strip() if detail and detail.strip() else "Caricamento modello non riuscito"
        )
        self._show(
            text=f"Modello non disponibile — {message}",
            error=True,
            duration=duration,
        )

    def _show(self, *, text: str, error: bool, duration: float) -> None:
        from AppKit import NSColor
        from Quartz import CABasicAnimation, CATransaction

        if self._closed:
            return
        self._cancel_timer()
        self._width = ERROR_WIDTH if error else READY_WIDTH
        self._layout()
        self.label.setStringValue_(text)
        color = (
            NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 0.25, 0.29, 1.0)
            if error
            else NSColor.colorWithCalibratedRed_green_blue_alpha_(0.24, 0.82, 0.49, 1.0)
        )
        CATransaction.begin()
        CATransaction.setDisableActions_(True)
        self.icon_layer.setStrokeColor_(color.CGColor())
        self.icon_layer.setPath_(self._icon_path(error=error))
        self.root_layer.setOpacity_(1.0)
        CATransaction.commit()
        fade = CABasicAnimation.animationWithKeyPath_("opacity")
        fade.setFromValue_(0.0)
        fade.setToValue_(1.0)
        fade.setDuration_(FADE_SECONDS)
        self.root_layer.addAnimation_forKey_(fade, "notification-fade")

        self._place_on_pointer_screen()
        if not self._visible:
            self.panel.orderFrontRegardless()
            self._visible = True
        self._schedule_hide(duration)

    def _layout(self) -> None:
        from Quartz import CATransaction

        frame = ((0, 0), (self._width, PANEL_HEIGHT))
        CATransaction.begin()
        CATransaction.setDisableActions_(True)
        self.root_layer.setFrame_(frame)
        self.background_layer.setFrame_(frame)
        CATransaction.commit()
        self.label.setFrame_(((43, 11), (self._width - 55, 22)))

    @staticmethod
    def _icon_path(*, error: bool) -> Any:
        from Quartz import (
            CGPathAddLineToPoint,
            CGPathCreateMutable,
            CGPathMoveToPoint,
        )

        path = CGPathCreateMutable()
        if error:
            CGPathMoveToPoint(path, None, 17.0, 17.0)
            CGPathAddLineToPoint(path, None, 27.0, 27.0)
            CGPathMoveToPoint(path, None, 27.0, 17.0)
            CGPathAddLineToPoint(path, None, 17.0, 27.0)
        else:
            CGPathMoveToPoint(path, None, 15.5, 22.0)
            CGPathAddLineToPoint(path, None, 20.0, 17.5)
            CGPathAddLineToPoint(path, None, 28.8, 26.5)
        return path

    def _place_on_pointer_screen(self) -> None:
        from AppKit import NSEvent, NSMouseInRect, NSScreen

        pointer = NSEvent.mouseLocation()
        screens = list(NSScreen.screens())
        screen = next(
            (
                candidate
                for candidate in screens
                if NSMouseInRect(pointer, candidate.frame(), False)
            ),
            None,
        )
        screen = screen or NSScreen.mainScreen() or (screens[0] if screens else None)
        if screen is None:
            return
        frame = screen.visibleFrame()
        x = frame.origin.x + frame.size.width - self._width - SCREEN_MARGIN
        y = frame.origin.y + frame.size.height - PANEL_HEIGHT - SCREEN_MARGIN
        self.panel.setFrame_display_(
            ((x, y), (self._width, PANEL_HEIGHT)),
            True,
        )

    def _schedule_hide(self, duration: float) -> None:
        from Foundation import NSTimer

        self._timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            duration,
            False,
            self.hide,
        )

    def _cancel_timer(self) -> None:
        if self._timer is None:
            return
        self._timer.invalidate()
        self._timer = None

    def hide(self, _timer: Any = None) -> None:
        self._timer = None
        if self._closed or not self._visible:
            return
        self.panel.orderOut_(None)
        self._visible = False

    def close(self) -> None:
        if self._closed:
            return
        self._cancel_timer()
        if self._visible:
            self.panel.orderOut_(None)
            self._visible = False
        self._closed = True
