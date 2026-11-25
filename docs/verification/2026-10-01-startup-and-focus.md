# Startup readiness and destination focus

The installed app reported "Nessun campo esterno accessibile" for the user's
completed dictation. Native inspection of T3 Code showed its focused composer
was a text entry area. The original dictation remained available in VoiceFlow.

## Reproductions

These checks failed before their corresponding fixes:

- `cargo test --locked -p voiceflow-desktop recording_shortcuts_during_model_loading`
  found that repeated Toggle commands remained queued during model loading.
- `cargo test --locked -p voiceflow-desktop focused_editor_is_found`
  returned no destination when the system-wide AX query was empty even though
  the application-level query could resolve the same editor. This uses injected
  AX responses at the production resolver, not a claim of reproducing macOS timing.
- With `VOICEFLOW_TEST_MODEL` pointing to the local Nemotron model,
  `cargo test --locked -p voiceflow-core decoder_is_primed -- --ignored`
  confirmed that a newly constructed decoder still deferred its silent pre-roll
  and first inference until after microphone capture could begin.

## Changes and checks

Admission reserves loading/finalizing under the snapshot lock before enqueuing
work. Further recording controls during these phases are ignored. The native HUD
receives the reserved state immediately and ignores older revisions. Cancellation
can dismiss a pending start without allowing its queued Toggle to start capture.

Model loading performs real silent inference before publishing readiness. New
decoders prime their context before opening the microphone. The main UI explains
the wait and disables the recording control with visible spinners.

Focus resolution queries the frontmost application before falling back to the
system-wide AX object, requests Electron's editor tree, and retries transient
missing responses. It still requires the destination PID and AX identity to match
before paste. Capture closes before AX waiting. The clipboard and Command+V path
remain unchanged.

The Electron attribute follows the [official accessibility documentation](https://github.com/electron/electron/blob/main/docs/tutorial/accessibility.md).
AX messaging timeouts follow [Apple's API documentation](https://developer.apple.com/documentation/applicationservices/1459345-axuielementsetmessagingtimeout).

Passed:

- All three regression reproductions after their fixes.
- Reservation, duplicate-control, cancellation and transient-focus tests.
- `cargo clippy --workspace --all-targets --locked -- -D warnings`.
- `cargo test --workspace --locked`.
- `pnpm desktop:check` and `pnpm desktop:ui`.
- `node scripts/verify-state-renders.ts`, including loading before/after model
  readiness, disabled start, both spinners and return to recording controls.
- The real-model readiness test in Auto, Italian and English.
- The real-model `short_recordings` integration test on existing synthetic
  fixtures, covering immediate stop, pause and silence resets.

The mocked AX regressions do not prove insertion in a physical native editor.
Native installation/runtime verification is recorded separately below.

## Installed app

- `pnpm desktop:build` completed and verified the saved stable signing identity.
- Exited the previous app after confirming the original transcript was in history.
- Retained the previous bundle in
  `artifacts/installed-backups/2026-10-01T16-18-45-180Z/VoiceFlow.app` and installed
  the signed build at `/Applications/VoiceFlow.app`.
- Strict deep signature verification passed on the installed bundle.
- Launched the installed app and inspected its native window. The model was
  loaded, start was available, the existing 57-session history remained present,
  and the original failed-delivery dictation was still in history.
- `pnpm desktop:verify-runtime /Applications/VoiceFlow.app/Contents/MacOS/voiceflow-desktop`
  confirmed the running process uses the installed executable. Its SHA-256
  matched the newly built bundle. T3 Code was returned to the foreground.
