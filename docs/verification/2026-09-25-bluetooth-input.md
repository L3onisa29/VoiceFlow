# Bluetooth playback and dictation, 2026-09-25

The system selected `Cuffioni` as both input and output. Before the fix,
starting capture in the packaged app changed headphone output from two channels
at 44,100 Hz to one channel at 16,000 Hz. Pausing released the headset microphone.
This matches Apple's documented Bluetooth microphone/playback mode change:
https://support.apple.com/en-us/102217.

VoiceFlow now resolves the built-in microphone through CoreAudio when the system
input uses Bluetooth. CPAL opens that device explicitly. Other input defaults
and the system's routing remain unchanged.

The hardware regression command failed before the change, selecting `Cuffioni`,
and passed afterwards:

```sh
cargo test -p voiceflow-desktop bluetooth_default_does_not_select_the_headset_microphone --locked -- --ignored --nocapture
```

The signed app was rebuilt and relaunched. A local speech fixture played through
the default Bluetooth headphones while VoiceFlow recorded. `system_profiler
SPAudioDataType` confirmed that output remained two channels at 44,100 Hz during
capture. A second synthetic phrase played specifically through the Mac speakers
and entered the built-in microphone. Live preview appeared before pausing;
the paused transcript contained:

> Il microfono del computer funziona mentre ascolto il video nelle cuffie. Questa frase deve comparire nella trascrizione

Resuming also retained two-channel, 44,100 Hz headphone output. The test recording
was cancelled without pasting it into another app. The updated app was left idle.
Clippy, workspace tests and the signed release build passed for the version
tested here. Formatting passed for the changed audio files. A later workspace
format check found separate edits in progress in settings, service and streaming;
those edits were not part of this hardware verification.

This verifies actual microphone transcription with concurrent headphone audio,
using synthetic speech on this Mac and headset. It does not measure subjective
sound quality or other Bluetooth hardware. Dictation now requires speaking near
the Mac; another app opening the headset microphone can still change its profile.
