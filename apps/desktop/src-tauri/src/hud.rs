//! A genuine nonactivating NSPanel: clicking its controls must leave the editor's
//! caret and application focus untouched. All AppKit objects stay on main.
//!
//! Layout, left to right: the secondary action, the status capsule and the primary
//! action, as three separate rounded shapes. The capsule is as wide as its content, so
//! the panel resizes around its centre when the phase changes. Holding ⌥ turns the
//! actions into their alternatives: cancel instead of pause, copy instead of paste.
use crate::service::{Command, Service, Snapshot};
use objc2::rc::Retained;
use objc2::runtime::{AnyClass, AnyObject};
use objc2::{DefinedClass, MainThreadOnly, define_class, msg_send, sel};
use objc2_app_kit::*;
use objc2_foundation::{MainThreadMarker, NSPoint, NSRect, NSSize, NSString, NSTimer};
use std::{
    cell::{Cell, RefCell},
    time::Instant,
};
use tauri::{AppHandle, Manager};

const HEIGHT: f64 = 44.0;
const BUTTON: f64 = 44.0;
const GAP: f64 = 6.0;
const RADIUS: f64 = 10.0;
const PADDING: f64 = 14.0;
const DOT: f64 = 8.0;
const SPACING: f64 = 10.0;
const BARS: usize = 13;
const BAR_WIDTH: f64 = 3.0;
const BAR_STEP: f64 = 5.0;
const METER_WIDTH: f64 = BAR_STEP * (BARS - 1) as f64 + BAR_WIDTH;
const SPINNER: f64 = 16.0;
/// Where the capsule starts inside the panel.
const CAPSULE_X: f64 = BUTTON + GAP;
const METER_X: f64 = CAPSULE_X + PADDING + DOT + SPACING;
const SPINNER_CENTER: NSPoint = NSPoint::new(CAPSULE_X + PADDING + SPINNER / 2.0, HEIGHT / 2.0);

fn visible_phase(phase: &str) -> bool {
    matches!(
        phase,
        "loading" | "recording" | "paused" | "stopping" | "finalizing" | "error"
    )
}
fn busy_phase(phase: &str) -> bool {
    matches!(phase, "loading" | "stopping" | "finalizing")
}
fn timer_label(phase: &str, elapsed_seconds: u64) -> String {
    match phase {
        "loading" => "Avvio…".into(),
        "stopping" | "finalizing" => "Chiudo…".into(),
        _ => format!("{:02}:{:02}", elapsed_seconds / 60, elapsed_seconds % 60),
    }
}
/// The capsule says what failed once; the panel above says what happens to the text.
fn error_label(message: Option<&str>) -> &'static str {
    match message {
        Some(message) if message.to_lowercase().contains("microfono") => {
            "Microfono non disponibile"
        }
        _ => "Dettatura interrotta",
    }
}
fn rect(x: f64, y: f64, w: f64, h: f64) -> NSRect {
    NSRect::new(NSPoint::new(x, y), NSSize::new(w, h))
}
fn color(r: f64, g: f64, b: f64, a: f64) -> Retained<NSColor> {
    NSColor::colorWithSRGBRed_green_blue_alpha(r, g, b, a)
}
fn orange(alpha: f64) -> Retained<NSColor> {
    color(1.0, 0.54, 0.24, alpha)
}
fn surface() -> Retained<NSColor> {
    color(0.055, 0.055, 0.055, 0.97)
}

