//! Webview panels over other applications: the live transcript above the HUD and the
//! recent dictations picker. Both are nonactivating panels, like the HUD itself: the
//! user's application stays active while they type in them, so its focused field is
//! still the destination when VoiceFlow pastes. All AppKit objects stay on main.
use crate::platform;
use anyhow::{Context, Result, anyhow};
use objc2::rc::Retained;
use objc2::runtime::{AnyClass, AnyObject};
use objc2::{ClassType, MainThreadOnly, define_class, msg_send};
use objc2_app_kit::{
    NSEvent, NSEventType, NSPanel, NSScreen, NSWindowCollectionBehavior, NSWindowStyleMask,
};
use objc2_foundation::{MainThreadMarker, NSPoint, NSRect, NSSize};
use serde::Deserialize;
use std::{cell::RefCell, collections::HashMap, sync::OnceLock, time::Duration};
use tauri::{AppHandle, Emitter, WebviewUrl, WebviewWindowBuilder};

pub const LIVE: &str = "live";
pub const RECENTS: &str = "recents";
const LIVE_WIDTH: f64 = 460.0;
const RECENTS_WIDTH: f64 = 600.0;
/// Space between the live panel and the HUD below it.
const LIVE_GAP: f64 = 8.0;

define_class!(
    // Replaces the class of the window Tauri created. Same instance layout as NSWindow.
    #[unsafe(super = NSPanel)]
    #[thread_kind = MainThreadOnly]
    struct OverlayPanel;
    impl OverlayPanel {
        #[unsafe(method(canBecomeKeyWindow))]
        fn can_become_key(&self) -> bool { true }
        #[unsafe(method(canBecomeMainWindow))]
        fn can_become_main(&self) -> bool { false }
        // A click in the webview takes the keyboard without activating VoiceFlow.
        #[unsafe(method(sendEvent:))]
        fn send_event(&self, event: &NSEvent) {
            if event.r#type() == NSEventType::LeftMouseDown && !self.isKeyWindow() {
                self.makeKeyWindow();
            }
            unsafe {
                let _: () = msg_send![super(self), sendEvent: event];
            }
        }
    }
);

struct Overlay {
    panel: Retained<NSPanel>,
    /// Bottom edge kept while the content grows: the panel expands upward.
    bottom: Option<f64>,
}
static APP: OnceLock<AppHandle> = OnceLock::new();
thread_local! {
    static OVERLAYS: RefCell<HashMap<&'static str, Overlay>> = RefCell::new(HashMap::new());
}

fn with_overlay<T>(label: &str, f: impl FnOnce(&mut Overlay) -> T) -> Option<T> {
    OVERLAYS.with(|overlays| overlays.borrow_mut().get_mut(label).map(f))
}

/// Create both panels hidden at startup, so showing them costs no webview load.
pub fn create(app: &AppHandle) -> Result<()> {
    MainThreadMarker::new().context("Overlay creati fuori dal thread principale")?;
    let _ = APP.set(app.clone());
    for (label, page, width) in [
        (LIVE, "live.html", LIVE_WIDTH),
        (RECENTS, "recents.html", RECENTS_WIDTH),
    ] {
        let window = WebviewWindowBuilder::new(app, label, WebviewUrl::App(page.into()))
            .title("VoiceFlow")
            .inner_size(width, 120.0)
            .visible(false)
            .focused(false)
            .decorations(false)
            .transparent(true)
            .shadow(false)
            .resizable(false)
            .skip_taskbar(true)
            .always_on_top(true)
            .visible_on_all_workspaces(true)
            .accept_first_mouse(true)
            .build()?;
        let raw = window.ns_window()? as *mut AnyObject;
        let panel = unsafe {
            objc2::ffi::object_setClass(raw, OverlayPanel::class() as *const AnyClass);
            Retained::retain(raw as *mut NSPanel).ok_or_else(|| anyhow!("Pannello mancante"))?
        };
        panel.setStyleMask(panel.styleMask() | NSWindowStyleMask::NonactivatingPanel);
        panel.setFloatingPanel(true);
        panel.setLevel(25);
        panel.setHidesOnDeactivate(false);
        panel.setHasShadow(true);
        // The live panel takes the keyboard only when the user clicks into its text.
        panel.setBecomesKeyOnlyIfNeeded(label == LIVE);
        panel.setCollectionBehavior(
            NSWindowCollectionBehavior::CanJoinAllSpaces
                | NSWindowCollectionBehavior::FullScreenAuxiliary
                | NSWindowCollectionBehavior::Transient,
        );
        OVERLAYS.with(|overlays| {
            overlays.borrow_mut().insert(
                label,
                Overlay {
                    panel,
                    bottom: None,
                },
            )
        });
    }
    Ok(())
}

