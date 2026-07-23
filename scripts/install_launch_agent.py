#!/usr/bin/env -S uv run --script
from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from xml.sax.saxutils import escape

LABEL = "com.voiceflow.voicebud"


def render_plist(
    template: str,
    *,
    uv: Path,
    project: Path,
    config: Path,
    log_dir: Path,
    codex: Path | None = None,
) -> str:
    values = {
        "@UV@": uv.expanduser().absolute(),
        "@PROJECT@": project.expanduser().resolve(),
        "@CONFIG@": config.expanduser().resolve(),
        "@LOG_DIR@": log_dir.expanduser().resolve(),
    }
    rendered = template
    for marker, value in values.items():
        rendered = rendered.replace(marker, escape(str(value)))
    path_entries = [
        uv.expanduser().absolute().parent,
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
        Path("/usr/bin"),
        Path("/bin"),
        Path("/usr/sbin"),
        Path("/sbin"),
    ]
    environment_values = {
        "PATH": ":".join(dict.fromkeys(str(path) for path in path_entries)),
    }
    if codex is not None:
        environment_values["VOICEFLOW_CODEX_BIN"] = str(codex.expanduser().absolute())
    environment_lines = [
        "  <key>EnvironmentVariables</key>",
        "  <dict>",
    ]
    for key, value in environment_values.items():
        environment_lines.extend(
            [
                f"    <key>{escape(key)}</key>",
                f"    <string>{escape(value)}</string>",
            ]
        )
    environment_lines.append("  </dict>")
    rendered = rendered.replace("@ENVIRONMENT@", "\n".join(environment_lines))
    return rendered


def build_parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Install the VoiceBud LaunchAgent plist")
    parser.add_argument("--project", type=Path, default=root)
    parser.add_argument("--config", type=Path, default=root / "config.yaml")
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    project = args.project.expanduser().resolve()
    config = args.config.expanduser().resolve()
    destination = args.destination.expanduser().resolve()
    uv_bin = shutil.which("uv")
    if uv_bin is None:
        raise SystemExit("uv is not available on PATH")
    if not config.is_file():
        raise SystemExit(f"Configuration not found: {config}")
    template_path = project / "launchd" / f"{LABEL}.plist.in"
    if not template_path.is_file():
        raise SystemExit(f"LaunchAgent template not found: {template_path}")
    log_dir = Path.home() / "Library/Logs/VoiceBud"
    log_dir.mkdir(parents=True, exist_ok=True)
    codex_bin = shutil.which("codex")
    rendered = render_plist(
        template_path.read_text(encoding="utf-8"),
        uv=Path(uv_bin),
        project=project,
        config=config,
        log_dir=log_dir,
        codex=Path(codex_bin) if codex_bin is not None else None,
    )

    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup = destination.with_suffix(f".plist.backup-{timestamp}")
        shutil.copy2(destination, backup)
        print(f"Backed up existing plist to {backup}")
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".plist", encoding="utf-8", delete=False
    ) as temporary:
        temporary.write(rendered)
        temporary_path = Path(temporary.name)
    try:
        subprocess.run(["plutil", "-lint", str(temporary_path)], check=True)
        shutil.copy2(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)
    print(f"Installed {destination}")
    if codex_bin is None:
        print(
            "Codex was not found during installation; set postprocess.codex.executable "
            "when Codex post-processing is enabled."
        )
    print(f"Load explicitly with: launchctl bootstrap gui/$(id -u) {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