struct Meter {
    levels: [f64; BARS],
    recording: bool,
    busy: bool,
    from_busy: f64,
    changed: Instant,
    started: Instant,
}
impl Default for Meter {
    fn default() -> Self {
        let now = Instant::now();
        Self {
            levels: [0.0; BARS],
            recording: false,
            busy: false,
            from_busy: 0.0,
            changed: now,
            started: now,
        }
    }
}
impl Meter {
    fn busy_mix(&self, now: Instant) -> f64 {
        let t = (now.duration_since(self.changed).as_secs_f64() / 0.28).min(1.0);
        let eased = t * t * (3.0 - 2.0 * t);
        self.from_busy + (f64::from(self.busy) - self.from_busy) * eased
    }
    fn set_phase(&mut self, phase: &str, now: Instant) {
        let busy = busy_phase(phase);
        if self.busy != busy {
            self.from_busy = self.busy_mix(now);
            self.changed = now;
            self.busy = busy;
        }
        self.recording = phase == "recording";
        if !self.recording {
            self.levels.fill(0.0);
        }
    }
    fn push(&mut self, level: f32) {
        self.levels.rotate_left(1);
        self.levels[BARS - 1] = f64::from(level * 14.0).clamp(0.0, 1.0);
    }
    /// Bars of the meter, gathered into a turning ring while busy.
    fn mark(&self, index: usize, now: Instant) -> (NSRect, f64) {
        let mix = self.busy_mix(now);
        let angle = index as f64 * std::f64::consts::TAU / BARS as f64
            - now.duration_since(self.started).as_secs_f64() * std::f64::consts::TAU;
        // Louder in the middle, as a voice is drawn.
        let envelope = (std::f64::consts::PI * (index as f64 + 1.0) / (BARS as f64 + 1.0)).sin();
        let height = 3.0
            + if self.recording {
                self.levels[index] * 22.0 * envelope * (1.0 - mix)
            } else {
                0.0
            };
        let x = METER_X + index as f64 * BAR_STEP;
        let x = x + (SPINNER_CENTER.x - 1.5 + angle.cos() * 6.0 - x) * mix;
        let y = (HEIGHT - height) / 2.0 + angle.sin() * 6.0 * mix;
        (
            rect(x, y, BAR_WIDTH, height),
            1.0 - mix * (1.0 - index as f64 / (BARS - 1) as f64) * 0.75,
        )
    }
}

/// Which action each button performs now.
#[derive(Clone, Copy, PartialEq, Debug)]
enum Action {
    Pause,
    Resume,
    Cancel,
    Retry,
    Recents,
    Stop,
    StopCopy,
    Dismiss,
    Clear,
}
fn actions(phase: &str, recoverable: bool, option: bool) -> (Action, Action) {
    match phase {
        "error" if recoverable => (Action::Retry, Action::Dismiss),
        "error" => (Action::Recents, Action::Clear),
        _ if option => (Action::Cancel, Action::StopCopy),
        "paused" => (Action::Resume, Action::Stop),
        _ => (Action::Pause, Action::Stop),
    }
}
fn describe(action: Action) -> (&'static str, &'static str) {
    match action {
        Action::Pause => ("pause.fill", "Pausa"),
        Action::Resume => ("play.fill", "Riprendi"),
        Action::Cancel => ("xmark", "Annulla · ⌃ Spazio"),
        Action::Retry => ("arrow.clockwise", "Riprova: riapre il microfono e continua"),
        Action::Recents => ("list.bullet", "Apri i recenti · ⌃⌥V"),
        Action::Stop => ("stop.fill", "Termina e inserisci · ⌥ Spazio"),
        Action::StopCopy => ("doc.on.doc", "Termina e copia · ⇧⌥ Spazio"),
        Action::Dismiss => ("xmark", "Chiudi e salva il testo nei recenti"),
        Action::Clear => ("xmark", "Chiudi"),
    }
}

