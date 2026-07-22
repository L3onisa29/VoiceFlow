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
) -> str:
    values = {
        "@UV@": uv,
        "@PROJECT@": project,
        "@CONFIG@": config,
        "@LOG_DIR@": log_dir,
    }
    rendered = template
    for marker, value in values.items():
        rendered = rendered.replace(marker, escape(str(value.resolve())))
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
    rendered = render_plist(
        template_path.read_text(encoding="utf-8"),
        uv=Path(uv_bin),
        project=project,
        config=config,
        log_dir=log_dir,
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
    print(f"Load explicitly with: launchctl bootstrap gui/$(id -u) {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

