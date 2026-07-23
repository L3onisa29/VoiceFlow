from __future__ import annotations

import importlib.util
import plistlib
from pathlib import Path


def load_installer() -> object:
    path = Path(__file__).resolve().parents[1] / "scripts/install_launch_agent.py"
    spec = importlib.util.spec_from_file_location("voiceflow_launch_installer", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plist_template_has_safe_launch_semantics(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    template = (root / "launchd/com.voiceflow.voicebud.plist.in").read_text(encoding="utf-8")
    installer = load_installer()
    rendered = installer.render_plist(  # type: ignore[attr-defined]
        template,
        uv=tmp_path / "uv",
        project=tmp_path / "Voice & Flow",
        config=tmp_path / "Voice & Flow/config.yaml",
        log_dir=tmp_path / "Logs",
        codex=tmp_path / "bin/codex",
    )
    assert "com.voiceflow.voicebud" in rendered
    assert "<key>KeepAlive</key>\n  <false/>" in rendered
    assert "run" in rendered and "--locked" in rendered and "--no-dev" in rendered
    assert "--config" in rendered
    assert "/Users/" + "riadanas" not in rendered
    assert "Voice &amp; Flow" in rendered
    plist = plistlib.loads(rendered.encode())
    assert plist["EnvironmentVariables"]["VOICEFLOW_CODEX_BIN"] == str(
        (tmp_path / "bin/codex").resolve()
    )
    assert "/usr/local/bin" in plist["EnvironmentVariables"]["PATH"]
    assert plist["StandardOutPath"] == "/dev/null"
    assert plist["StandardErrorPath"] == "/dev/null"


def test_rendered_executables_keep_stable_symlink_paths(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    template = (root / "launchd/com.voiceflow.voicebud.plist.in").read_text(encoding="utf-8")
    installer = load_installer()
    versioned = tmp_path / "Caskroom/codex/1.2.3/codex"
    versioned.parent.mkdir(parents=True)
    versioned.touch()
    stable = tmp_path / "bin/codex"
    stable.parent.mkdir()
    stable.symlink_to(versioned)
    rendered = installer.render_plist(  # type: ignore[attr-defined]
        template,
        uv=tmp_path / "bin/uv",
        project=tmp_path / "VoiceFlow",
        config=tmp_path / "VoiceFlow/config.yaml",
        log_dir=tmp_path / "Logs",
        codex=stable,
    )

    plist = plistlib.loads(rendered.encode())
    assert plist["EnvironmentVariables"]["VOICEFLOW_CODEX_BIN"] == str(stable.absolute())
