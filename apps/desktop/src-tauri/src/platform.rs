use anyhow::{Result, anyhow, ensure};
use core_foundation::{
    base::{CFEqual, CFGetTypeID, CFRelease, CFTypeRef, TCFType},
    boolean::CFBoolean,
    string::{CFString, CFStringGetTypeID, CFStringRef},
};
use core_graphics::{
    event::{CGEvent, CGEventFlags, CGEventTapLocation},
    event_source::{CGEventSource, CGEventSourceStateID},
};
use objc2_app_kit::{NSApplication, NSWorkspace};
use objc2_av_foundation::{AVAuthorizationStatus, AVCaptureDevice, AVMediaTypeAudio};
use objc2_foundation::MainThreadMarker;
use serde::Serialize;
use std::{
    ffi::c_void,
    time::{Duration, Instant},
};

#[link(name = "ApplicationServices", kind = "framework")]
unsafe extern "C" {
    fn AXIsProcessTrusted() -> bool;
    fn CGEventSourceFlagsState(state: i32) -> u64;
    fn AXUIElementCreateSystemWide() -> CFTypeRef;
    fn AXUIElementCreateApplication(pid: i32) -> CFTypeRef;
    fn AXUIElementSetMessagingTimeout(element: CFTypeRef, timeout: f32) -> i32;
    fn AXUIElementSetAttributeValue(element: CFTypeRef, name: CFStringRef, value: CFTypeRef)
    -> i32;
    fn AXUIElementCopyAttributeValue(
        element: CFTypeRef,
        name: CFStringRef,
        value: *mut CFTypeRef,
    ) -> i32;
    fn AXUIElementGetPid(element: CFTypeRef, pid: *mut i32) -> i32;
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Permissions {
    pub microphone: bool,
    pub microphone_status: String,
    pub accessibility: bool,
}

pub fn permissions() -> Permissions {
    // AVMediaTypeAudio is the documented media identifier for microphone access.
    let status =
        unsafe { AVCaptureDevice::authorizationStatusForMediaType(AVMediaTypeAudio.unwrap()) };
    Permissions {
        microphone: status == AVAuthorizationStatus::Authorized,
        microphone_status: if status == AVAuthorizationStatus::NotDetermined {
            "not-determined"
        } else if status == AVAuthorizationStatus::Authorized {
            "authorized"
        } else {
            "denied"
        }
        .into(),
        accessibility: unsafe { AXIsProcessTrusted() },
    }
}

pub fn request_microphone() {
    let callback = block2::RcBlock::new(|_granted: objc2::runtime::Bool| {});
    unsafe {
        AVCaptureDevice::requestAccessForMediaType_completionHandler(
            AVMediaTypeAudio.unwrap(),
            &callback,
        )
    };
}

/// Owned AX reference. Created, compared and released on the dictation worker.
pub struct FocusTarget {
    element: CFTypeRef,
    pid: i32,
}
impl Drop for FocusTarget {
    fn drop(&mut self) {
        unsafe {
            CFRelease(self.element);
        }
    }
}

impl FocusTarget {
    pub fn pid(&self) -> i32 {
        self.pid
    }
}

pub fn focused_target() -> Option<FocusTarget> {
    if !unsafe { AXIsProcessTrusted() } {
        return None;
    }
    let pid = frontmost_pid()?;
    if pid == std::process::id() as i32 {
        return None;
    }
    resolve_focus(|source| read_focused_target(source, pid))
}

#[derive(Clone, Copy, Debug, PartialEq)]
enum FocusSource {
    Application,
    SystemWide,
}

fn resolve_focus<T>(mut read: impl FnMut(FocusSource) -> Option<T>) -> Option<T> {
    read(FocusSource::Application).or_else(|| read(FocusSource::SystemWide))
}

fn read_focused_target(source: FocusSource, expected_pid: i32) -> Option<FocusTarget> {
    unsafe {
        let root = match source {
            FocusSource::Application => AXUIElementCreateApplication(expected_pid),
            FocusSource::SystemWide => AXUIElementCreateSystemWide(),
        };
        if root.is_null() {
            return None;
        }
        // Bound a stalled application's AX response instead of blocking dictation.
        let _ = AXUIElementSetMessagingTimeout(root, 0.15);
        if source == FocusSource::Application {
            // Electron builds its native editor tree lazily. This is its documented
            // assistive-client API; it does not activate the app or move the caret.
            let name = CFString::new("AXManualAccessibility");
            let mut enabled: CFTypeRef = std::ptr::null();
            let _ = AXUIElementCopyAttributeValue(root, name.as_concrete_TypeRef(), &mut enabled);
            let yes = CFBoolean::true_value();
            if enabled.is_null() || CFEqual(enabled, yes.as_CFTypeRef()) == 0 {
                let _ = AXUIElementSetAttributeValue(
                    root,
                    name.as_concrete_TypeRef(),
                    yes.as_CFTypeRef(),
                );
            }
            if !enabled.is_null() {
                CFRelease(enabled);
            }
        }
        let mut value: *const c_void = std::ptr::null();
        let status = AXUIElementCopyAttributeValue(
            root,
            CFString::new("AXFocusedUIElement").as_concrete_TypeRef(),
            &mut value,
        );
        CFRelease(root);
        if status != 0 || value.is_null() {
            if !value.is_null() {
                CFRelease(value);
            }
            return None;
        }
        let mut pid = 0;
        if AXUIElementGetPid(value, &mut pid) != 0
            || pid != expected_pid
            || frontmost_pid() != Some(expected_pid)
        {
            CFRelease(value);
            return None;
        }
        let _ = AXUIElementSetMessagingTimeout(value, 0.15);
        let attribute = |name: &str| -> Option<String> {
            let mut attribute: CFTypeRef = std::ptr::null();
            if AXUIElementCopyAttributeValue(
                value,
                CFString::new(name).as_concrete_TypeRef(),
                &mut attribute,
            ) != 0
                || attribute.is_null()
            {
                return None;
            }
            if CFGetTypeID(attribute) != CFStringGetTypeID() {
                CFRelease(attribute);
                return None;
            }
            Some(CFString::wrap_under_create_rule(attribute as CFStringRef).to_string())
        };
        let role = attribute("AXRole");
        if !accepts_destination(role.as_deref(), attribute("AXSubrole").as_deref()) {
            CFRelease(value);
            return None;
        }
        Some(FocusTarget {
            element: value,
            pid,
        })
    }
}

pub fn copy_text(text: &str) -> Result<()> {
    arboard::Clipboard::new()?.set_text(text)?;
    Ok(())
}

/// Validate the element captured at stop after finalization. Never send Return.
pub fn paste_text(target: &FocusTarget, text: &str, cancelled: impl Fn() -> bool) -> Result<()> {
    let deadline = Instant::now() + Duration::from_secs(2);
    // Option+Space stops dictation. Let the physical modifiers go before Cmd+V.
    while unsafe { CGEventSourceFlagsState(1) } & 0x001e0000 != 0 {
        ensure!(!cancelled(), "Dettatura annullata");
        ensure!(
            Instant::now() < deadline,
            "Rilascia i tasti modificatori. Il testo è disponibile in VoiceFlow."
        );
        std::thread::sleep(Duration::from_millis(10));
    }
    let current = wait_for_focus(Some(target.pid), &cancelled).ok_or_else(|| {
        anyhow!("Il campo di destinazione non risponde. Il testo resta disponibile in VoiceFlow.")
    })?;
    ensure!(
        current.pid == target.pid && unsafe { CFEqual(current.element, target.element) } != 0,
        "Il campo attivo è cambiato. Il testo resta disponibile in VoiceFlow."
    );
    let source = CGEventSource::new(CGEventSourceStateID::HIDSystemState)
        .map_err(|_| anyhow!("Cannot create keyboard source"))?;
    let down = CGEvent::new_keyboard_event(source.clone(), 9, true)
        .map_err(|_| anyhow!("Cannot create paste event"))?;
    let up = CGEvent::new_keyboard_event(source, 9, false)
        .map_err(|_| anyhow!("Cannot create paste event"))?;
    down.set_flags(CGEventFlags::CGEventFlagCommand);
    up.set_flags(CGEventFlags::CGEventFlagCommand);
    ensure!(!cancelled(), "Dettatura annullata");
    copy_text(text)?;
    ensure!(!cancelled(), "Dettatura annullata");
    down.post(CGEventTapLocation::HID);
    up.post(CGEventTapLocation::HID);
    Ok(())
}

fn accepts_destination(_role: Option<&str>, subrole: Option<&str>) -> bool {
    subrole != Some("AXSecureTextField")
}
/// Main-window controls return focus to the user's previous app. The native HUD
/// is nonactivating and never needs this step.
pub fn hide_if_frontmost(mtm: MainThreadMarker) {
    if is_frontmost() {
        NSApplication::sharedApplication(mtm).hide(None);
    }
}
fn is_frontmost() -> bool {
    frontmost_pid() == Some(std::process::id() as i32)
}
/// The frontmost application when it is not VoiceFlow.
pub fn frontmost_foreign_pid() -> Option<i32> {
    frontmost_pid().filter(|pid| *pid != std::process::id() as i32)
}
/// Name shown to the user for a running application.
pub fn app_name(pid: i32) -> Option<String> {
    objc2_app_kit::NSRunningApplication::runningApplicationWithProcessIdentifier(pid)
        .and_then(|app| app.localizedName())
        .map(|name| name.to_string())
        .filter(|name| !name.trim().is_empty())
}
fn frontmost_pid() -> Option<i32> {
    NSWorkspace::sharedWorkspace()
        .frontmostApplication()
        .map(|app| app.processIdentifier())
}
pub fn destination_at_stop(cancelled: impl Fn() -> bool) -> Option<FocusTarget> {
    let deadline = Instant::now() + Duration::from_millis(600);
    while is_frontmost() && Instant::now() < deadline && !cancelled() {
        std::thread::sleep(Duration::from_millis(10));
    }
    wait_for_focus(frontmost_pid(), cancelled)
}

fn retry_focus<T>(
    mut read: impl FnMut() -> Option<T>,
    mut retry: impl FnMut() -> bool,
) -> Option<T> {
    loop {
        if let Some(target) = read() {
            return Some(target);
        }
        if !retry() {
            return None;
        }
    }
}

fn wait_for_focus(expected_pid: Option<i32>, cancelled: impl Fn() -> bool) -> Option<FocusTarget> {
    let deadline = Instant::now() + Duration::from_millis(600);
    let available =
        || !cancelled() && frontmost_pid() == expected_pid && unsafe { AXIsProcessTrusted() };
    retry_focus(
        || available().then(focused_target).flatten(),
        || {
            if !available() || Instant::now() >= deadline {
                return false;
            }
            std::thread::sleep(Duration::from_millis(20));
            true
        },
    )
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn focused_editor_is_found_when_the_system_wide_ax_query_returns_no_element() {
        let target = resolve_focus(|source| match source {
            FocusSource::Application => Some("same editor"),
            FocusSource::SystemWide => None,
        });
        assert_eq!(target, Some("same editor"));
    }
    #[test]
    fn system_wide_focus_remains_a_fallback_for_native_editors() {
        assert_eq!(
            resolve_focus(|source| (source == FocusSource::SystemWide).then_some("editor")),
            Some("editor")
        );
    }
    #[test]
    fn transient_missing_focus_is_retried_without_replacing_a_valid_target() {
        let mut reads = [None, None, Some("same editor"), Some("different editor")].into_iter();
        assert_eq!(
            retry_focus(|| reads.next().flatten(), || true),
            Some("same editor")
        );
        assert_eq!(reads.next(), Some(Some("different editor")));
        let mut attempts = 0;
        assert_eq!(
            retry_focus::<()>(
                || {
                    attempts += 1;
                    None
                },
                || false
            ),
            None
        );
        assert_eq!(attempts, 1);
    }
    #[test]
    fn editors_with_generic_accessibility_roles_can_receive_paste() {
        for role in [Some("AXWebArea"), Some("AXGroup"), None] {
            assert!(
                accepts_destination(role, None),
                "Focused editor rejected: {role:?}"
            );
        }
    }
    #[test]
    fn password_fields_are_never_destinations() {
        assert!(!accepts_destination(
            Some("AXTextField"),
            Some("AXSecureTextField")
        ));
    }
}
