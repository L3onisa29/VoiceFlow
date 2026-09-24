//! A genuine nonactivating NSPanel: clicking Pause/Finish must leave the editor's
//! caret and application focus untouched. All AppKit objects stay on main.
use crate::service::{Command, Service, Snapshot};
use objc2::rc::Retained;
use objc2::runtime::AnyObject;
use objc2::{DefinedClass, MainThreadOnly, define_class, msg_send, sel};
use objc2_app_kit::*;
use objc2_foundation::{MainThreadMarker, NSPoint, NSRect, NSSize, NSString};
use std::cell::RefCell;
use tauri::{AppHandle, Manager};

const WIDTH: f64 = 208.0;
const HEIGHT: f64 = 44.0;
fn rect(x: f64, y: f64, w: f64, h: f64) -> NSRect {
    NSRect::new(NSPoint::new(x, y), NSSize::new(w, h))
}
fn orange() -> Retained<NSColor> {
    NSColor::colorWithSRGBRed_green_blue_alpha(1.0, 0.54, 0.24, 1.0)
}
#[derive(Default)]
struct Meter {
    levels: [f64; 9],
    recording: bool,
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
            orange().setFill();
            let meter = self.ivars().meter.borrow();
            for (i, level) in meter.levels.iter().enumerate() {
                let height = if meter.recording { 3.0 + level * 23.0 } else { 3.0 };
                NSBezierPath::bezierPathWithRoundedRect_xRadius_yRadius(rect(75.0+i as f64*5.0,(HEIGHT-height)/2.0,3.0,height),1.5,1.5).fill();
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
        }
    }
    fn update(&self, state: &Snapshot, mtm: MainThreadMarker) {
        if !matches!(
            state.phase.as_str(),
            "recording" | "paused" | "finalizing" | "error"
        ) {
            self.panel.orderOut(None);
            return;
        }
        let recording = state.phase == "recording";
        let error = state.phase == "error";
        *self.view.ivars().phase.borrow_mut() = state.phase.clone();
        {
            let mut meter = self.view.ivars().meter.borrow_mut();
            if recording {
                meter.levels.rotate_left(1);
                meter.levels[8] = f64::from(state.level * 14.0).clamp(0.0, 1.0);
            } else {
                meter.levels.fill(0.0);
            }
            meter.recording = recording;
        }
        self.timer.setStringValue(&NSString::from_str(&if error {
            "Errore".into()
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
        self.pause.setEnabled(state.phase != "finalizing");
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
        } else if state.phase == "finalizing" {
            "…"
        } else {
            "✓"
        }));
        self.finish.setEnabled(state.phase != "finalizing");
        self.finish
            .setAccessibilityLabel(Some(&NSString::from_str(if error {
                "Chiudi indicatore"
            } else {
                "Termina e incolla"
            })));
        self.view.setToolTip(
            state
                .message
                .as_ref()
                .map(|s| NSString::from_str(s))
                .as_deref(),
        );
        self.view.setNeedsDisplay(true);
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
            hud.panel.orderOut(None);
        }
    });
}
pub fn update(app: &AppHandle, state: Snapshot) {
    let handle = app.clone();
    let _ = app.run_on_main_thread(move || {
        let mtm = MainThreadMarker::new().expect("HUD is dispatched on main");
        HUD.with(|slot| {
            let mut slot = slot.borrow_mut();
            if slot.is_none() && matches!(state.phase.as_str(), "recording" | "error") {
                *slot = Some(Hud::new(handle, mtm));
            }
            if let Some(hud) = slot.as_ref() {
                hud.update(&state, mtm);
            }
        });
    });
}
