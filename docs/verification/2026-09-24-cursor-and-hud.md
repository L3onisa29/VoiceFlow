# Cursor insertion and floating controls

Scope: Rust desktop app, standard macOS paste in T3 Code and other editors.
The T3-specific reservation bridge is deliberately excluded following the
user's latest direction. No T3 installation or legacy Python integration was changed.

## Regression checks

- The prior role filter rejected AXWebArea, AXGroup and a focused element without
  a reported role. The new regression test failed before the fix and passes after.
- Secure text fields remain excluded.
- An explicit pause flushes a sub-chunk tail once; a repeated flush adds no words,
  resume preserves the first phrase, and finish preserves the resumed phrase.
- The elapsed timer freezes during pause, tolerates repeated pause/resume calls,
  and continues beyond 120 seconds.
- Stop captures the current external field. Delivery rechecks its identity after
  finalization and waits for held keyboard modifiers before posting Command+V.
- Delivery errors preserve the final transcript and produce the error state.

## Desktop acceptance checks

1. Open the packaged VoiceFlow app and grant its Microphone and Accessibility
   permissions in macOS if needed.
2. Put the caret between two words in a **disposable unsent** T3 Code draft.
3. Press Option+Space. Confirm T3 remains active and the floating panel appears.
4. Dictate, click Pause, wait, then Resume. Confirm the timer freezes in pause,
   the microphone stops, and the editor's caret stays in place.
5. Click Finish or press Option+Space. Confirm the text appears at the caret,
   existing surrounding text remains, and the draft is not sent.
6. Repeat with another editor. Change fields while speaking to confirm the field
   selected at stop receives the text.
7. Switch fields during finalization. Confirm an error instead of a paste into
   the unexpected destination, and recover the text with Copy text.
8. Stop while paused; cancel with Control+Space; repeat a new recording.

## Results

- `cargo test --workspace --locked`: 11 tests passed.
- `cargo clippy --workspace --all-targets --locked -- -D warnings`: passed.
- `pnpm desktop:check`: passed.
- `pnpm test:streaming`: Italian and English real-model checks passed after the
  decoder changes. First partial wall latency: 0.695 s / 0.724 s; inference:
  2.822 s / 2.544 s; final flush: 338 ms / 323 ms. Synthetic audio, no microphone.
- `pnpm desktop:build`: passed; packaged app signed ad-hoc.
- `codesign --verify --deep --strict`: passed.
- Opened the packaged app with native computer control. NVIDIA loaded and the
  revised UI was visible. T3 Code's installed composer is accessible to the
  inspection tool; no T3 bridge calls were made.
- The previous bundle lacked Accessibility. The rebuilt bundle reports both
  Microphone and Accessibility ungranted. A permission request is pending with
  the user. No system permission was changed by the agent.
- Therefore **recording HUD interaction, real pause/resume, global shortcut and
  actual insertion at the T3 caret are not yet verified**. Synthetic/unit tests
  and the successful build do not establish those desktop outcomes.
- A synthetic Option+Space sent by the UI tool did not activate dictation; it
  inserted a nonbreaking space, which was removed immediately. Verify the
  physical shortcut after permissions are enabled. No message was submitted.

