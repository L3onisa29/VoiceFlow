# VoiceFlow desktop

## Current application

TanStack Router owns navigation in the React interface. `src/router.ts` assembles
the typed route tree from `src/routes/__root.tsx` and `src/routes/index.tsx`.
The `/` route renders dictation. The root provides the outlet and a recovery
link for unknown routes. Hash history keeps navigation within the bundled
WebView document, without requiring server rewrites. See the
[TanStack history documentation](https://tanstack.com/router/latest/docs/guide/history-types).

The Tauri `.app` embeds React assets. CPAL captures the selected microphone; Rust
resamples audio to mono 16 kHz and feeds the NVIDIA decoder every 560 ms.
Encoder caches persist between chunks. Tauri events update the preview while
recording. Stopping drains queued audio and flushes decoder lookahead.

Before the first audio chunk of each utterance, the NVIDIA adapter feeds two
silent chunks to initialize encoder context, or four in automatic language mode.
Without this pre-roll, immediate
short words can disappear even after a complete stop flush. This also applies
after pause and silence resets. It adds decoder work but no recording delay or
recorded duration. `pnpm test:short-recordings` checks synthetic speech lasting
0.27–1.01 seconds against the real local model, including pause/resume and silence
boundaries in Italian and automatic language mode.
The isolated 273 ms "Ciao" fixture still fails in automatic mode, including with
the model's offline API; its regression assertion uses Italian. "Pronto" in Auto
works on immediate stop but remains unreliable after a silence reset. Automatic
mode reset assertions cover the two longer phrases. Short-word recognition is
not guaranteed by the model, and this fix does not force a language or replay audio.

There is no recording duration limit or 30-second transcription batch. The audio queue
retains outstanding frames when inference falls behind, releasing each frame after
processing. Stop closes capture, drains this finite backlog, flushes the resampler
and decoder tail, then saves the concatenated transcript once. Previously decoded
audio is not processed again. Memory usage grows with the backlog if inference
stays slower than capture, and with transcript length. A microphone error stops
dictation with a visible error and preserves recognized text.

Two seconds of low audio energy commits the phrase and resets the decoder
without ending dictation. This initial endpoint detector uses an RMS threshold,
not a trained VAD. Soft speech and noise need real-microphone evaluation.

The model loads once per process. Startup also runs silent inference before
reporting the model ready. Each new decoder primes its encoder context before
the microphone opens. During preparation the native indicator keeps animating
and the main window explains that recording has not started. Recording controls
are reserved immediately when submitted; repeated shortcuts during loading or
finalization are ignored rather than queued for later playback.

ONNX Runtime runs on CPU with four intra-op
threads. No NVIDIA GPU, CUDA, Python process or API key is needed.

### Interaction and storage

- Grant Microphone access to VoiceFlow, and Accessibility for automatic paste.
- If macOS selects a Bluetooth microphone, VoiceFlow uses the Mac's built-in
  microphone instead. This keeps headphone playback out of the reduced-quality
  Bluetooth duplex mode. Speak toward the Mac while listening through the
  headphones. Other input defaults, including USB microphones, are respected.
  VoiceFlow does not change the system input or output. If no built-in mic is
  available, select a non-Bluetooth input in macOS before starting dictation.
- `Option+Space` starts and stops dictation from another app.
- `Control+Space` cancels during recording and is released afterwards.
- A black/orange native nonactivating NSPanel shows elapsed time on the left,
  nine narrow audio bars, Pause/Resume and Finish controls on the right. It
  appears above other apps, including full-screen spaces, without taking focus.
- Pause releases the microphone, drains and commits the decoder tail, and freezes
  the timer. Resume starts a fresh capture/resampler while retaining the text.
  Finish works while paused. There is no pause timeout or recording duration cap.
- While capturing, VoiceFlow mutes the built-in speakers when they are the
  default output, so playback does not enter the microphone. Bluetooth and
  wired headphones are left alone. CoreAudio transport and stream terminal
  types identify the output, independently of its name or system language.
  Output changes are checked every 50 ms while recording. Pause, finish, cancel,
  capture errors and normal app exit restore the mute state before decoding
  the remaining audio. Speakers that were already muted stay muted. Playback
  continues silently; media players are not paused and volume is not changed.
  External speakers and apps routed to an output other than the system default
  are not silenced. A forced process termination cannot run the restoration.
- The app captures the focused element at **stop**, then checks its PID and AX
  identity again after finalization. Web editors exposed as AXWebArea or AXGroup
  are accepted. Secure text fields and VoiceFlow itself are excluded.
- Focus is read from the frontmost application's Accessibility object, with the
  system-wide object as a fallback. Electron's documented `AXManualAccessibility`
  attribute exposes its native editor tree before capture begins. Missing AX
  responses are retried for up to 600 ms at stop and before delivery, with bounded
  messaging timeouts. A changed app or a different resolved element still prevents
  paste. The microphone closes before waiting for the destination.
- Paste waits for physical modifier keys to be released, then posts Command+V to
  the macOS HID event stream. It never sends Return. If focus changes during
  finalization, it reports an error and preserves the complete text for recovery.
- Starting/stopping from VoiceFlow's main window hides the app and returns focus
  to the previous application. The floating panel doesn't need to restore focus.
- T3 Code uses the same standard paste path as other editors. No dedicated
  integration or bridge is required.
- Closing the window hides it. The menu bar menu reopens it or quits.

Paste sends Command+V, never Return. The reported outcome confirms the event
was sent, not that the destination editor confirmed insertion. The final text
replaces the clipboard and remains there until replaced. Automatic clipboard
expiry/restoration is not implemented.

Faithful formatting preserves recognized words and the model's punctuation.
The optional command formatter recognizes `a capo`, `nuovo paragrafo`,
`punto elenco`, `new line`, `new paragraph` and `bullet point`. Use faithful mode
when quoting those phrases literally. Semantic formatting will be a separate
text processor; this build sends no audio or transcript to remote APIs.

Language, format and local cleanup preferences are saved in SQLite at
`~/Library/Application Support/VoiceFlow/desktop-settings.sqlite3`. On first
initialization, the app imports the legacy `desktop-settings.json` in a transaction
and retains the original file; subsequent launches use SQLite only. Failed imports
can be retried, while unreadable databases and newer schemas are not replaced.
Completed transcript history is stored locally in `history.sqlite3`; audio is not
persisted. Explicit benchmarks write synthetic
transcripts under ignored `artifacts/streaming/`.

The bundle identifier is `com.voiceflow.desktop`. Microphone usage description
and the audio-input entitlement are in the bundle. Grant permissions to the
packaged app. `pnpm desktop:build` now requires a stable Apple signing identity.
On this Mac it uses the personal development certificate already in Keychain.
The signer and designated requirement are pinned outside the repository in
`~/Library/Application Support/VoiceFlow/build-signing.json`. The build verifies
the bundle signature and rejects a changed identity or ad-hoc fallback.
On a new Mac, set `APPLE_SIGNING_IDENTITY` to the intended Keychain certificate
for the first build; later builds reuse it automatically. Direct Tauri builds
without this wrapper fail in the pre-build hook.

The existing personal certificate expires on 2027-09-16. Renewal requires an
explicit signing configuration review; never delete the pin merely to get an
unrecognized signer through. Developer ID distribution and notarization remain
separate work. See [update permission verification](verification/2026-09-24-stable-signing.md).

## NVIDIA model and provenance

The base model is
[NVIDIA Nemotron 3.5 ASR Streaming 0.6B](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b).
The UI supports Italian, English and automatic detection. Accurate switching
between languages inside one phrase is not guaranteed.

The default download uses the
[Gimics INT8 ONNX conversion](https://huggingface.co/Gimics/nemotron-3.5-asr-streaming-0.6b-onnx-int8)
of the `altunenes/parakeet-rs` export. This is a third-party conversion of
NVIDIA's weights. Three artifacts are pinned by repository revision, size and
SHA-256 in `models/nemotron-manifest.json`. The downloader verifies each file
before renaming its temporary download into place. Re-running it verifies
existing files. Startup checks files and multilingual support, but does not
repeat the full hash scan.

The approximately 682 MB download lives in
`~/Library/Application Support/VoiceFlow/models/nemotron-3.5-streaming-int8`.
It is outside Git and the app bundle. Weights inherit NVIDIA's OpenMDW-1.1 terms;
preserve the license and conversion provenance when preparing distribution.
Application code retains the repository's MIT license. Inference uses
`parakeet-rs = 0.3.8` and `ort`, pinned by `Cargo.lock`.

## Development and verification

Verified on 24 September 2026, Apple M1 with 8 GB RAM and macOS 26.6.2:

| Component                | Installed       |
| ------------------------ | --------------- |
| Node via nvm             | 24.18.0         |
| pnpm                     | 11.5.0          |
| Rust and Cargo, Homebrew | 1.97.1          |
| Rustfmt and Clippy       | available       |
| Xcode and macOS SDK      | 27.0            |
| Clang                    | Xcode toolchain |

`pnpm doctor` repeats prerequisite checks and checks model-file presence.
Rustup and CMake are unnecessary here: Homebrew supplies Rust and `ort` uses
prebuilt ONNX Runtime. Other platforms and Intel Macs are unverified.

```bash
pnpm install --frozen-lockfile
pnpm model:download
pnpm desktop:check
pnpm desktop:ui
cargo fmt --all --check
cargo clippy --workspace --all-targets --locked -- -D warnings
cargo test --workspace --locked
pnpm desktop:build
```

The output is `target/release/bundle/macos/VoiceFlow.app`. Runtime needs no Vite
server. Run performance measurements separately from builds on an 8 GB Mac.

Rust tests cover preview before stop,
over 300 seconds of input, continuation after pauses, idempotent finish, silence,
invalid samples, resampling duration and command formatting.

The reproducible real-model checks use synthetic speech, not the microphone:

```bash
pnpm test:streaming
# Also process a sample longer than 120 seconds, without pacing:
pnpm test:streaming --long
```

These require `ffmpeg` and macOS `say` voices Alice and Samantha. They deliver
20 ms audio frames into the same resampler/session used by the app and assert
progressive output and key phrases, including negations and final words.
JSONL records retain successive partial transcripts and timing measurements.

Initial measured results on the M1 with INT8:

| Synthetic input | Duration | Inference |   RTF | First partial, input elapsed | Stop flush |
| --------------- | -------: | --------: | ----: | ---------------------------: | ---------: |
| Italian         |  13.33 s |    4.86 s | 0.365 |                       0.56 s |     353 ms |
| English         |  11.00 s |    3.21 s | 0.292 |                       0.56 s |     421 ms |

RTF below 1 means inference consumed less time than the audio duration. The
input timestamp is not wall-clock latency. The benchmark additionally reports
first-partial wall-clock latency in subsequent runs. Model load took 2.4–2.7 s;
Italian peak resident memory was about 959 MB. English omitted some sentence
punctuation. These samples verify functionality, not accuracy on the user's voice.

An initial FP32 trial was stopped after 258 seconds during initialization,
with concurrent compilation and severe memory pressure. It is not a comparable
performance result. INT8 was selected to reduce memory use, then verified with
actual progressive text output.

The repeatable suite also passed a 133.29-second sample, decoded in 32.07 seconds
with 242 chunks and all ten final confirmations preserved. This unpaced run
checks continuity beyond 120 seconds, not long-session microphone accuracy.
The repeated Italian and English paced tests showed first wall-clock partials
at 0.77 and 0.75 seconds respectively. See the
[measured verification record](verification/2026-09-24-streaming.json).

The packaged app was built and `codesign --verify --deep --strict` passed with
the audio-input entitlement. Its linked dependencies are system frameworks;
ONNX Runtime is linked into the executable. Native UI verification now covers
microphone capture, progressive transcription, actual insertion between existing
text in T3 Code, the floating panel's appearance, pause/resume with a frozen
clock, and retaining the editor's focus while using panel controls. The test draft
was removed without sending a message. Accessibility initially remained stale
across ad-hoc rebuilds: removing its old entry and adding the exact current bundle
resolved the mismatch. Stable signing now preserves permissions across updates.
The later signing check confirmed microphone capture and permission retention,
but did not confirm T3 insertion. See [cursor/HUD verification](verification/2026-09-24-cursor-and-hud.md)
and [the latest signing check](verification/2026-09-24-stable-signing.md).

## Remaining work

Before promotion to `main`, recheck T3 insertion and extend verification to other editors. Also
check long real dictation, device disconnection, focus changes during
finalization, cancellation, physical global shortcuts and reopening hidden windows. Assess whether the initial endpoint detector needs a trained VAD.

Possible later features include semantic formatting,
feedback/recovery history, configurable shortcuts, clipboard cleanup, an in-app
model downloader, updates, distribution signing and notarization. Evaluate
formatting quality, privacy and cost separately using the finished transcript.
