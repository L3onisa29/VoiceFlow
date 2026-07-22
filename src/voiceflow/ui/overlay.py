from __future__ import annotations

import collections
from typing import Any

BAR_COUNT = 24
PANEL_W, PANEL_H = 310, 78


class Overlay:
    def __init__(self, level_fn: Any) -> None:
        import objc
        from AppKit import (
            NSBackingStoreBuffered,
            NSBezierPath,
            NSColor,
            NSFont,
            NSFontAttributeName,
            NSForegroundColorAttributeName,
            NSMutableParagraphStyle,
            NSPanel,
            NSParagraphStyleAttributeName,
            NSScreen,
            NSTimer,
            NSView,
            NSWindowStyleMaskBorderless,
            NSWindowStyleMaskNonactivatingPanel,
        )
        from Foundation import NSString

        class OverlayView(NSView):
            def initWithFrame_(view_self, frame: Any) -> Any:
                view_self = objc.super(OverlayView, view_self).initWithFrame_(frame)
                if view_self is None:
                    return None
                view_self.levels = collections.deque([0.02] * BAR_COUNT, maxlen=BAR_COUNT)
                view_self.text = "Inizializzazione…"
                view_self.waveform = False
                view_self.phase = 0
                return view_self

            def drawRect_(view_self, _rect: Any) -> None:
                bounds = view_self.bounds()
                NSColor.colorWithCalibratedWhite_alpha_(0.03, 0.94).setFill()
                NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(bounds, 16, 16).fill()
                style = NSMutableParagraphStyle.alloc().init()
                style.setAlignment_(1)
                attrs = {
                    NSFontAttributeName: NSFont.boldSystemFontOfSize_(12),
                    NSForegroundColorAttributeName: NSColor.whiteColor(),
                    NSParagraphStyleAttributeName: style,
                }
                if view_self.waveform:
                    pad, gap = 18, 3
                    bar_width = (bounds.size.width - 2 * pad - gap * (BAR_COUNT - 1)) / BAR_COUNT
                    NSColor.colorWithCalibratedRed_green_blue_alpha_(0.9, 0.2, 0.3, 1).setFill()
                    for index, level in enumerate(view_self.levels):
                        height = max(4, min(1.0, level * 10) * 35)
                        x = pad + index * (bar_width + gap)
                        y = 31 + (35 - height) / 2
                        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                            ((x, y), (bar_width, height)), bar_width / 2, bar_width / 2
                        ).fill()
                    text_rect = ((0, 8), (bounds.size.width, 16))
                else:
                    dots = "." * (view_self.phase % 4)
                    view_self.text = view_self.text.rstrip(".") + dots
                    text_rect = ((0, 29), (bounds.size.width, 18))
                NSString.stringWithString_(view_self.text).drawInRect_withAttributes_(
                    text_rect, attrs
                )

        screen = NSScreen.mainScreen().frame()
        x = (screen.size.width - PANEL_W) / 2
        self.panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            ((x, 110), (PANEL_W, PANEL_H)),
            NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
            NSBackingStoreBuffered,
            False,
        )
        self.panel.setLevel_(25)
        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(NSColor.clearColor())
        self.panel.setIgnoresMouseEvents_(True)
        self.panel.setCollectionBehavior_(1)
        self.view = OverlayView.alloc().initWithFrame_(((0, 0), (PANEL_W, PANEL_H)))
        self.panel.setContentView_(self.view)
        self._level_fn = level_fn
        self._timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            1 / 20.0, True, self._tick
        )

    def _tick(self, _timer: Any) -> None:
        self.view.phase += 1
        if self.view.waveform:
            self.view.levels.append(self._level_fn())
        self.view.setNeedsDisplay_(True)

    def update(self, *, text: str, visible: bool, waveform: bool) -> None:
        self.view.text = text
        self.view.waveform = waveform
        if waveform:
            self.view.levels.extend([0.02] * BAR_COUNT)
        if visible:
            self.panel.orderFrontRegardless()
        else:
            self.panel.orderOut_(None)

    def close(self) -> None:
        self._timer.invalidate()
        self.panel.orderOut_(None)