fn screen_under_mouse(mtm: MainThreadMarker) -> Option<NSRect> {
    let point = NSEvent::mouseLocation();
    let screens = NSScreen::screens(mtm);
    screens
        .iter()
        .find(|s| {
            let r = s.frame();
            point.x >= r.origin.x
                && point.x < r.origin.x + r.size.width
                && point.y >= r.origin.y
                && point.y < r.origin.y + r.size.height
        })
        .or_else(|| NSScreen::mainScreen(mtm))
        .map(|screen| screen.visibleFrame())
}

/// Resize to the content height, keeping the bottom edge: content grows upward.
/// The live panel sits on top of the HUD; the recents panel keeps the bottom it had
/// when it appeared, moving down only if the top would leave the screen.
fn fit(label: &str, height: f64, animate: bool, mtm: MainThreadMarker) {
    let height = height.clamp(1.0, 2000.0).round();
    with_overlay(label, |overlay| {
        let visible = overlay.panel.isVisible();
        let (x, width, bottom, top_limit) = if label == LIVE {
            let Some(hud) = crate::hud::frame() else {
                return;
            };
            let screen = overlay
                .panel
                .screen()
                .map(|s| s.visibleFrame())
                .or_else(|| screen_under_mouse(mtm));
            let top_limit = screen.map_or(f64::MAX, |r| r.origin.y + r.size.height);
            (
                hud.origin.x + (hud.size.width - LIVE_WIDTH) / 2.0,
                LIVE_WIDTH,
                hud.origin.y + hud.size.height + LIVE_GAP,
                top_limit,
            )
        } else {
            let Some(screen) = (if visible {
                overlay.panel.screen().map(|s| s.visibleFrame())
            } else {
                screen_under_mouse(mtm)
            }) else {
                return;
            };
            let bottom = *overlay
                .bottom
                .get_or_insert(screen.origin.y + (screen.size.height - height) / 2.0 + 60.0);
            (
                screen.origin.x + (screen.size.width - RECENTS_WIDTH) / 2.0,
                RECENTS_WIDTH,
                bottom,
                screen.origin.y + screen.size.height - 24.0,
            )
        };
        let bottom = bottom.min(top_limit - height);
        let frame = NSRect::new(NSPoint::new(x.round(), bottom), NSSize::new(width, height));
        if animate && visible {
            crate::hud::animate_frame(&overlay.panel, frame, 0.28);
        } else {
            overlay.panel.setFrame_display(frame, true);
        }
    });
}

fn show(label: &str, key: bool) {
    with_overlay(label, |overlay| {
        if key {
            overlay.panel.makeKeyAndOrderFront(None);
        } else {
            overlay.panel.orderFrontRegardless();
        }
    });
}

fn hide(label: &str) {
    let was_visible = with_overlay(label, |overlay| {
        let visible = overlay.panel.isVisible();
        overlay.panel.orderOut(None);
        overlay.bottom = None;
        visible
    });
    // The page stops sizing itself, or its next measure would show it again.
    if was_visible == Some(true)
        && let Some(app) = APP.get()
    {
        let _ = app.emit_to(label, "overlay-hidden", ());
    }
}

fn is_key(label: &str) -> bool {
    with_overlay(label, |overlay| {
        overlay.panel.isVisible() && overlay.panel.isKeyWindow()
    })
    .unwrap_or(false)
}

/// Hide the live panel with the HUD. Called on main.
pub fn hide_live() {
    hide(LIVE);
}

fn release_on_main() {
    with_overlay(LIVE, |overlay| {
        if overlay.panel.isKeyWindow() {
            // Ordering out returns key status to the active application.
            overlay.panel.orderOut(None);
            overlay.panel.orderFrontRegardless();
        }
    });
    if is_key(RECENTS) {
        hide(RECENTS);
    }
}

