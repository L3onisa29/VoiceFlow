use anyhow::{Result, anyhow, ensure};
use core_foundation::{
    base::{CFEqual, CFGetTypeID, CFRelease, CFTypeRef, TCFType},
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

pub fn focused_target() -> Option<FocusTarget> {
    if !unsafe { AXIsProcessTrusted() } {
        return None;
    }
    unsafe {
        let root = AXUIElementCreateSystemWide();
        if root.is_null() {
            return None;
        }
        let mut value: *const c_void = std::ptr::null();
        let status = AXUIElementCopyAttributeValue(
            root,
            CFString::new("AXFocusedUIElement").as_concrete_TypeRef(),
            &mut value,
        );
        CFRelease(root);
        if status != 0 || value.is_null() {
            return None;
        }
        let mut pid = 0;
        if AXUIElementGetPid(value, &mut pid) != 0 || pid == std::process::id() as i32 {
            CFRelease(value);
            return None;
        }
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
pub fn paste_text(target: &FocusTarget, text: &str) -> Result<()> {
    let deadline = Instant::now() + Duration::from_secs(2);
    // Option+Space stops dictation. Let the physical modifiers go before Cmd+V.
    while unsafe { CGEventSourceFlagsState(1) } & 0x001e0000 != 0 {
        ensure!(
            Instant::now() < deadline,
            "Rilascia i tasti modificatori. Il testo è disponibile in VoiceFlow."
        );
        std::thread::sleep(Duration::from_millis(10));
    }
    let current = focused_target().ok_or_else(|| anyhow!("Nessun campo di testo attivo"))?;
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
    copy_text(text)?;
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
    NSWorkspace::sharedWorkspace()
        .frontmostApplication()
        .is_some_and(|app| app.processIdentifier() == std::process::id() as i32)
}
pub fn destination_at_stop() -> Option<FocusTarget> {
    let deadline = Instant::now() + Duration::from_millis(600);
    while is_frontmost() && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(10));
    }
    focused_target()
}
#[cfg(test)]
mod tests {
    use super::*;
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
