# VoiceFlow

A macOS dictation app built with Rust, Tauri 2, React and TanStack Router. NVIDIA Nemotron 3.5
ASR Streaming 0.6B transcribes Italian and English locally while you speak.
The app shows a live preview and pastes the final text at the cursor when you
finish. There is no configured recording duration limit.

## Build and use

Requirements: macOS, Xcode command-line tools, Node 24, pnpm 11 and a Rust
compiler with Rustfmt and Clippy. Local packaged builds require an Apple signing
certificate in Keychain. Check the installed tools with `pnpm doctor`.

```bash
pnpm install --frozen-lockfile
pnpm doctor
pnpm model:download
pnpm desktop:build
```

On this Mac the build automatically reuses the saved signing certificate. On a
new Mac, set `APPLE_SIGNING_IDENTITY` to the intended certificate for the first
build. Subsequent builds retain that identity; a missing or changed certificate
stops the build instead of silently producing an ad-hoc signature.

Open `target/release/bundle/macos/VoiceFlow.app` from Finder. Once built and the
model downloaded, normal use needs no terminal or development server. Grant
Microphone and Accessibility to VoiceFlow. Stable signing preserves these grants
across updates built with the same identity.

- `Option+Space` starts dictation, then finishes and pastes into the focused app.
- `Control+Space` cancels the dictation.
- The floating indicator shows elapsed time, audio activity and pause/resume and
  finish controls without taking focus from the destination editor.
- Formatting can preserve the transcript or interpret spoken commands such as
  `a capo`, `nuovo paragrafo`, `punto elenco` and their English equivalents.

Paste sends Command+V and never submits the destination form. If delivery fails,
the transcript remains available to copy. T3 Code uses the standard paste path.
Audio and transcripts stay on the Mac; no API key or remote model is required.

## Project structure

| Path                            | Purpose                                                                |
| ------------------------------- | ---------------------------------------------------------------------- |
| `apps/desktop/src`              | React interface and Tauri events                                       |
| `apps/desktop/src-tauri`        | Native app, microphone, shortcuts, indicator, permissions and paste    |
| `crates/voiceflow-core`         | Resampling, NVIDIA streaming and transcript formatting                 |
| `scripts`                       | Build, signing, model download, diagnostics and streaming verification |
| `models/nemotron-manifest.json` | Pinned model files, sizes and checksums                                |
| `docs`                          | Desktop behavior and verification records                              |

The repository contains the current desktop implementation only. The previous
Python implementation remains recoverable from Git history before the cleanup.

## Verification

```bash
pnpm desktop:check
pnpm desktop:ui
pnpm test:signing
cargo fmt --all --check
cargo clippy --workspace --all-targets --locked -- -D warnings
cargo test --workspace --locked
pnpm desktop:build
```

CI runs the TypeScript check, UI build, signing-policy tests, Rust formatting,
Clippy, Rust tests and a release compilation on macOS. CI does not distribute an
unsigned app or require the local signing certificate. Packaged builds use
`pnpm desktop:build` on the signing machine.

For real-model streaming verification, install `ffmpeg` and the macOS Alice and
Samantha voices, then run:

```bash
pnpm test:streaming
pnpm test:streaming --long
```

These checks use synthetic speech and the same Rust resampler and streaming
session as the app. The long check processes more than 120 seconds of audio.

See [desktop documentation](docs/desktop.md) for model provenance, storage,
signing and remaining work. The [latest permission verification](docs/verification/2026-09-24-stable-signing.md)
confirmed permissions across two builds and microphone transcription. Its T3
insertion check remained inconclusive; successful insertion from an earlier run
is documented separately.
