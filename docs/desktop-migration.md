# VoiceFlow desktop migration

Work branch: `codex/tauri-streaming-app`. `main` is unchanged. Pending work was
saved on `codex/uv-parakeet-migration` before creating this branch:

- `03c533d`: retained-audio expiry and abandoned feedback cleanup.
- `6606bb4`: cancellation shortcut and dictation lifecycle changes.
- `86af34d`: ignore local test artifacts.

## Current application

The Tauri `.app` embeds React assets. CPAL captures the system microphone; Rust
resamples audio to mono 16 kHz and feeds the NVIDIA decoder every 560 ms.
Encoder caches persist between chunks. Tauri events update the preview while
recording. Stopping drains queued audio and flushes decoder lookahead.

There is no recording timer or 30-second transcription batch. The audio queue
is bounded. A microphone error or processing overrun stops dictation with a
visible error and preserves recognized text. Transcript memory grows with text,
so available memory still matters in very long sessions.

Two seconds of low audio energy commits the phrase and resets the decoder
without ending dictation. This initial endpoint detector uses an RMS threshold,
not a trained VAD. Soft speech and noise need real-microphone evaluation.

The model loads once per process. ONNX Runtime runs on CPU with four intra-op
threads. No NVIDIA GPU, CUDA, Python process or API key is needed.

### Interaction and storage

- Grant Microphone access to VoiceFlow, and Accessibility for automatic paste.
- `Option+Space` starts and stops dictation from another app.
- `Control+Space` cancels during recording and is released afterwards.
- The app captures the focused text field at start and pastes only if that
  element remains focused at completion. Secure fields are excluded.
- Starting inside VoiceFlow, changing focus, or lacking Accessibility leaves
  the final text in the preview for manual copying.
- Closing the window hides it. The menu bar menu reopens it or quits.

Paste sends Command+V, never Return. The reported outcome confirms the event
was sent, not that the destination editor confirmed insertion. The final text
replaces the clipboard and remains there until replaced. Legacy clipboard
expiry/restoration has not yet been ported.

Faithful formatting preserves recognized words and the model's punctuation.
The optional command formatter recognizes `a capo`, `nuovo paragrafo`,
`punto elenco`, `new line`, `new paragraph` and `bullet point`. Use faithful mode
when quoting those phrases literally. Semantic formatting will be a separate
text processor; this build sends no audio or transcript to remote APIs.

Only language and format preferences are saved, atomically, to
`~/Library/Application Support/VoiceFlow/desktop-settings.json`. No audio or
transcript history is written by the app. Explicit benchmarks write synthetic
transcripts under ignored `artifacts/streaming/`.

The bundle identifier is `com.voiceflow.desktop`. Microphone usage description
and the audio-input entitlement are in the bundle. Grant permissions to the
packaged app. Ad-hoc builds can require renewed permission after rebuilding or
moving. Stable distribution signing and notarization remain to be configured.

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

| Component | Installed |
| --- | --- |
| Node via nvm | 24.18.0 |
| pnpm | 11.5.0 |
| Rust and Cargo, Homebrew | 1.97.1 |
| Rustfmt and Clippy | available |
| Xcode and macOS SDK | 27.0 |
| Clang | Xcode toolchain |

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

The Python baseline passed 619 tests with 2 opt-in tests skipped, Ruff and
Pyright before preservation commits. Rust tests cover preview before stop,
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

| Synthetic input | Duration | Inference | RTF | First partial, input elapsed | Stop flush |
| --- | ---: | ---: | ---: | ---: | ---: |
| Italian | 13.33 s | 4.86 s | 0.365 | 0.56 s | 353 ms |
| English | 11.00 s | 3.21 s | 0.292 | 0.56 s | 421 ms |

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
ONNX Runtime is linked into the executable. Native UI inspection could not run because the
computer-control tool failed twice with `Sky Computer Use native pipe startup
failed`. Physical microphone capture, permissions prompts and target-app paste
remain unverified. The synthetic benchmark does not cover those integrations.

## Remaining migration work

This is the first desktop implementation, not feature parity with Python.
Before promotion to `main`, verify physical microphones, long real dictation,
device disconnection, focus changes, cancellation and paste in the user's
editors. Assess whether the initial endpoint detector needs a trained VAD.

Still to port or implement: Codex/Ollama semantic formatting, T3 integration,
feedback/recovery history, configurable shortcuts, clipboard cleanup, an in-app
model downloader, updates, distribution signing and notarization. Evaluate
formatting quality, privacy and cost separately using the finished transcript.
