# VoiceBud

VoiceBud is a macOS menu-bar dictation app. It records locally, transcribes with the multilingual Parakeet TDT 0.6B V3 ONNX INT8 model on `CPUExecutionProvider`, optionally cleans the transcript, and inserts it at the current cursor.

Codex is the default post-processor (`gpt-5.6-luna`, low reasoning). Audio never leaves the Mac: only non-empty STT text is supplied to the configured post-processor. If Codex or Ollama is unavailable, VoiceBud immediately falls back to the original transcript. Optional Codex model validation sends a synthetic preflight sentence when `validate_model_on_startup` is enabled; it is disabled by default and never blocks local STT startup.

## Run

Requirements: macOS, [uv](https://docs.astral.sh/uv/), and Microphone, Accessibility, and Input Monitoring permissions for uv's Python 3.12 runtime. Codex CLI authentication is required only when the Codex post-processor is enabled.

```bash
uv sync --locked
codex login status
uv run --locked voiceflow
```

Normal operation logs only startup information, warnings, and errors. File and terminal
writes run on a background logging thread and never perform I/O on recording, UI, or
pipeline threads. For a temporary, detailed development trace, start VoiceBud with:

```bash
uv run --locked voiceflow --debug
```

In debug mode every dictation emits grep-friendly events with its job ID, host and target platform,
destination app bundle, operation (`integration`, `input_write`, or `clipboard_write`),
delivery channel, processing stage, outcome, error code, and timings. Transcript contents,
provider stderr, bridge secrets, and complete payloads are never included. The configured
rotating log can be followed from another terminal:

```bash
tail -F ~/Library/Logs/VoiceBud/voicebud.log
rg 'dictation.failed|integration.t3.failed|delivery.permission_denied' \
  ~/Library/Logs/VoiceBud/voicebud.log
```

Press `Option`+`Space` once to start recording and again to stop. The space bar and any `F1`–`F20` key can be combined with supported modifiers. This local configuration selects `MacBook Pro Microphone` explicitly; set `audio.device: null` to follow the current CoreAudio default instead. VoiceBud requests and verifies Microphone, Accessibility, and Input Monitoring before activating input, but opens the microphone stream only for the duration of each recording and closes it immediately afterward. It monitors the permissions, recording stream, and hotkey listener while running, rejects genuinely short or silent captures, stops a forgotten toggle recording after 120 seconds, and exposes recoverable input errors through the menu.

The focused Accessibility element is captured when recording stops, so the destination and cursor can be changed at any point while speaking. If no destination is available at that moment, VoiceBud processes the recording and leaves the transcript on the clipboard. If the destination changes while STT or post-processing runs, VoiceBud does not send keystrokes to the new field: it leaves the transcript on the clipboard and reports the change. Paste insertion clears the transcript after 60 seconds only if the clipboard is still unchanged, so newer user clipboard content is never removed.

## T3 Code Desktop bridge

When the authoritative macOS bundle identifier `com.t3tools.t3code` is frontmost as recording begins, VoiceBud uses the local T3 bridge instead of Accessibility. A failed or unavailable frontmost-app check stops recording before audio capture; the visible app name alone is never used to identify T3 Code.

The production T3 Code bundle creates `~/.t3/userdata/integrations/voicebud/bridge.json`; when `T3CODE_HOME` is set, it replaces the `~/.t3` prefix. The descriptor must be a regular, user-owned, non-symlink file with mode `0600` and exactly this schema:

```json
{
  "version": 1,
  "transport": "unix",
  "socketPath": "/Users/me/.t3/userdata/integrations/voicebud/bridge-1234-random.sock",
  "secret": "<per-session-random-secret>",
  "pid": 1234
}
```

`pid` must be a positive integer. The socket path must be absolute, must resolve inside the descriptor directory, and must identify a real, non-symlink Unix socket owned by the same user. VoiceBud opens the descriptor with `O_NOFOLLOW`, validates it using `fstat`, limits it to 16 KiB, and rejects group/other permissions. It also pins the descriptor fingerprint and the socket `st_dev`/`st_ino` for every accepted recording.

Each connection carries exactly one compact UTF-8 JSON object followed by newline and receives exactly one newline-terminated response. `recording.started` contains `version`, `type`, a fresh `requestId` formed as `<process-random-prefix>:<monotonic-counter>` (with the counter incremented under lock), an independently generated random UUID `nonce`, the recording UUID v4, Unix `sentAt` milliseconds, and `auth`. Only an explicit correlated response accepts the reservation:

```json
{
  "version": 1,
  "requestId": "<same-request-id>",
  "accepted": true,
  "code": "accepted"
}
```

A rejection sets `accepted` to `false` and includes a code such as `authentication_failed`, `expired`, `malformed`, `oversized`, `rate_limited`, `replay`, `duplicate_recording`, `unknown_recording`, `renderer_unavailable`, `delivery_failed`, or `delivery_ambiguous`. Responses contain exactly `version`, `requestId`, `accepted`, and `code`; they do not echo `recordingId` or `nonce`. Unknown or missing fields, malformed JSON, inconsistent `accepted`/`code`, wrong request correlation, oversized responses, EOF before newline, timeout, or disconnect never count as acceptance.

After STT, deterministic normalization, and optional post-processing, `transcription.completed` sends the same recording ID plus the final text in `transcript`. The destination is immutable from the accepted start through completion: VoiceBud does not recapture focus, inspect a different chat, inject keystrokes, paste, or fall back to the clipboard. Descriptor or socket replacement—including a T3 restart—invalidates the pinned session, so the transcript is not delivered to the new process.

Connection and write share a monotonic two-second budget. After the request has been written, VoiceBud allows a separate six-second response budget, including the renderer acknowledgement. There are no automatic retries: an explicit rejection is terminal and a timeout or disconnect is ambiguous. Both cases fail closed and remain isolated from other queued recordings. VoiceBud does not automatically inject or copy a failed T3 transcript to the clipboard; the existing explicit “copy last transcription” action remains available. Logs and bridge results exclude the secret, transcript, descriptor content, and complete wire payloads.

### Manual end-to-end check

1. Start a T3 Code build that implements the v1 Unix-socket endpoint and creates the descriptor and socket above with the required ownership and permissions.
2. Start VoiceBud, focus T3 Code, press `Option`+`Space`, and verify T3 receives and explicitly accepts one `recording.started`.
3. Speak, press `Option`+`Space`, and verify T3 receives one `transcription.completed` with the same `recordingId` and the post-processed final text.
4. Repeat with another app frontmost and verify the existing Accessibility-at-stop behavior is unchanged.
5. During an accepted recording, restart T3 or replace its descriptor/socket; verify VoiceBud reports a closed failure and the replacement process receives no completion.
6. Simulate rejection, timeout, disconnect, and concurrent recordings; verify no retry, paste, typing, or cross-job interference occurs.

Configuration schema 3 is strict. Schema-1 and schema-2 files are migrated in memory, including the former Codex prompt location and deprecated clipboard/logging keys; unsafe legacy `ctrl+shift` defaults become `alt+space`. The recording hotkey defaults to `Alt/Option + Space`; while recording, `Control + Space` cancels the capture, abandons any T3 Code conversation, and skips transcription and model processing. Both chords are configurable through `hotkey.key` and `hotkey.cancel_key`. History is disabled by default; the dictionary and RAM-only correction feedback are local. Other unknown keys, the former `llm` section, and Whisper engine settings are rejected. Selection order is `voiceflow --config`, `VOICEFLOW_CONFIG`, then the repository or packaged `config.yaml`. See [config.yaml](config.yaml) for every supported setting.

The first run reuses a valid Handy Parakeet model in place when possible. If that model cannot be loaded, VoiceBud leaves it untouched and falls back to its own cache under `~/Library/Application Support/VoiceFlow/models/`. Invalid VoiceFlow caches are quarantined and downloaded again.

VoiceBud applies deterministic spoken punctuation, explicit-list and structured-token rules before deciding whether semantic cleanup is needed. Short clean dictations stay on the rules-only fast path; “Pulizia approfondita alla prossima dettatura” requests provider cleanup once without changing the `Option`+`Space` recording shortcut. Dictionary replacements are local and scoped only by language and the destination bundle identifier. Provider prompts receive preferred terms as delimited data and never receive the destination bundle identifier.

`benchmarks/benchmark_quality.py` evaluates a local JSONL corpus without adding audio to the repository. It reports WER, CER, required-term recall, empty rate, latency percentiles and corpus/configuration fingerprints; `benchmarks/manifest.example.jsonl` documents the format. Baseline regression checks run only for matching corpus and configuration fingerprints.

## Quality and benchmark

```bash
uv lock --check
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest
uv run --locked pyright
uv build --wheel
uv run --locked benchmarks/benchmark_stt.py path/to/mono-16k.wav --json-output benchmark.json
```

Real Codex integration tests are opt-in with `VOICEFLOW_RUN_CODEX_INTEGRATION=1`; the real Ollama integration is opt-in with `VOICEFLOW_RUN_OLLAMA_INTEGRATION=1`.

## LaunchAgent

Generate and install the per-user plist explicitly:

```bash
uv run scripts/install_launch_agent.py
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.voiceflow.voicebud.plist
```

The installer backs up an existing plist, validates it with `plutil`, records absolute paths for uv and the currently discoverable Codex executable, and sets `KeepAlive=false`. Runtime and configuration-error logs rotate at `~/Library/Logs/VoiceBud/voicebud.log`; LaunchAgent stdout and stderr are disabled to avoid unbounded duplicate logs. Quit from the menu bar stops the app without LaunchAgent restart.

VoiceBud is currently a local Python application rather than a signed `.app` bundle. macOS therefore grants TCC permissions to the uv-managed Python executable. Recreating that environment or moving the project can require granting the permissions again.

## Privacy

Microphone samples remain in this process and are passed only to local ONNX Runtime. VoiceBud never sends audio to Codex or Ollama. When post-processing is enabled, only transcript text is sent to that provider. Transcript contents and provider stderr are not written to VoiceBud logs. Pipeline-owned samples are zeroed after processing, while the bounded correction buffer schedules active expiry using `feedback.recent_audio_seconds` and zeroes expired samples even when the app is idle. Confirmed benchmark WAVs remain persistent by design; startup cleanup removes only abandoned hidden transaction files and never confirmed WAVs or `manifest.jsonl`.

Directories created by VoiceBud must be exactly `0700`; pre-existing custom directories
keep their existing mode. If an unusual process umask removes owner permissions from a
new directory, VoiceBud fails safely instead of applying `chmod`: after `mkdir` returns,
the created inode may already have been replaced, so mutating the path would be unsafe.
