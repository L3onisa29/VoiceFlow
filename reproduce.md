# Reproduce VoiceBud

```bash
brew install uv
git clone <repository-url> VoiceFlow
cd VoiceFlow
uv sync --locked
codex login
uv run --locked voiceflow
```

Grant the uv-managed Python 3.12 runtime Microphone, Accessibility, and Input Monitoring permissions in System Settings → Privacy & Security, then restart VoiceBud. Model discovery, configuration, LaunchAgent installation, privacy, tests, and benchmark commands are documented in [README.md](README.md).