/// Give the keyboard back to the active application before keystrokes are sent to it.
/// For worker threads: waits briefly for the main thread to do it.
pub fn release_focus(app: &AppHandle) {
    if MainThreadMarker::new().is_some() {
        return release_on_main();
    }
    let (done, wait) = crossbeam_channel::bounded(1);
    let dispatched = app.run_on_main_thread(move || {
        release_on_main();
        let _ = done.send(());
    });
    if dispatched.is_ok() {
        let _ = wait.recv_timeout(Duration::from_millis(300));
    }
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct FitRequest {
    window: String,
    height: f64,
    #[serde(default)]
    animate: bool,
}

fn label(name: &str) -> Result<&'static str, String> {
    match name {
        LIVE => Ok(LIVE),
        RECENTS => Ok(RECENTS),
        _ => Err("Pannello sconosciuto".into()),
    }
}

/// The page measured its content: size the panel to it and show it.
#[tauri::command]
pub fn overlay_fit(app: AppHandle, request: FitRequest) -> Result<(), String> {
    let label = label(&request.window)?;
    if !request.height.is_finite() {
        return Err("Altezza non valida".into());
    }
    app.run_on_main_thread(move || {
        let mtm = MainThreadMarker::new().expect("dispatched on main");
        // The live panel only exists above a visible HUD.
        if label == LIVE && crate::hud::frame().is_none() {
            hide(LIVE);
            return;
        }
        let was_visible = with_overlay(label, |o| o.panel.isVisible()).unwrap_or(false);
        if !was_visible {
            // Appear from the bottom edge, then grow to the content.
            fit(label, 1.0, false, mtm);
            show(label, label == RECENTS);
        }
        fit(label, request.height, request.animate || !was_visible, mtm);
    })
    .map_err(|e| e.to_string())
}

#[tauri::command]
pub fn overlay_hide(app: AppHandle, window: String) -> Result<(), String> {
    let label = label(&window)?;
    app.run_on_main_thread(move || hide(label))
        .map_err(|e| e.to_string())
}

/// Leave the live panel's text, returning the keyboard to the user's application.
#[tauri::command]
pub fn overlay_release(app: AppHandle) -> Result<(), String> {
    app.run_on_main_thread(release_on_main)
        .map_err(|e| e.to_string())
}

/// ⌃⌥V: open the picker, or close it when it already has the keyboard.
pub fn toggle_recents(app: &AppHandle) {
    let handle = app.clone();
    let _ = app.run_on_main_thread(move || {
        if is_key(RECENTS) {
            hide(RECENTS);
        } else {
            // The page measures itself and calls overlay_fit, which shows the panel.
            let _ = handle.emit_to(RECENTS, "recents-open", ());
        }
    });
}

/// Paste a saved dictation where the user was typing before opening the picker.
#[tauri::command]
pub async fn recents_insert(app: AppHandle, text: String) -> Result<String, String> {
    if text.trim().is_empty() || text.len() > 1_000_000 {
        return Err("Nessun testo da inserire.".into());
    }
    let (hidden, wait) = crossbeam_channel::bounded(1);
    app.run_on_main_thread(move || {
        hide(RECENTS);
        let _ = hidden.send(());
    })
    .map_err(|e| e.to_string())?;
    let _ = wait.recv_timeout(Duration::from_millis(300));
    tauri::async_runtime::spawn_blocking(move || {
        platform::copy_text(&text).map_err(|e| format!("{e:#}"))?;
        let Some(target) = platform::destination_at_stop(|| false) else {
            return Ok("Copiato negli appunti: nessun campo attivo in cui incollare.".into());
        };
        Ok(match platform::paste_text(&target, &text, || false) {
            Ok(()) => "Inserito nel campo attivo".into(),
            Err(error) => format!("Copiato negli appunti. {error:#}"),
        })
    })
    .await
    .map_err(|e| e.to_string())?
}

/// The picker closes when the user clicks elsewhere, like a menu.
pub fn on_blur(app: &AppHandle, window: &str) {
    if window == RECENTS {
        let _ = app.run_on_main_thread(|| hide(RECENTS));
    }
}
