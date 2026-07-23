# Reproduce VoiceBud

```bash
brew install uv
git clone <repository-url> VoiceFlow
cd VoiceFlow
uv sync --locked
codex login
uv run --locked voiceflow
```

VoiceBud requests Microphone, Accessibility, and Input Monitoring on first start. If macOS requires manual approval, grant all three to the uv-managed Python 3.12 runtime in System Settings → Privacy & Security, then choose “Riprova permessi / ingresso” from the menu. Press `F12` once to start recording and once to stop; use `fn`+`F12` when macOS maps that key to volume. Model discovery, configuration, LaunchAgent installation, privacy, tests, and benchmark commands are documented in [README.md](README.md).