struct ViewIvars {
    app: AppHandle,
    meter: RefCell<Meter>,
    phase: RefCell<String>,
    actions: Cell<(Action, Action)>,
}
define_class!(
    #[unsafe(super = NSView)]
    #[thread_kind = MainThreadOnly]
    #[ivars = ViewIvars]
    struct HudView;
    impl HudView {
        #[unsafe(method(acceptsFirstMouse:))]
        fn accepts_first_mouse(&self, _event: Option<&NSEvent>) -> bool { true }
        #[unsafe(method(drawRect:))]
        fn draw(&self, _dirty: NSRect) {
            let width = self.bounds().size.width;
            let phase = self.ivars().phase.borrow().clone();
            let busy = busy_phase(&phase);
            let error = phase == "error";
            let now = Instant::now();
            let shape = |frame: NSRect, fill: &NSColor, border: bool| {
                let path = NSBezierPath::bezierPathWithRoundedRect_xRadius_yRadius(
                    rect(frame.origin.x + 0.5, frame.origin.y + 0.5, frame.size.width - 1.0, frame.size.height - 1.0),
                    RADIUS, RADIUS,
                );
                fill.setFill();
                path.fill();
                if border {
                    color(0.15, 0.15, 0.15, 1.0).setStroke();
                    path.setLineWidth(1.0);
                    path.stroke();
                }
            };
            shape(rect(0.0, 0.0, BUTTON, HEIGHT), &surface(), true);
            shape(rect(CAPSULE_X, 0.0, width - 2.0 * CAPSULE_X, HEIGHT), &surface(), true);
            if error {
                shape(rect(width - BUTTON, 0.0, BUTTON, HEIGHT), &surface(), true);
            } else {
                shape(rect(width - BUTTON, 0.0, BUTTON, HEIGHT), &orange(if busy { 0.45 } else { 1.0 }), false);
            }
            // Status dot: pulses while recording, grey in pause, red on error.
            if !busy {
                let started = self.ivars().meter.borrow().started;
                let pulse = 0.25 + 0.75 * (0.5 + 0.5 * (now.duration_since(started).as_secs_f64() * 4.5).cos());
                let fill = match phase.as_str() {
                    "recording" => orange(pulse),
                    "error" => color(1.0, 0.35, 0.32, 1.0),
                    _ => color(0.47, 0.47, 0.47, 1.0),
                };
                fill.setFill();
                NSBezierPath::bezierPathWithOvalInRect(rect(CAPSULE_X + PADDING, (HEIGHT - DOT) / 2.0, DOT, DOT)).fill();
            }
            if !error {
                let meter = self.ivars().meter.borrow();
                for i in 0..BARS {
                    let (frame, alpha) = meter.mark(i, now);
                    if phase == "paused" {
                        color(0.33, 0.33, 0.33, 1.0).setFill();
                    } else {
                        orange(alpha).setFill();
                    }
                    NSBezierPath::bezierPathWithRoundedRect_xRadius_yRadius(frame, 1.5, 1.5).fill();
                }
            }
        }
        #[unsafe(method(pauseClicked:))]
        fn pause_clicked(&self, _sender: &AnyObject) {
            self.perform(self.ivars().actions.get().0);
        }
        #[unsafe(method(finishClicked:))]
        fn finish_clicked(&self, _sender: &AnyObject) {
            self.perform(self.ivars().actions.get().1);
        }
    }
);
impl HudView {
    fn perform(&self, action: Action) {
        let app = &self.ivars().app;
        let service = app.state::<Service>();
        let _ = match action {
            Action::Pause | Action::Resume | Action::Retry => service.send(Command::Pause),
            Action::Cancel | Action::Clear => service.send(Command::Cancel),
            Action::Stop => service.send(Command::Stop),
            Action::StopCopy => service.send(Command::StopCopy),
            Action::Dismiss => service.send(Command::Dismiss),
            Action::Recents => {
                crate::overlay::toggle_recents(app);
                service.send(Command::Cancel)
            }
        };
        service.publish(app);
    }
}
define_class!(
    #[unsafe(super = NSPanel)]
    #[thread_kind = MainThreadOnly]
    struct RecordingPanel;
    impl RecordingPanel {
        #[unsafe(method(canBecomeKeyWindow))]
        fn can_become_key(&self) -> bool { false }
        #[unsafe(method(canBecomeMainWindow))]
        fn can_become_main(&self) -> bool { false }
    }
);
struct Hud {
    revision: Cell<u64>,
    panel: Retained<RecordingPanel>,
    view: Retained<HudView>,
    label: Retained<NSTextField>,
    pause: Retained<NSButton>,
    finish: Retained<NSButton>,
    animation: RefCell<Option<Retained<NSTimer>>>,
    recoverable: Cell<bool>,
    option: Cell<bool>,
    /// Phase and text the capsule was last sized for.
    layout: RefCell<(String, String)>,
}
thread_local! { static HUD: RefCell<Option<Hud>> = const { RefCell::new(None) }; }

fn symbol(name: &str, description: &str) -> Option<Retained<NSImage>> {
    let image = NSImage::imageWithSystemSymbolName_accessibilityDescription(
        &NSString::from_str(name),
        Some(&NSString::from_str(description)),
    )?;
    let configuration = NSImageSymbolConfiguration::configurationWithPointSize_weight(13.0, 0.3);
    image.imageWithSymbolConfiguration(&configuration)
}

