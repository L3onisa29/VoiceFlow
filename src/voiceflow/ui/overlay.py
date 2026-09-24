from __future__ import annotations

import collections
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from voiceflow.ui.status_controller import OverlayAccent, OverlayPresentation

BAR_COUNT = 12
DOT_COUNT = 5
PANEL_H = 30
PANEL_RADIUS = 15
CONTENT_PADDING_X = 12.0
BAR_WIDTH = 3.5
BAR_GAP = 3.0
DOT_SIZE = 4.5
DOT_STEP = 8.0
TRANSITION_SECONDS = 0.18
WAVEFORM_INTERVAL = 1 / 20
DOT_CYCLE_SECONDS = 1.05


@dataclass(frozen=True, slots=True)
class OverlayContentLayout:
    width: float
    padding_x: float = CONTENT_PADDING_X
    circular: bool = False

    def container_width(self, height: float) -> float:
        if self.circular:
            return height
        return self.width + self.padding_x * 2


@dataclass(frozen=True, slots=True)
class OverlayGeometry:
    width: float
    height: float
    bottom_offset: int


def content_layout_for_mode(mode: Any) -> OverlayContentLayout:
    from voiceflow.ui.status_controller import OverlayMode

    if mode == OverlayMode.WAVEFORM:
        content_width = BAR_COUNT * BAR_WIDTH + (BAR_COUNT - 1) * BAR_GAP
        return OverlayContentLayout(content_width)
    if mode == OverlayMode.PROCESSING:
        content_width = DOT_SIZE + (DOT_COUNT - 1) * DOT_STEP
        return OverlayContentLayout(content_width)
    return OverlayContentLayout(PANEL_H, padding_x=0.0, circular=True)


def geometry_for_mode(mode: Any) -> OverlayGeometry:
    content = content_layout_for_mode(mode)
    return OverlayGeometry(content.container_width(PANEL_H), PANEL_H, 50)


