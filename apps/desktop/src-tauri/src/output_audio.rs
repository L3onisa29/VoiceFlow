use anyhow::{Result, anyhow, ensure};
use core_foundation::{
    base::TCFType,
    string::{CFString, CFStringRef},
};
use crossbeam_channel::{Sender, bounded};
use objc2_core_audio::{
    AudioObjectGetPropertyData, AudioObjectGetPropertyDataSize, AudioObjectPropertyAddress,
    AudioObjectSetPropertyData, kAudioDevicePropertyMute, kAudioDevicePropertyStreams,
    kAudioDevicePropertyTransportType, kAudioDeviceTransportTypeBluetooth,
    kAudioDeviceTransportTypeBluetoothLE, kAudioDeviceTransportTypeBuiltIn,
    kAudioHardwarePropertyDefaultInputDevice, kAudioHardwarePropertyDefaultOutputDevice,
    kAudioHardwarePropertyDevices, kAudioObjectPropertyName, kAudioObjectPropertyScopeGlobal,
    kAudioObjectPropertyScopeInput, kAudioObjectPropertyScopeOutput, kAudioObjectSystemObject,
    kAudioStreamPropertyTerminalType, kAudioStreamTerminalTypeLFESpeaker,
    kAudioStreamTerminalTypeReceiverSpeaker, kAudioStreamTerminalTypeSpeaker,
};

