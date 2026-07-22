# VoiceBud

VoiceBud is a macOS menu-bar dictation app. It records locally, transcribes with the multilingual Parakeet TDT 0.6B V3 ONNX INT8 model on `CPUExecutionProvider`, optionally cleans the transcript, and inserts it at the current cursor.

Codex is the default post-processor (`gpt-5.6-luna`, low reasoning). Audio never leaves the Mac: only non-empty STT text is supplied to Codex. If Codex is unavailable or times out, VoiceBud inserts the original transcript.

## Run

Requirements: macOS, [uv](https://docs.astral.sh/uv/), a Codex CLI authenticated with ChatGPT, and Microphone, Accessibility, and Input Monitoring permissions for uv's Python 3.12 runtime.

```bash
uv sync --locked
codex login status
uv run --locked voiceflow
```

Press `ctrl+shift` once to start recording and again to stop. The menu bar and overlay report model download/loading, recording, queue, STT, post-processing, insertion, fallback, and errors. The first run reuses a valid Handy Parakeet model in place when present; otherwise it downloads into `~/Library/Application Support/VoiceFlow/models/`.

Configuration is strict. Unknown keys, the former `llm` section, and Whisper engine settings are rejected. Selection order is `voiceflow --config`, `VOICEFLOW_CONFIG`, then the repository `config.yaml`. See [config.yaml](config.yaml) for every supported setting.

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

The installer backs up an existing plist, validates the generated file with `plutil`, uses absolute paths, sets `KeepAlive=false`, and writes logs under `~/Library/Logs/VoiceBud/`. Quit from the menu bar stops the app without LaunchAgent restart.

## Privacy

Microphone samples remain in this process and are passed only to local ONNX Runtime. VoiceBud never sends audio to Codex or Ollama. When post-processing is enabled, the transcript text is sent to that configured provider. Transcript contents are excluded from logs unless `logging.include_transcripts` is explicitly enabled.