class Overlay:
    def __init__(self, level_fn: Any) -> None:
        from AppKit import (
            NSBackingStoreBuffered,
            NSColor,
            NSPanel,
            NSView,
            NSWindowStyleMaskBorderless,
            NSWindowStyleMaskNonactivatingPanel,
        )
        from Quartz import CALayer, CAShapeLayer

        self._level_fn = level_fn
        self._levels = collections.deque([0.02] * BAR_COUNT, maxlen=BAR_COUNT)
        self._waveform_timer: Any | None = None
        self._presentation: OverlayPresentation | None = None
        self._visible = False
        self._closed = False
        self._geometry = geometry_for_mode(None)
        self._placement: tuple[tuple[float, float], tuple[float, float]] | None = None

        self.panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (self._geometry.width, self._geometry.height)),
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

        self.view = NSView.alloc().initWithFrame_(
            ((0, 0), (self._geometry.width, self._geometry.height))
        )
        self.view.setWantsLayer_(True)
        self.root_layer = CALayer.layer()
        self.root_layer.setFrame_(((0, 0), (self._geometry.width, self._geometry.height)))
        self.view.setLayer_(self.root_layer)
        self.panel.setContentView_(self.view)

        self.background_layer = CALayer.layer()
        self.background_layer.setFrame_(((0, 0), (self._geometry.width, self._geometry.height)))
        self.background_layer.setCornerRadius_(PANEL_RADIUS)
        self.background_layer.setBackgroundColor_(
            NSColor.colorWithCalibratedWhite_alpha_(0.02, 0.88).CGColor()
        )
        self.background_layer.setBorderWidth_(0.5)
        self.background_layer.setBorderColor_(
            NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.22).CGColor()
        )
        self.root_layer.addSublayer_(self.background_layer)

        self.waveform_layer = CALayer.layer()
        self.waveform_layer.setFrame_(((0, 0), (self._geometry.width, self._geometry.height)))
        self.waveform_layer.setOpacity_(0.0)
        self.root_layer.addSublayer_(self.waveform_layer)
        self.waveform_bars = []
        for _index in range(BAR_COUNT):
            bar = CALayer.layer()
            bar.setCornerRadius_(1.25)
            self.waveform_layer.addSublayer_(bar)
            self.waveform_bars.append(bar)

        self.processing_layer = CALayer.layer()
        self.processing_layer.setFrame_(((0, 0), (self._geometry.width, self._geometry.height)))
        self.processing_layer.setOpacity_(0.0)
        self.root_layer.addSublayer_(self.processing_layer)
        self.processing_dots = []
        for _index in range(DOT_COUNT):
            dot = CALayer.layer()
            dot.setCornerRadius_(2.25)
            self.processing_layer.addSublayer_(dot)
            self.processing_dots.append(dot)

        self.outcome_layer = CAShapeLayer.layer()
        self.outcome_layer.setFrame_(((0, 0), (self._geometry.width, self._geometry.height)))
        self.outcome_layer.setFillColor_(None)
        self.outcome_layer.setLineWidth_(2.4)
        self.outcome_layer.setLineCap_("round")
        self.outcome_layer.setLineJoin_("round")
        self.outcome_layer.setOpacity_(0.0)
        self.root_layer.addSublayer_(self.outcome_layer)

        self._layout_content()

    def update(self, presentation: OverlayPresentation) -> None:
        from voiceflow.ui.status_controller import OverlayMode

        if self._closed or presentation == self._presentation:
            return
        previous = self._presentation
        self._presentation = presentation

        if not presentation.visible:
            self._stop_waveform_timer()
            self._stop_dot_animations()
            self._set_layer_opacities(None)
            if self._visible:
                self.panel.orderOut_(None)
                self._visible = False
            return

        self._apply_geometry(presentation.mode)
        self._set_accent(presentation.accent, previous)
        if presentation.mode == OverlayMode.WAVEFORM:
            self._levels.clear()
            self._levels.extend([0.02] * BAR_COUNT)
            self._layout_waveform()
            self._stop_dot_animations()
            self._set_layer_opacities(self.waveform_layer)
            self._start_waveform_timer()
        elif presentation.mode == OverlayMode.PROCESSING:
            self._stop_waveform_timer()
            self._start_dot_animations()
            self._set_layer_opacities(self.processing_layer)
        else:
            self._stop_waveform_timer()
            self._stop_dot_animations()
            self._set_outcome_path(presentation.mode)
            self._set_layer_opacities(self.outcome_layer)

        if not self._visible:
            self.panel.orderFrontRegardless()
            self._visible = True

    def _apply_geometry(self, mode: Any) -> None:
        from Quartz import CATransaction

        geometry = geometry_for_mode(mode)
        self._geometry = geometry
        CATransaction.begin()
        CATransaction.setDisableActions_(True)
        frame = ((0, 0), (geometry.width, geometry.height))
        self.root_layer.setFrame_(frame)
        self.background_layer.setFrame_(frame)
        self.waveform_layer.setFrame_(frame)
        self.processing_layer.setFrame_(frame)
        self.outcome_layer.setFrame_(frame)
        CATransaction.commit()
        self._layout_content()
        self._place_on_pointer_screen()

    def _layout_content(self) -> None:
        self._layout_waveform()
        self._layout_processing_dots()

    def _layout_processing_dots(self) -> None:
        from Quartz import CATransaction

        content_width = DOT_SIZE + (DOT_COUNT - 1) * DOT_STEP
        start_x = (self._geometry.width - content_width) / 2
        CATransaction.begin()
        CATransaction.setDisableActions_(True)
        for index, dot in enumerate(self.processing_dots):
            dot.setFrame_(
                (
                    (
                        start_x + index * DOT_STEP,
                        (self._geometry.height - DOT_SIZE) / 2,
                    ),
                    (DOT_SIZE, DOT_SIZE),
                )
            )
        CATransaction.commit()

    def _set_layer_opacities(self, visible_layer: Any | None) -> None:
        from Quartz import CABasicAnimation, CATransaction

        layers = (self.waveform_layer, self.processing_layer, self.outcome_layer)
        CATransaction.begin()
        CATransaction.setDisableActions_(True)
        for layer in layers:
            layer.setOpacity_(1.0 if layer is visible_layer else 0.0)
        CATransaction.commit()
        if visible_layer is not None:
            fade = CABasicAnimation.animationWithKeyPath_("opacity")
            fade.setFromValue_(0.0)
            fade.setToValue_(1.0)
            fade.setDuration_(TRANSITION_SECONDS)
            visible_layer.addAnimation_forKey_(fade, "content-crossfade")

    def _set_accent(
        self,
        accent: OverlayAccent,
        previous: OverlayPresentation | None,
    ) -> None:
        from Quartz import CABasicAnimation, CATransaction

        color = self._color_for(accent)
        prior_color = self._color_for(previous.accent) if previous is not None else color
        CATransaction.begin()
        CATransaction.setDisableActions_(True)
        for layer in (*self.waveform_bars, *self.processing_dots):
            layer.setBackgroundColor_(color)
        self.outcome_layer.setStrokeColor_(color)
        CATransaction.commit()
        if previous is not None and previous.accent != accent:
            for layer in (*self.waveform_bars, *self.processing_dots):
                animation = CABasicAnimation.animationWithKeyPath_("backgroundColor")
                animation.setFromValue_(prior_color)
                animation.setToValue_(color)
                animation.setDuration_(TRANSITION_SECONDS)
                layer.addAnimation_forKey_(animation, "accent-crossfade")
            stroke = CABasicAnimation.animationWithKeyPath_("strokeColor")
            stroke.setFromValue_(prior_color)
            stroke.setToValue_(color)
            stroke.setDuration_(TRANSITION_SECONDS)
            self.outcome_layer.addAnimation_forKey_(stroke, "accent-crossfade")

    @staticmethod
    def _color_for(accent: OverlayAccent) -> Any:
        from AppKit import NSColor

        from voiceflow.ui.status_controller import OverlayAccent

        colors = {
            OverlayAccent.CORAL: (0.98, 0.28, 0.31),
            OverlayAccent.AMBER: (1.0, 0.68, 0.20),
            OverlayAccent.PURPLE: (0.65, 0.38, 0.96),
            OverlayAccent.CYAN: (0.26, 0.70, 0.96),
            OverlayAccent.GREEN: (0.24, 0.82, 0.49),
            OverlayAccent.RED: (1.0, 0.25, 0.29),
        }
        red, green, blue = colors[accent]
        return NSColor.colorWithCalibratedRed_green_blue_alpha_(
            red,
            green,
            blue,
            1.0,
        ).CGColor()

    def _layout_waveform(self) -> None:
        from Quartz import CATransaction

        total_width = BAR_COUNT * BAR_WIDTH + (BAR_COUNT - 1) * BAR_GAP
        start_x = (self._geometry.width - total_width) / 2
        CATransaction.begin()
        CATransaction.setDisableActions_(True)
        for index, (bar, level) in enumerate(zip(self.waveform_bars, self._levels, strict=True)):
            normalized = max(0.0, min(1.0, float(level) * 10))
            height = 3.0 + normalized * 15.0
            bar.setFrame_(
                (
                    (
                        start_x + index * (BAR_WIDTH + BAR_GAP),
                        (self._geometry.height - height) / 2,
                    ),
                    (BAR_WIDTH, height),
                )
            )
            bar.setCornerRadius_(BAR_WIDTH / 2)
        CATransaction.commit()

    def _start_waveform_timer(self) -> None:
        if self._waveform_timer is not None or self._closed:
            return
        from Foundation import NSRunLoop, NSRunLoopCommonModes, NSTimer

        self._waveform_timer = NSTimer.timerWithTimeInterval_repeats_block_(
            WAVEFORM_INTERVAL,
            True,
            self._tick_waveform,
        )
        NSRunLoop.mainRunLoop().addTimer_forMode_(
            self._waveform_timer,
            NSRunLoopCommonModes,
        )

    def _stop_waveform_timer(self) -> None:
        if self._waveform_timer is None:
            return
        self._waveform_timer.invalidate()
        self._waveform_timer = None

    def _tick_waveform(self, _timer: Any) -> None:
        if self._closed or not self._visible:
            self._stop_waveform_timer()
            return
        self._place_on_pointer_screen()
        self._levels.append(self._level_fn())
        self._layout_waveform()

    def _start_dot_animations(self) -> None:
        from Quartz import (
            CAAnimationGroup,
            CACurrentMediaTime,
            CAKeyframeAnimation,
            CAMediaTimingFunction,
            kCAMediaTimingFunctionEaseInEaseOut,
        )

        start_time = CACurrentMediaTime()
        timing = CAMediaTimingFunction.functionWithName_(kCAMediaTimingFunctionEaseInEaseOut)
        for index, dot in enumerate(self.processing_dots):
            if dot.animationForKey_("processing-wave") is not None:
                continue
            vertical = CAKeyframeAnimation.animationWithKeyPath_("transform.translation.y")
            vertical.setValues_([0.0, 3.0, 0.0, -1.0, 0.0])
            vertical.setKeyTimes_([0.0, 0.25, 0.5, 0.75, 1.0])
            vertical.setTimingFunctions_([timing, timing, timing, timing])

            scale = CAKeyframeAnimation.animationWithKeyPath_("transform.scale")
            scale.setValues_([0.82, 1.12, 0.92, 0.82, 0.82])
            scale.setKeyTimes_([0.0, 0.25, 0.5, 0.75, 1.0])
            scale.setTimingFunctions_([timing, timing, timing, timing])

            opacity = CAKeyframeAnimation.animationWithKeyPath_("opacity")
            opacity.setValues_([0.42, 1.0, 0.68, 0.42, 0.42])
            opacity.setKeyTimes_([0.0, 0.25, 0.5, 0.75, 1.0])
            opacity.setTimingFunctions_([timing, timing, timing, timing])

            group = CAAnimationGroup.animation()
            group.setAnimations_([vertical, scale, opacity])
            group.setDuration_(DOT_CYCLE_SECONDS)
            group.setBeginTime_(start_time + index * 0.09)
            group.setRepeatCount_(float("inf"))
            group.setRemovedOnCompletion_(False)
            dot.addAnimation_forKey_(group, "processing-wave")

    def _stop_dot_animations(self) -> None:
        for dot in self.processing_dots:
            dot.removeAnimationForKey_("processing-wave")

    def _set_outcome_path(self, mode: Any) -> None:
        from Quartz import (
            CATransaction,
            CGPathAddLineToPoint,
            CGPathCloseSubpath,
            CGPathCreateMutable,
            CGPathMoveToPoint,
        )

        from voiceflow.ui.status_controller import OverlayMode

        path = CGPathCreateMutable()
        center_x = self._geometry.width / 2
        if mode == OverlayMode.CANCELLED:
            # A compact octagonal road sign with a line-art STOP label.
            points = (
                (center_x - 4.0, 5.5),
                (center_x + 4.0, 5.5),
                (center_x + 9.5, 11.0),
                (center_x + 9.5, 19.0),
                (center_x + 4.0, 24.5),
                (center_x - 4.0, 24.5),
                (center_x - 9.5, 19.0),
                (center_x - 9.5, 11.0),
            )
            CGPathMoveToPoint(path, None, *points[0])
            for point in points[1:]:
                CGPathAddLineToPoint(path, None, *point)
            CGPathCloseSubpath(path)

            top, middle, bottom = 17.5, 15.0, 12.5
            start_x = center_x - 6.5

            # S
            CGPathMoveToPoint(path, None, start_x + 2.5, top)
            CGPathAddLineToPoint(path, None, start_x, top)
            CGPathAddLineToPoint(path, None, start_x, middle)
            CGPathAddLineToPoint(path, None, start_x + 2.5, middle)
            CGPathAddLineToPoint(path, None, start_x + 2.5, bottom)
            CGPathAddLineToPoint(path, None, start_x, bottom)

            # T
            start_x += 3.5
            CGPathMoveToPoint(path, None, start_x, top)
            CGPathAddLineToPoint(path, None, start_x + 2.5, top)
            CGPathMoveToPoint(path, None, start_x + 1.25, top)
            CGPathAddLineToPoint(path, None, start_x + 1.25, bottom)

            # O
            start_x += 3.5
            CGPathMoveToPoint(path, None, start_x, bottom)
            CGPathAddLineToPoint(path, None, start_x, top)
            CGPathAddLineToPoint(path, None, start_x + 2.5, top)
            CGPathAddLineToPoint(path, None, start_x + 2.5, bottom)
            CGPathCloseSubpath(path)

            # P
            start_x += 3.5
            CGPathMoveToPoint(path, None, start_x, bottom)
            CGPathAddLineToPoint(path, None, start_x, top)
            CGPathAddLineToPoint(path, None, start_x + 2.5, top)
            CGPathAddLineToPoint(path, None, start_x + 2.5, middle)
            CGPathAddLineToPoint(path, None, start_x, middle)
        elif mode == OverlayMode.COPIED:
            # Rear sheet: only the exposed edges are drawn to keep the
            # overlapping-paper silhouette legible at menu-overlay size.
            CGPathMoveToPoint(path, None, center_x - 6.0, 13.0)
            CGPathAddLineToPoint(path, None, center_x - 6.0, 21.0)
            CGPathAddLineToPoint(path, None, center_x + 3.0, 21.0)
            CGPathAddLineToPoint(path, None, center_x + 3.0, 18.0)

            # Front sheet.
            CGPathMoveToPoint(path, None, center_x - 3.0, 9.0)
            CGPathAddLineToPoint(path, None, center_x + 7.0, 9.0)
            CGPathAddLineToPoint(path, None, center_x + 7.0, 18.0)
            CGPathAddLineToPoint(path, None, center_x - 3.0, 18.0)
            CGPathAddLineToPoint(path, None, center_x - 3.0, 9.0)

            # Small success check inside the front sheet.
            CGPathMoveToPoint(path, None, center_x - 1.0, 13.5)
            CGPathAddLineToPoint(path, None, center_x + 1.0, 11.5)
            CGPathAddLineToPoint(path, None, center_x + 5.0, 15.5)
        elif mode in {OverlayMode.SUCCESS, OverlayMode.WARNING}:
            CGPathMoveToPoint(path, None, center_x - 6.5, 15.0)
            CGPathAddLineToPoint(path, None, center_x - 2.0, 10.5)
            CGPathAddLineToPoint(path, None, center_x + 6.8, 19.5)
        else:
            CGPathMoveToPoint(path, None, center_x - 5.0, 10.0)
            CGPathAddLineToPoint(path, None, center_x + 5.0, 20.0)
            CGPathMoveToPoint(path, None, center_x + 5.0, 10.0)
            CGPathAddLineToPoint(path, None, center_x - 5.0, 20.0)
        CATransaction.begin()
        CATransaction.setDisableActions_(True)
        line_width = (
            1.2 if mode == OverlayMode.CANCELLED else 1.8 if mode == OverlayMode.COPIED else 2.4
        )
        self.outcome_layer.setLineWidth_(line_width)
        self.outcome_layer.setPath_(path)
        CATransaction.commit()

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
        x = frame.origin.x + (frame.size.width - self._geometry.width) / 2
        y = frame.origin.y + self._geometry.bottom_offset
        placement = ((x, y), (self._geometry.width, self._geometry.height))
        if placement == self._placement:
            return
        self.panel.setFrame_display_(placement, True)
        self._placement = placement

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop_waveform_timer()
        self._stop_dot_animations()
        for layer in (
            self.waveform_layer,
            self.processing_layer,
            self.outcome_layer,
        ):
            layer.removeAllAnimations()
        if self._visible:
            self.panel.orderOut_(None)
            self._visible = False