fn button(
    view: &HudView,
    action: objc2::runtime::Sel,
    mtm: MainThreadMarker,
) -> Retained<NSButton> {
    let button = unsafe {
        NSButton::buttonWithTitle_target_action(
            &NSString::from_str(""),
            Some(view),
            Some(action),
            mtm,
        )
    };
    button.setBordered(false);
    button.setImagePosition(NSCellImagePosition::ImageOnly);
    button
}

/// Animate a window to `frame` without blocking the main thread.
pub(crate) fn animate_frame(window: &NSWindow, frame: NSRect, duration: f64) {
    let Some(context) = AnyClass::get(c"NSAnimationContext") else {
        window.setFrame_display(frame, true);
        return;
    };
    unsafe {
        let _: () = msg_send![context, beginGrouping];
        let current: *mut AnyObject = msg_send![context, currentContext];
        let _: () = msg_send![current, setDuration: duration];
        let animator: *mut AnyObject = msg_send![window, animator];
        let _: () = msg_send![animator, setFrame: frame, display: true];
        let _: () = msg_send![context, endGrouping];
    }
}

impl Hud {
    fn new(app: AppHandle, mtm: MainThreadMarker) -> Self {
        let width = 2.0 * CAPSULE_X + 120.0;
        let allocated = RecordingPanel::alloc(mtm);
        let panel: Retained<RecordingPanel> = unsafe {
            msg_send![allocated,
                initWithContentRect: rect(0.0, 0.0, width, HEIGHT),
                styleMask: NSWindowStyleMask::Borderless | NSWindowStyleMask::NonactivatingPanel,
                backing: NSBackingStoreType::Buffered, defer: false
            ]
        };
        unsafe {
            panel.setReleasedWhenClosed(false);
        }
        panel.setOpaque(false);
        panel.setBackgroundColor(Some(&NSColor::clearColor()));
        panel.setHasShadow(true);
        panel.setLevel(25);
        panel.setHidesOnDeactivate(false);
        panel.setBecomesKeyOnlyIfNeeded(true);
        panel.setCollectionBehavior(
            NSWindowCollectionBehavior::CanJoinAllSpaces
                | NSWindowCollectionBehavior::FullScreenAuxiliary,
        );
        let allocated = HudView::alloc(mtm).set_ivars(ViewIvars {
            app,
            meter: RefCell::default(),
            phase: RefCell::default(),
            actions: Cell::new((Action::Pause, Action::Stop)),
        });
        let view: Retained<HudView> =
            unsafe { msg_send![super(allocated), initWithFrame: rect(0.0, 0.0, width, HEIGHT)] };
        view.setAutoresizingMask(
            NSAutoresizingMaskOptions::ViewWidthSizable
                | NSAutoresizingMaskOptions::ViewHeightSizable,
        );
        let label = NSTextField::labelWithString(&NSString::from_str("00:00"), mtm);
        label.setTextColor(Some(&color(0.87, 0.87, 0.87, 1.0)));
        let pause = button(&view, sel!(pauseClicked:), mtm);
        pause.setFrame(rect(0.0, 0.0, BUTTON, HEIGHT));
        let finish = button(&view, sel!(finishClicked:), mtm);
        finish.setFrame(rect(width - BUTTON, 0.0, BUTTON, HEIGHT));
        // The primary action stays on the right edge while the panel resizes.
        finish.setAutoresizingMask(NSAutoresizingMaskOptions::ViewMinXMargin);
        view.addSubview(&label);
        view.addSubview(&pause);
        view.addSubview(&finish);
        panel.setContentView(Some(&view));
        Self {
            revision: Cell::new(0),
            panel,
            view,
            label,
            pause,
            finish,
            animation: RefCell::default(),
            recoverable: Cell::new(false),
            option: Cell::new(false),
            layout: RefCell::default(),
        }
    }
    fn hide(&self) {
        if let Some(timer) = self.animation.borrow_mut().take() {
            timer.invalidate();
        }
        self.panel.orderOut(None);
        crate::overlay::hide_live();
    }
    fn animate(&self) {
        if self.animation.borrow().is_some() {
            return;
        }
        // AppKit keeps animating even while the inference worker is loading or decoding.
        // The callback captures no view or panel, so it cannot retain the HUD in a cycle.
        let block = block2::RcBlock::new(|_| {
            HUD.with(|slot| {
                if let Some(hud) = slot.borrow().as_ref() {
                    hud.tick();
                }
            });
        });
        let timer = unsafe {
            NSTimer::scheduledTimerWithTimeInterval_repeats_block(1.0 / 60.0, true, &block)
        };
        *self.animation.borrow_mut() = Some(timer);
    }
    fn tick(&self) {
        let option = NSEvent::modifierFlags_class().contains(NSEventModifierFlags::Option);
        if option != self.option.get() {
            self.option.set(option);
            self.apply_actions();
        }
        if *self.view.ivars().phase.borrow() == "paused" {
            // The paused counter blinks, like a stopped clock.
            let started = self.view.ivars().meter.borrow().started;
            let on = (started.elapsed().as_secs_f64() * 1.25).fract() < 0.6;
            self.label.setAlphaValue(if on { 0.55 } else { 0.25 });
        }
        self.view.setNeedsDisplay(true);
    }
    fn apply_actions(&self) {
        let phase = self.view.ivars().phase.borrow().clone();
        let busy = busy_phase(&phase);
        let pair = actions(&phase, self.recoverable.get(), self.option.get() && !busy);
        self.view.ivars().actions.set(pair);
        let light = color(0.87, 0.87, 0.87, 1.0);
        let on_orange = if phase == "error" {
            light.clone()
        } else {
            color(0.1, 0.05, 0.02, 1.0)
        };
        for (button, action, tint) in [
            (&self.pause, pair.0, light),
            (&self.finish, pair.1, on_orange),
        ] {
            let (name, description) = describe(action);
            button.setImage(symbol(name, description).as_deref());
            button.setContentTintColor(Some(&tint));
            button.setToolTip(Some(&NSString::from_str(description)));
            button.setAccessibilityLabel(Some(&NSString::from_str(description)));
            button.setEnabled(!busy);
        }
    }
    fn level(&self, revision: u64, level: f32) {
        // A level measured before a later transition must not animate that phase.
        if revision < self.revision.get() || *self.view.ivars().phase.borrow() != "recording" {
            return;
        }
        self.view.ivars().meter.borrow_mut().push(level);
    }
    /// Size the capsule to its content and keep the panel centred where it is.
    fn layout(&self, state: &Snapshot) {
        let phase = state.phase.as_str();
        let error = phase == "error";
        let busy = busy_phase(phase);
        let text = if error {
            error_label(state.message.as_deref()).to_owned()
        } else {
            timer_label(phase, state.elapsed_seconds)
        };
        let key = (
            phase.to_owned(),
            if error || busy {
                text.clone()
            } else {
                String::new()
            },
        );
        if *self.layout.borrow() == key {
            self.label.setStringValue(&NSString::from_str(&text));
            return;
        }
        *self.layout.borrow_mut() = key;
        let font = if error {
            NSFont::systemFontOfSize_weight(12.5, 0.0)
        } else {
            NSFont::monospacedDigitSystemFontOfSize_weight(13.0, 0.23)
        };
        self.label.setFont(Some(&font));
        let text_color = if error {
            color(0.94, 0.7, 0.68, 1.0)
        } else {
            color(0.87, 0.87, 0.87, 1.0)
        };
        self.label.setTextColor(Some(&text_color));
        self.label.setAlphaValue(1.0);
        // The counter is measured at its widest so seconds never resize the capsule.
        let measured = if !error && !busy { "00:00" } else { &text };
        self.label.setStringValue(&NSString::from_str(measured));
        self.label.sizeToFit();
        let size = self.label.frame().size;
        self.label.setStringValue(&NSString::from_str(&text));
        let start = if busy {
            CAPSULE_X + PADDING + SPINNER + SPACING - 2.0
        } else if error {
            CAPSULE_X + PADDING + DOT + SPACING
        } else {
            METER_X + METER_WIDTH + SPACING
        };
        self.label.setFrame(rect(
            start,
            (HEIGHT - size.height) / 2.0,
            size.width + 2.0,
            size.height,
        ));
        let width = (start + size.width + PADDING + GAP + BUTTON).round();
        let frame = self.panel.frame();
        if (frame.size.width - width).abs() < 0.5 {
            return;
        }
        let centre = frame.origin.x + frame.size.width / 2.0;
        let target = rect(
            (centre - width / 2.0).round(),
            frame.origin.y,
            width,
            HEIGHT,
        );
        if self.panel.isVisible() {
            animate_frame(&self.panel, target, 0.22);
        } else {
            self.panel.setFrame_display(target, false);
        }
    }
    fn update(&self, state: &Snapshot, mtm: MainThreadMarker) {
        if state.revision < self.revision.get() {
            return;
        }
        self.revision.set(state.revision);
        if !visible_phase(&state.phase) {
            self.hide();
            return;
        }
        *self.view.ivars().phase.borrow_mut() = state.phase.clone();
        self.recoverable.set(state.recoverable);
        // Bars advance only on `level`, at the meter's pace: a state that arrives in
        // between, such as new text, must not add a step of its own.
        self.view
            .ivars()
            .meter
            .borrow_mut()
            .set_phase(&state.phase, Instant::now());
        let first = !self.panel.isVisible();
        self.layout(state);
        self.apply_actions();
        let status = match state.phase.as_str() {
            "loading" => Some("Preparazione in corso. Il microfono non sta ancora registrando."),
            "stopping" => Some("Chiusura registrazione… Acquisisco le ultime parole."),
            "finalizing" => Some("Elaborazione della trascrizione in corso"),
            _ => state.message.as_deref(),
        };
        self.view
            .setToolTip(status.map(NSString::from_str).as_deref());
        self.view.setNeedsDisplay(true);
        self.animate();
        if first {
            let point = NSEvent::mouseLocation();
            let screens = NSScreen::screens(mtm);
            let screen = screens
                .iter()
                .find(|s| {
                    let r = s.frame();
                    point.x >= r.origin.x
                        && point.x < r.origin.x + r.size.width
                        && point.y >= r.origin.y
                        && point.y < r.origin.y + r.size.height
                })
                .or_else(|| NSScreen::mainScreen(mtm));
            if let Some(screen) = screen {
                let r = screen.visibleFrame();
                let width = self.panel.frame().size.width;
                self.panel.setFrameOrigin(NSPoint::new(
                    (r.origin.x + (r.size.width - width) / 2.0).round(),
                    r.origin.y + 20.0,
                ));
            }
            self.panel.orderFrontRegardless();
        }
        self.panel.invalidateShadow();
    }
}
/// Frame of the visible HUD, for panels placed above it. Main thread only.
pub fn frame() -> Option<NSRect> {
    HUD.with(|slot| {
        slot.borrow()
            .as_ref()
            .filter(|hud| hud.panel.isVisible())
            .map(|hud| hud.panel.frame())
    })
}
/// Microphone level only. The panel already redraws on its own timer, so this
/// avoids cloning and re-applying the whole state for every meter step.
pub fn level(app: &AppHandle, revision: u64, level: f32) {
    let _ = app.run_on_main_thread(move || {
        HUD.with(|slot| {
            if let Some(hud) = slot.borrow().as_ref() {
                hud.level(revision, level);
            }
        });
    });
}
pub fn update(app: &AppHandle, state: Snapshot) {
    let handle = app.clone();
    let _ = app.run_on_main_thread(move || {
        let mtm = MainThreadMarker::new().expect("HUD is dispatched on main");
        HUD.with(|slot| {
            let mut slot = slot.borrow_mut();
            if slot.is_none() && visible_phase(&state.phase) {
                *slot = Some(Hud::new(handle, mtm));
            }
            if let Some(hud) = slot.as_ref() {
                hud.update(&state, mtm);
            }
        });
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn closing_indicator_never_returns_to_the_counter_before_it_disappears() {
        assert_eq!(timer_label("recording", 21), "00:21");
        assert_eq!(timer_label("paused", 21), "00:21");
        for (phase, elapsed) in [("stopping", 21), ("finalizing", 22), ("finalizing", 23)] {
            assert!(visible_phase(phase));
            assert_eq!(timer_label(phase, elapsed), "Chiudo…");
        }
        assert!(!visible_phase("complete"));
        assert!(!visible_phase("idle"));
        assert_eq!(timer_label("recording", 0), "00:00");
    }

    #[test]
    fn loading_is_visible_before_audio_capture_starts() {
        assert!(visible_phase("loading"));
        assert!(visible_phase("recording"));
        assert!(visible_phase("finalizing"));
        assert!(visible_phase("stopping"));
        assert!(busy_phase("stopping"));
        assert!(busy_phase("finalizing"));
        assert!(!visible_phase("idle"));
        assert!(!visible_phase("complete"));
    }

    #[test]
    fn errors_are_named_once_and_briefly() {
        assert_eq!(
            error_label(Some(
                "Microfono non disponibile o elaborazione troppo lenta."
            )),
            "Microfono non disponibile"
        );
        assert_eq!(error_label(Some("Decoder failure")), "Dettatura interrotta");
        assert_eq!(error_label(None), "Dettatura interrotta");
    }

    #[test]
    fn option_turns_actions_into_their_alternatives_except_on_error() {
        assert_eq!(
            actions("recording", false, false),
            (Action::Pause, Action::Stop)
        );
        assert_eq!(
            actions("paused", false, false),
            (Action::Resume, Action::Stop)
        );
        assert_eq!(
            actions("recording", false, true),
            (Action::Cancel, Action::StopCopy)
        );
        assert_eq!(
            actions("paused", false, true),
            (Action::Cancel, Action::StopCopy)
        );
        assert_eq!(
            actions("error", true, true),
            (Action::Retry, Action::Dismiss)
        );
        assert_eq!(
            actions("error", false, false),
            (Action::Recents, Action::Clear)
        );
    }

    #[test]
    fn spinner_keeps_moving_without_worker_updates() {
        let mut meter = Meter::default();
        let start = meter.started;
        meter.set_phase("loading", start);
        let first = start + std::time::Duration::from_secs(1);
        let later = first + std::time::Duration::from_millis(100);
        assert_ne!(meter.mark(0, first).0, meter.mark(0, later).0);
        assert!(!meter.recording);
    }

    #[test]
    fn spinner_morphs_into_audio_bars_and_back() {
        let mut meter = Meter::default();
        let start = meter.started;
        meter.set_phase("loading", start);
        let recording = start + std::time::Duration::from_secs(1);
        let spinner = meter.mark(0, recording);
        meter.set_phase("recording", recording);
        assert_eq!(
            meter.mark(0, recording),
            spinner,
            "No jump at capture start"
        );
        assert_eq!(meter.busy_mix(recording), 1.0);
        let halfway = recording + std::time::Duration::from_millis(140);
        assert!((meter.busy_mix(halfway) - 0.5).abs() < 1e-10);
        let settled = recording + std::time::Duration::from_millis(280);
        for i in 0..BARS {
            assert_eq!(
                meter.mark(i, settled),
                (rect(METER_X + i as f64 * BAR_STEP, 20.5, 3.0, 3.0), 1.0)
            );
        }
        meter.levels[BARS / 2] = 1.0;
        assert_eq!(meter.mark(BARS / 2, settled).0.size.height, 25.0);
        meter.set_phase("finalizing", settled);
        assert!(!meter.recording);
        assert_eq!(meter.busy_mix(settled), 0.0);
        assert_eq!(
            meter.busy_mix(settled + std::time::Duration::from_millis(280)),
            1.0
        );
    }

    #[test]
    fn interrupted_transition_is_continuous_and_pause_clears_audio() {
        let mut meter = Meter::default();
        let start = meter.started;
        meter.set_phase("loading", start);
        let halfway = start + std::time::Duration::from_millis(140);
        let before = meter.mark(0, halfway);
        meter.set_phase("recording", halfway);
        assert_eq!(meter.mark(0, halfway), before);
        meter.levels.fill(1.0);
        meter.set_phase("paused", halfway);
        assert!(!meter.busy);
        assert!(!meter.recording);
        assert_eq!(meter.levels, [0.0; BARS]);
    }
}
