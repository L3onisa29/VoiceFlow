from __future__ import annotations

import argparse
from collections.abc import Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="voiceflow", description="VoiceBud dictation")
    parser.add_argument("--config", help="Path to the strict VoiceFlow YAML configuration")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose diagnostic logging in the terminal and configured log file",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    from voiceflow.app import run

    return run(config_path=args.config, debug=args.debug)


if __name__ == "__main__":
    raise SystemExit(main())
