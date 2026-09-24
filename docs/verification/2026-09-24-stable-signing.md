# Stable signing and permissions, 2026-09-24

The previous ad-hoc bundle had a designated requirement tied to its executable
hash. Recompilation changed that requirement and left an apparently enabled but
untrusted Accessibility entry in macOS.

The desktop build now pins the personal Apple Development certificate already
installed in Keychain, outside Git. It verifies the bundle and requires exactly
the same designated requirement on subsequent builds. It never falls back to
ad-hoc signing. The unrelated client certificate was not used.

## Two-build update check on this Mac

1. Built the first signed bundle, using the personal certificate explicitly.
2. Verified Microphone authorization in VoiceFlow. Replaced the stale VoiceFlow
   Accessibility entry with the current signed bundle using System Settings.
   Both permission callouts disappeared; the model was ready.
3. Quit VoiceFlow completely. Process inspection confirmed it was not running.
4. Changed the microphone callout wording to alter the embedded UI and executable.
5. Built again using only `CARGO_BUILD_JOBS=2 pnpm desktop:build`, with no signing
   environment override. The saved certificate was selected automatically.
6. Verified different executable hashes, identical designated requirements and
   team identifiers, and a successful strict signature check.
7. Launched the second bundle as a new process. Both permissions remained
   recognized without a new prompt or any intervening Settings changes.

| Check | First build | Second build |
| --- | --- | --- |
| CDHash | `b5a5ea377e1aefa969ddbc64602003a6838b7901` | `00b15e8fc954092d15e1f854eec190516d5bd6cc` |
| Signature | Apple Development | Same certificate and requirement |
| Microphone | Authorized | Still authorized |
| Accessibility | Trusted after initial migration | Still trusted |

The UI reads AVFoundation microphone authorization and `AXIsProcessTrusted()`.
After the update, real microphone capture transcribed synthetic speech played
through the speakers: `I permessi restano validi anche dopo un aggiornamento`.
No permission prompt appeared during this recording.

## Validation and limits

- Three signing regression tests passed. They reject ad-hoc requirements, missing
  or changed signers, and changes to the designated requirement.
- `pnpm desktop:check`, formatting and diff checks passed.
- Both packaged builds passed `codesign --verify --deep --strict`.
- A direct build preflight without the wrapper's signing environment failed as
  intended.
- T3 cursor insertion was not confirmed in this second-build run. VoiceFlow
  reported sending paste, but the observed draft did not contain the transcript.
  The computer-use tool's own paste attempts also timed out. This does not prove
  why insertion failed. The synthetic draft was removed and its empty state
  verified; no message was submitted. Earlier successful cursor insertion is
  recorded separately in `2026-09-24-cursor-and-hud.md`.

This proves permission retention across these two local updates at the same
bundle path. It does not cover certificate renewal, another Mac, Developer ID
distribution or notarization. The current certificate expires on 2027-09-16.
