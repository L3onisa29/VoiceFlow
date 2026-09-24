# Cancellation versus capture failure

User report: Ctrl+Space cancellation left the error “Acquisizione interrotta:
audio incompleto. Il testo parziale è recuperabile.” visible.

## Reproduction

Extracted the existing cancellation state transition, preserving its `had_active`
guard, and exercised the ordering where the audio failure has already removed
`Active` before the shortcut command arrives:

```sh
cargo test -p voiceflow-desktop cancellation_after_capture_failure -j 2
```

Before the fix, this command failed with `left: "error", right: "idle"` and
“Cancellation left the capture error visible”. After the fix it passes.
This establishes the stale-error race. It does not establish which microphone
error or queue overrun originally set the capture failure flag on the user's Mac.

The normal cancellation path in the old packaged app returned “Dettatura
annullata”; the race did not reproduce deterministically through GUI automation.

## Fix

- Cancellation travels through a dedicated bounded, coalescing channel, selected
  before commands/audio. A full command queue cannot reject Ctrl+Space.
- Pending cancellation wins over a capture failure. It clears the transcript,
  error, level and timer, including after the failed capture has been removed.
- Ctrl+Space remains registered through finalization and a recoverable recording
  error. It is released when the session is cancelled, dismissed or completed.
- Starting again after an error reuses the already registered shortcut.
- Finalization and clipboard delivery check for cancellation before posting paste.
  Once paste has already been sent, cancellation cannot undo it.
- The error panel's close action and main-window “Annulla dettatura” dismiss the
  failed session through the same cancellation path.
- Real capture errors remain errors unless cancellation was explicitly requested.

## Automated checks

All 14 Rust tests pass. New tests cover the failure-before-cancel ordering,
full command queue/repeated key presses, finalization without `Active`, repeated
cancellation, and preserving a result that has already been delivered.
Clippy with warnings denied, TypeScript and formatting checks pass.

The corrected packaged app was exercised with actual microphone capture: the
preview contained “Questa frase di prova deve essere annullata”, then returned
to “Pronto quando vuoi”, 00:00 and “Dettatura annullata”, with the text discarded
and no error panel. The synthetic keyboard events from the computer-control tool
do not reliably exercise Carbon global hotkeys; the exact failure ordering is
covered by the deterministic regression test above.
