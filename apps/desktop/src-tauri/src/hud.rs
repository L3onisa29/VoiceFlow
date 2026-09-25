//! A genuine nonactivating NSPanel: clicking Pause/Finish must leave the editor's
//! caret and application focus untouched. All AppKit objects stay on main.
use crate::service::{Command, Service, Snapshot};
use objc2::rc::Retained;
use objc2::runtime::AnyObject;
use objc2::{DefinedClass, MainThreadOnly, define_class, msg_send, sel};
use objc2_app_kit::*;
use objc2_foundation::{MainThreadMarker, NSPoint, NSRect, NSSize, NSString, NSTimer};
use std::{cell::RefCell, time::Instant};
use tauri::{AppHandle, Manager};

const WIDTH: f64 = 208.0;
const HEIGHT: f64 = 44.0;
fn visible_phase(phase: &str) -> bool {
    matches!(
        phase,
        "loading" | "recording" | "paused" | "finalizing" | "error"
    )
}
fn busy_phase(phase: &str) -> bool {
    matches!(phase, "loading" | "finalizing")
}
fn rect(x: f64, y: f64, w: f64, h: f64) -> NSRect {
    NSRect::new(NSPoint::new(x, y), NSSize::new(w, h))
}
fn orange() -> Retained<NSColor> {
    NSColor::colorWithSRGBRed_green_blue_alpha(1.0, 0.54, 0.24, 1.0)
}
struct Meter {
    levels: [f64; 9],
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
            levels: [0.0; 9],
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
    fn mark(&self, index: usize, now: Instant) -> (NSRect, f64) {
        let mix = self.busy_mix(now);
        let angle = index as f64 * std::f64::consts::TAU / 9.0
            - now.duration_since(self.started).as_secs_f64() * std::f64::consts::TAU;
        let x = 75.0 + index as f64 * 5.0;
        let height = 3.0
            + if self.recording {
                self.levels[index] * 23.0 * (1.0 - mix)
            } else {
                0.0
            };
        let x = x + (95.0 + angle.cos() * 9.0 - x) * mix;
        let y = (HEIGHT - height) / 2.0 + angle.sin() * 9.0 * mix;
        (
            rect(x, y, 3.0, height),
            1.0 - mix * (1.0 - index as f64 / 8.0) * 0.75,
        )
    }
}
struct ViewIvars {
    app: AppHandle,
    meter: RefCell<Meter>,
    phase: RefCell<String>,
}
define_class!(
    #[unsafe(super = NSView)]
    #[thread_kind = MainThreadOnly]
    #[ivars = ViewIvars]
    struct HudView;
    impl HudView {
        #[unsafe(method(drawRect:))]
        fn draw(&self, _dirty: NSRect) {
            NSColor::colorWithSRGBRed_green_blue_alpha(0.055,0.055,0.055,0.97).setFill();
            NSBezierPath::bezierPathWithRoundedRect_xRadius_yRadius(self.bounds(), 22.0,22.0).fill();
            let meter = self.ivars().meter.borrow();
            let now = Instant::now();
            for i in 0..9 {
                let (frame, alpha) = meter.mark(i, now);
                NSColor::colorWithSRGBRed_green_blue_alpha(1.0, 0.54, 0.24, alpha).setFill();
                NSBezierPath::bezierPathWithRoundedRect_xRadius_yRadius(frame,1.5,1.5).fill();
            }
        }
        #[unsafe(method(pauseClicked:))]
        fn pause_clicked(&self, _sender: &AnyObject) {
            if self.ivars().phase.borrow().as_str() == "error" { crate::show(&self.ivars().app); hide(); }
            else { let _ = self.ivars().app.state::<Service>().send(Command::Pause); }
        }
        #[unsafe(method(finishClicked:))]
        fn finish_clicked(&self, _sender: &AnyObject) {
            if self.ivars().phase.borrow().as_str() == "error" { let _ = self.ivars().app.state::<Service>().send(Command::Cancel); }
            else { let _ = self.ivars().app.state::<Service>().send(Command::Stop); }
        }
    }
);
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
    panel: Retained<RecordingPanel>,
    view: Retained<HudView>,
    timer: Retained<NSTextField>,
    pause: Retained<NSButton>,
    finish: Retained<NSButton>,
    animation: RefCell<Option<Retained<NSTimer>>>,
}
thread_local! { static HUD: RefCell<Option<Hud>> = const { RefCell::new(None) }; }
impl Hud {
    fn new(app: AppHandle, mtm: MainThreadMarker) -> Self {
        let allocated = RecordingPanel::alloc(mtm);
        let panel: Retained<RecordingPanel> = unsafe {
            msg_send![allocated,
                initWithContentRect: rect(0.0,0.0,WIDTH,HEIGHT),
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
        });
        let view: Retained<HudView> =
            unsafe { msg_send![super(allocated), initWithFrame: rect(0.0,0.0,WIDTH,HEIGHT)] };
        let timer = NSTextField::labelWithString(&NSString::from_str("00:00"), mtm);
        timer.setFrame(rect(14.0, 14.0, 57.0, 17.0));
        timer.setFont(Some(&NSFont::monospacedDigitSystemFontOfSize_weight(
            12.0, 0.3,
        )));
        timer.setTextColor(Some(&NSColor::whiteColor()));
        let pause = unsafe {
            NSButton::buttonWithTitle_target_action(
                &NSString::from_str("Ⅱ"),
                Some(&view),
                Some(sel!(pauseClicked:)),
                mtm,
            )
        };
        pause.setFrame(rect(132.0, 7.0, 32.0, 30.0));
        pause.setBordered(false);
        pause.setFont(Some(&NSFont::systemFontOfSize(17.0)));
        pause.setContentTintColor(Some(&orange()));
        let finish = unsafe {
            NSButton::buttonWithTitle_target_action(
                &NSString::from_str("✓"),
                Some(&view),
                Some(sel!(finishClicked:)),
                mtm,
            )
        };
        finish.setFrame(rect(168.0, 7.0, 28.0, 30.0));
        finish.setBordered(false);
        finish.setFont(Some(&NSFont::systemFontOfSize(16.0)));
        finish.setContentTintColor(Some(&NSColor::whiteColor()));
        finish.setToolTip(Some(&NSString::from_str("Termina e incolla · ⌥ Spazio")));
        view.addSubview(&timer);
        view.addSubview(&pause);
        view.addSubview(&finish);
        panel.setContentView(Some(&view));
        Self {
            panel,
            view,
            timer,
            pause,
            finish,
            animation: RefCell::default(),
        }
    }
    fn hide(&self) {
        if let Some(timer) = self.animation.borrow_mut().take() {
            timer.invalidate();
        }
        self.panel.orderOut(None);
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
                    hud.view.setNeedsDisplay(true);
                }
            });
        });
        let timer = unsafe {
            NSTimer::scheduledTimerWithTimeInterval_repeats_block(1.0 / 60.0, true, &block)
        };
        *self.animation.borrow_mut() = Some(timer);
    }
    fn update(&self, state: &Snapshot, mtm: MainThreadMarker) {
        if !visible_phase(&state.phase) {
            self.hide();
            return;
        }
        let recording = state.phase == "recording";
        let error = state.phase == "error";
        let busy = busy_phase(&state.phase);
        *self.view.ivars().phase.borrow_mut() = state.phase.clone();
        {
            let mut meter = self.view.ivars().meter.borrow_mut();
            meter.set_phase(&state.phase, Instant::now());
            if recording {
                meter.levels.rotate_left(1);
                meter.levels[8] = f64::from(state.level * 14.0).clamp(0.0, 1.0);
            } else {
                meter.levels.fill(0.0);
            }
        }
        self.timer.setStringValue(&NSString::from_str(&if error {
            "Errore".into()
        } else if state.phase == "loading" {
            "Avvio…".into()
        } else {
            format!(
                "{:02}:{:02}",
                state.elapsed_seconds / 60,
                state.elapsed_seconds % 60
            )
        }));
        self.pause.setTitle(&NSString::from_str(if error {
            "!"
        } else if recording {
            "Ⅱ"
        } else {
            "▶"
        }));
        self.pause.setEnabled(!busy);
        self.pause
            .setAccessibilityLabel(Some(&NSString::from_str(if error {
                "Recupera testo"
            } else if recording {
                "Pausa"
            } else {
                "Riprendi"
            })));
        self.pause.setToolTip(Some(&NSString::from_str(if error {
            "Apri VoiceFlow per recuperare il testo"
        } else if recording {
            "Pausa"
        } else {
            "Riprendi"
        })));
        self.finish.setTitle(&NSString::from_str(if error {
            "×"
        } else if busy {
            "…"
        } else {
            "✓"
        }));
        self.finish.setEnabled(!busy);
        self.finish
            .setAccessibilityLabel(Some(&NSString::from_str(if error {
                "Chiudi indicatore"
            } else {
                "Termina e incolla"
            })));
        let status = if state.phase == "loading" {
            Some("Preparazione in corso. Il microfono non sta ancora registrando.")
        } else if state.phase == "finalizing" {
            Some("Elaborazione della trascrizione in corso")
        } else {
            state.message.as_deref()
        };
        self.view
            .setToolTip(status.map(NSString::from_str).as_deref());
        self.view.setNeedsDisplay(true);
        self.animate();
        if !self.panel.isVisible() {
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
                self.panel.setFrameOrigin(NSPoint::new(
                    r.origin.x + (r.size.width - WIDTH) / 2.0,
                    r.origin.y + 20.0,
                ));
            }
            self.panel.orderFrontRegardless();
        }
    }
}
fn hide() {
    HUD.with(|slot| {
        if let Some(hud) = slot.borrow().as_ref() {
            hud.hide();
        }
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
    fn loading_is_visible_before_audio_capture_starts() {
        assert!(visible_phase("loading"));
        assert!(visible_phase("recording"));
        assert!(visible_phase("finalizing"));
        assert!(!visible_phase("idle"));
        assert!(!visible_phase("complete"));
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
    fn spinner_morphs_into_audio_dots_and_back() {
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
        for i in 0..9 {
            assert_eq!(
                meter.mark(i, settled),
                (rect(75.0 + i as f64 * 5.0, 20.5, 3.0, 3.0), 1.0)
            );
        }
        meter.levels[4] = 1.0;
        assert_eq!(meter.mark(4, settled).0.size.height, 26.0);
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
        assert_eq!(meter.levels, [0.0; 9]);
    }
}