/// Opening a Bluetooth microphone switches headset playback to a low-quality
/// duplex profile. Select the built-in mic for this app without changing the
/// system default. A non-Bluetooth default (including USB mics) is respected.
pub fn capture_input_name() -> Result<Option<String>> {
    let input = CoreAudio::read(
        kAudioObjectSystemObject as u32,
        kAudioHardwarePropertyDefaultInputDevice,
        kAudioObjectPropertyScopeGlobal,
    )?;
    ensure!(input != 0, "Nessun microfono disponibile");
    let transport = CoreAudio::read(
        input,
        kAudioDevicePropertyTransportType,
        kAudioObjectPropertyScopeGlobal,
    )?;
    if ![
        kAudioDeviceTransportTypeBluetooth,
        kAudioDeviceTransportTypeBluetoothLE,
    ]
    .contains(&transport)
    {
        return Ok(None);
    }
    for device in CoreAudio::objects(
        kAudioObjectSystemObject as u32,
        kAudioHardwarePropertyDevices,
        kAudioObjectPropertyScopeGlobal,
    )? {
        if CoreAudio::read(
            device,
            kAudioDevicePropertyTransportType,
            kAudioObjectPropertyScopeGlobal,
        )? == kAudioDeviceTransportTypeBuiltIn
            && !CoreAudio::objects(
                device,
                kAudioDevicePropertyStreams,
                kAudioObjectPropertyScopeInput,
            )?
            .is_empty()
        {
            return CoreAudio::name(device).map(Some);
        }
    }
    anyhow::bail!(
        "Per mantenere la qualità delle cuffie Bluetooth, seleziona un microfono integrato o USB come ingresso audio di macOS."
    );
}
use std::{
    ptr::{NonNull, null},
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

/// Lives exactly as long as the microphone stream. Polling stays off both the
/// CoreAudio callback and the decoder thread, which can spend seconds decoding.
pub struct OutputProtection {
    stop: Sender<()>,
    worker: Option<JoinHandle<Result<()>>>,
}

impl OutputProtection {
    pub fn start(failed: Arc<AtomicBool>) -> Result<Self> {
        let mut guard = SpeakerMute::new(CoreAudio);
        guard.refresh()?; // Silence speakers before the microphone starts.
        let (stop, stopped) = bounded(1);
        let worker = thread::Builder::new()
            .name("voiceflow-output-audio".into())
            .spawn(move || {
                while stopped
                    .recv_timeout(Duration::from_millis(50))
                    .is_err_and(|e| e.is_timeout())
                {
                    if let Err(error) = guard.refresh() {
                        failed.store(true, Ordering::Release);
                        eprintln!("Protezione audio interrotta: {error}");
                        // Keep the existing mute until capture has actually stopped.
                        let _ = stopped.recv();
                        return Err(error);
                    }
                }
                guard.restore()
            })?;
        Ok(Self {
            stop,
            worker: Some(worker),
        })
    }

    pub fn finish(mut self) -> Result<()> {
        self.stop()
    }

    fn stop(&mut self) -> Result<()> {
        let _ = self.stop.try_send(());
        if let Some(worker) = self.worker.take() {
            worker
                .join()
                .map_err(|_| anyhow!("Controllo dell’uscita audio interrotto"))??;
        }
        Ok(())
    }
}

impl Drop for OutputProtection {
    fn drop(&mut self) {
        if let Err(error) = self.stop() {
            eprintln!("Ripristino audio non riuscito: {error}");
        }
    }
}

trait OutputDevice {
    fn speakers(&self) -> Result<Option<u32>>;
    fn muted(&self, device: u32) -> Result<bool>;
    fn set_muted(&self, device: u32, muted: bool) -> Result<()>;
}

struct SpeakerMute<B: OutputDevice> {
    backend: B,
    changed: Option<u32>,
}

impl<B: OutputDevice> SpeakerMute<B> {
    fn new(backend: B) -> Self {
        Self {
            backend,
            changed: None,
        }
    }

    fn refresh(&mut self) -> Result<()> {
        let speakers = self.backend.speakers()?;
        if speakers != self.changed {
            self.restore()?;
        }
        if let Some(device) = speakers
            && !self.backend.muted(device)?
        {
            // Retain ownership even if a write succeeds but its readback fails.
            self.changed = Some(device);
            self.backend.set_muted(device, true)?;
        }
        Ok(())
    }

    fn restore(&mut self) -> Result<()> {
        if let Some(device) = self.changed {
            self.backend.set_muted(device, false)?;
            self.changed = None;
        }
        Ok(())
    }
}

struct CoreAudio;

impl CoreAudio {
    fn name(device: u32) -> Result<String> {
        let mut address = Self::address(kAudioObjectPropertyName, kAudioObjectPropertyScopeGlobal);
        let mut value: CFStringRef = null();
        let mut size = size_of::<CFStringRef>() as u32;
        // SAFETY: this property returns an owned CFStringRef into a correctly
        // sized pointer slot. CoreAudio requires the caller to release it.
        let status = unsafe {
            AudioObjectGetPropertyData(
                device,
                NonNull::from(&mut address),
                0,
                null(),
                NonNull::from(&mut size),
                NonNull::from(&mut value).cast(),
            )
        };
        ensure!(
            status == 0 && !value.is_null(),
            "Nome del microfono non disponibile (CoreAudio {status})"
        );
        let name = unsafe { CFString::wrap_under_create_rule(value) };
        Ok(name.to_string())
    }

    fn address(selector: u32, scope: u32) -> AudioObjectPropertyAddress {
        AudioObjectPropertyAddress {
            mSelector: selector,
            mScope: scope,
            mElement: 0,
        }
    }

    fn read(object: u32, selector: u32, scope: u32) -> Result<u32> {
        let mut address = Self::address(selector, scope);
        let mut value = 0_u32;
        let mut size = size_of::<u32>() as u32;
        // SAFETY: the address, size and UInt32 output are valid for this call.
        let status = unsafe {
            AudioObjectGetPropertyData(
                object,
                NonNull::from(&mut address),
                0,
                null(),
                NonNull::from(&mut size),
                NonNull::from(&mut value).cast(),
            )
        };
        ensure!(
            status == 0 && size == size_of::<u32>() as u32,
            "Impossibile leggere l’uscita audio (CoreAudio {status}, proprietà {selector:#x})"
        );
        Ok(value)
    }

    fn objects(object: u32, selector: u32, scope: u32) -> Result<Vec<u32>> {
        let mut address = Self::address(selector, scope);
        let mut size = 0_u32;
        // SAFETY: both pointers refer to initialized stack values.
        let status = unsafe {
            AudioObjectGetPropertyDataSize(
                object,
                NonNull::from(&mut address),
                0,
                null(),
                NonNull::from(&mut size),
            )
        };
        ensure!(
            status == 0 && size.is_multiple_of(size_of::<u32>() as u32),
            "Impossibile leggere i canali di uscita (CoreAudio {status})"
        );
        let mut streams = vec![0_u32; size as usize / size_of::<u32>()];
        if streams.is_empty() {
            return Ok(streams);
        }
        let capacity = size;
        // SAFETY: streams contains exactly `size` writable bytes; CoreAudio
        // receives that capacity and updates size to the bytes actually written.
        let status = unsafe {
            AudioObjectGetPropertyData(
                object,
                NonNull::from(&mut address),
                0,
                null(),
                NonNull::from(&mut size),
                NonNull::new(streams.as_mut_ptr()).unwrap().cast(),
            )
        };
        ensure!(
            status == 0 && size <= capacity && size.is_multiple_of(size_of::<u32>() as u32),
            "Impossibile leggere i canali di uscita (CoreAudio {status})"
        );
        streams.truncate(size as usize / size_of::<u32>());
        Ok(streams)
    }

    fn speaker_device(device: u32) -> Result<Option<u32>> {
        if device == 0 {
            return Ok(None);
        }
        let transport = Self::read(
            device,
            kAudioDevicePropertyTransportType,
            kAudioObjectPropertyScopeGlobal,
        )?;
        if transport != kAudioDeviceTransportTypeBuiltIn {
            return Ok(None);
        }
        // A wired headphone jack is also built-in. Inspect the terminal, never
        // localized device names, before deciding to mute the output.
        for stream in Self::objects(
            device,
            kAudioDevicePropertyStreams,
            kAudioObjectPropertyScopeOutput,
        )? {
            let terminal = Self::read(
                stream,
                kAudioStreamPropertyTerminalType,
                kAudioObjectPropertyScopeGlobal,
            )?;
            if [
                kAudioStreamTerminalTypeSpeaker,
                kAudioStreamTerminalTypeLFESpeaker,
                kAudioStreamTerminalTypeReceiverSpeaker,
                // Apple's built-in driver can report the IOKit IOAudioTypes.h
                // OUTPUT_SPEAKER value instead of CoreAudio's 'spkr' fourcc.
                0x0301,
            ]
            .contains(&terminal)
            {
                return Ok(Some(device));
            }
        }
        Ok(None)
    }
}

impl OutputDevice for CoreAudio {
    fn speakers(&self) -> Result<Option<u32>> {
        Self::speaker_device(Self::read(
            kAudioObjectSystemObject as u32,
            kAudioHardwarePropertyDefaultOutputDevice,
            kAudioObjectPropertyScopeGlobal,
        )?)
    }

    fn muted(&self, device: u32) -> Result<bool> {
        Ok(Self::read(
            device,
            kAudioDevicePropertyMute,
            kAudioObjectPropertyScopeOutput,
        )? != 0)
    }

    fn set_muted(&self, device: u32, muted: bool) -> Result<()> {
        let mut address = Self::address(kAudioDevicePropertyMute, kAudioObjectPropertyScopeOutput);
        let mut value = u32::from(muted);
        // SAFETY: the mute property takes a UInt32; both pointers remain valid.
        let status = unsafe {
            AudioObjectSetPropertyData(
                device,
                NonNull::from(&mut address),
                0,
                null(),
                size_of::<u32>() as u32,
                NonNull::from(&mut value).cast(),
            )
        };
        ensure!(
            status == 0,
            "Impossibile {} gli altoparlanti (CoreAudio {status})",
            if muted { "silenziare" } else { "ripristinare" }
        );
        // HAL writes can be asynchronous. Do not start capture until the mute
        // is observable, and do not report restoration before it has happened.
        let deadline = Instant::now() + Duration::from_millis(250);
        while self.muted(device)? != muted {
            ensure!(
                Instant::now() < deadline,
                "L’uscita audio non ha applicato il silenziamento richiesto"
            );
            thread::sleep(Duration::from_millis(5));
        }
        Ok(())
    }
}

impl<B: OutputDevice> Drop for SpeakerMute<B> {
    fn drop(&mut self) {
        if let Err(error) = self.restore() {
            eprintln!("Ripristino audio non riuscito: {error}");
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{cell::RefCell, collections::HashMap, rc::Rc};

    #[derive(Default)]
    struct State {
        speakers: Option<u32>,
        muted: HashMap<u32, bool>,
        writes: Vec<(u32, bool)>,
        fail_mute: bool,
    }

    #[derive(Clone, Default)]
    struct Fake(Rc<RefCell<State>>);

    impl OutputDevice for Fake {
        fn speakers(&self) -> Result<Option<u32>> {
            Ok(self.0.borrow().speakers)
        }
        fn muted(&self, device: u32) -> Result<bool> {
            Ok(*self.0.borrow().muted.get(&device).unwrap_or(&false))
        }
        fn set_muted(&self, device: u32, muted: bool) -> Result<()> {
            let mut state = self.0.borrow_mut();
            anyhow::ensure!(!muted || !state.fail_mute, "Cannot mute");
            state.muted.insert(device, muted);
            state.writes.push((device, muted));
            Ok(())
        }
    }

    #[test]
    fn speakers_are_silent_only_while_capture_owns_the_guard() {
        let backend = Fake::default();
        backend.0.borrow_mut().speakers = Some(7);
        for _ in 0..2 {
            // Resume must acquire a fresh guard after pause.
            let mut guard = SpeakerMute::new(backend.clone());
            guard.refresh().unwrap();
            assert!(
                backend.muted(7).unwrap(),
                "Speaker playback can leak into capture"
            );
            guard.refresh().unwrap();
            drop(guard);
            assert!(!backend.muted(7).unwrap());
        }
        assert_eq!(
            backend.0.borrow().writes,
            [(7, true), (7, false), (7, true), (7, false)]
        );
    }

    #[test]
    fn bluetooth_and_headphones_are_never_modified() {
        let backend = Fake::default();
        let mut guard = SpeakerMute::new(backend.clone());
        guard.refresh().unwrap();
        drop(guard);
        assert!(backend.0.borrow().writes.is_empty());
    }

    #[test]
    fn already_muted_speakers_stay_muted() {
        let backend = Fake::default();
        backend.0.borrow_mut().speakers = Some(7);
        backend.0.borrow_mut().muted.insert(7, true);
        let mut guard = SpeakerMute::new(backend.clone());
        guard.refresh().unwrap();
        drop(guard);
        assert!(backend.muted(7).unwrap());
        assert!(backend.0.borrow().writes.is_empty());
    }

    #[test]
    fn route_changes_restore_the_old_device_and_protect_the_new_one() {
        let backend = Fake::default();
        let mut guard = SpeakerMute::new(backend.clone());
        guard.refresh().unwrap(); // Bluetooth.
        backend.0.borrow_mut().speakers = Some(7);
        guard.refresh().unwrap(); // Bluetooth disconnected.
        assert!(backend.muted(7).unwrap());
        backend.0.borrow_mut().speakers = Some(8);
        guard.refresh().unwrap();
        assert!(!backend.muted(7).unwrap());
        assert!(backend.muted(8).unwrap());
        backend.0.borrow_mut().speakers = None;
        guard.refresh().unwrap(); // Headphones connected.
        assert!(!backend.muted(8).unwrap());
        drop(guard);
        assert_eq!(
            backend.0.borrow().writes,
            [(7, true), (7, false), (8, true), (8, false)]
        );
    }

    #[test]
    fn mute_failure_prevents_unprotected_capture() {
        let backend = Fake::default();
        backend.0.borrow_mut().speakers = Some(7);
        backend.0.borrow_mut().fail_mute = true;
        let mut guard = SpeakerMute::new(backend.clone());
        assert!(guard.refresh().is_err());
    }

    #[test]
    fn startup_error_or_cancellation_restores_speakers() {
        let backend = Fake::default();
        backend.0.borrow_mut().speakers = Some(7);
        let failed_start = || -> Result<()> {
            let mut guard = SpeakerMute::new(backend.clone());
            guard.refresh()?;
            anyhow::bail!("Microphone failed to start");
        };
        assert!(failed_start().is_err());
        assert!(!backend.muted(7).unwrap());
        assert_eq!(backend.0.borrow().writes, [(7, true), (7, false)]);
    }

    #[test]
    #[ignore = "Temporarily mutes the current Mac speakers; run explicitly on hardware"]
    fn real_default_output_is_restored_after_stop_and_drop() {
        let backend = CoreAudio;
        let device = CoreAudio::read(
            kAudioObjectSystemObject as u32,
            kAudioHardwarePropertyDefaultOutputDevice,
            kAudioObjectPropertyScopeGlobal,
        )
        .unwrap();
        let speakers = backend.speakers().unwrap();
        let before = backend.muted(device).ok();
        eprintln!("CoreAudio default={device}, speakers={speakers:?}, initially_muted={before:?}");
        for finish_explicitly in [true, false] {
            let failed = Arc::new(AtomicBool::new(false));
            let protection = OutputProtection::start(Arc::clone(&failed)).unwrap();
            if speakers.is_some() {
                assert!(backend.muted(device).unwrap());
            } else {
                assert_eq!(backend.muted(device).ok(), before);
            }
            thread::sleep(Duration::from_millis(120));
            assert!(!failed.load(Ordering::Acquire));
            if finish_explicitly {
                protection.finish().unwrap();
            } else {
                drop(protection);
            }
            assert_eq!(backend.muted(device).ok(), before);
        }
    }

    #[test]
    #[ignore = "Temporarily mutes built-in speakers without changing the default output"]
    fn real_built_in_speakers_mute_and_restore() {
        struct FixedSpeakers(u32);
        impl OutputDevice for FixedSpeakers {
            fn speakers(&self) -> Result<Option<u32>> {
                CoreAudio::speaker_device(self.0)
            }
            fn muted(&self, device: u32) -> Result<bool> {
                CoreAudio.muted(device)
            }
            fn set_muted(&self, device: u32, muted: bool) -> Result<()> {
                CoreAudio.set_muted(device, muted)
            }
        }
        let devices = CoreAudio::objects(
            kAudioObjectSystemObject as u32,
            objc2_core_audio::kAudioHardwarePropertyDevices,
            kAudioObjectPropertyScopeGlobal,
        )
        .unwrap();
        let device = devices
            .into_iter()
            .find(|&d| CoreAudio::speaker_device(d).ok().flatten().is_some())
            .expect("No built-in speakers found");
        let before = CoreAudio.muted(device).unwrap();
        eprintln!("CoreAudio built-in speakers={device}, initially_muted={before}");
        for _ in 0..2 {
            let mut guard = SpeakerMute::new(FixedSpeakers(device));
            guard.refresh().unwrap();
            assert!(CoreAudio.muted(device).unwrap());
            drop(guard);
            assert_eq!(CoreAudio.muted(device).unwrap(), before);
        }
    }
}
