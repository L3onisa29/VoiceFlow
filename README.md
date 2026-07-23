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

Press `F12` once to start recording and again to stop; on a MacBook configured for media keys, use `fn`+`F12`. Any `F1`–`F20` key, optionally combined with supported modifiers, can be configured. This local configuration selects `MacBook Pro Microphone` explicitly; set `audio.device: null` to follow the current CoreAudio default instead. VoiceBud requests and verifies Microphone, Accessibility, and Input Monitoring before activating input, but opens the microphone stream only for the duration of each recording and closes it immediately afterward. It monitors the permissions, recording stream, and hotkey listener while running, rejects genuinely short or silent captures, stops a forgotten toggle recording after 120 seconds, and exposes recoverable input errors through the menu.

The focused Accessibility element is captured when recording stops, so the destination and cursor can be changed at any point while speaking. If no destination is available at that moment, VoiceBud processes the recording and leaves the transcript on the clipboard. If the destination changes while STT or post-processing runs, VoiceBud does not send keystrokes to the new field: it leaves the transcript on the clipboard and reports the change. Paste insertion clears the transcript after 60 seconds only if the clipboard is still unchanged, so newer user clipboard content is never removed.

Configuration schema 2 is strict. Schema-1 files are migrated in memory, including the former Codex prompt location and deprecated clipboard/logging keys; unsafe legacy `ctrl+shift` defaults become `f12`. Other unknown keys, the former `llm` section, and Whisper engine settings are rejected. Selection order is `voiceflow --config`, `VOICEFLOW_CONFIG`, then the repository or packaged `config.yaml`. See [config.yaml](config.yaml) for every supported setting.

The first run reuses a valid Handy Parakeet model in place when possible. If that model cannot be loaded, VoiceBud leaves it untouched and falls back to its own cache under `~/Library/Application Support/VoiceFlow/models/`. Invalid VoiceFlow caches are quarantined and downloaded again.

## Quality and benchmark

```bash
uv lock --check
uv run --locked ruff check .
uv run --locked pytest
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

Microphone samples remain in this process and are passed only to local ONNX Runtime. VoiceBud never sends audio to Codex or Ollama. When post-processing is enabled, only transcript text is sent to that provider. Transcript contents and provider stderr are not written to VoiceBud logs.
