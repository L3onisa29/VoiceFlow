from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from voiceflow.config import load_config, resolve_config_path


def write_config(source: Path, destination: Path, **changes: object) -> None:
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    raw.update(changes)
    destination.write_text(yaml.safe_dump(raw), encoding="utf-8")


def test_config_precedence(tmp_path: Path) -> None:
    cli = tmp_path / "cli.yaml"
    env = tmp_path / "env.yaml"
    assert resolve_config_path(cli, environ={"VOICEFLOW_CONFIG": str(env)}) == cli.resolve()
    assert resolve_config_path(None, environ={"VOICEFLOW_CONFIG": str(env)}) == env.resolve()
    assert resolve_config_path(None, environ={}, root=tmp_path) == (tmp_path / "config.yaml")


def test_relative_paths_are_based_on_yaml(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[1] / "config.yaml"
    destination = tmp_path / "voicebud.yaml"
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    raw["stt"]["model_path"] = "models/parakeet"
    raw["postprocess"]["codex"]["executable"] = "bin/codex"
    destination.write_text(yaml.safe_dump(raw), encoding="utf-8")
    config = load_config(destination)
    assert config.stt.model_path == (tmp_path / "models/parakeet").resolve()
    assert config.postprocess.codex.executable == (tmp_path / "bin/codex").resolve()


@pytest.mark.parametrize(
    ("section", "value"),
    [
        ("legacy", True),
        ("llm", {"model": "old"}),
    ],
)
def test_unknown_and_legacy_sections_are_rejected(
    tmp_path: Path, section: str, value: object
) -> None:
    source = Path(__file__).resolve().parents[1] / "config.yaml"
    destination = tmp_path / "invalid.yaml"
    write_config(source, destination, **{section: value})
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        load_config(destination)


def test_legacy_whisper_key_is_rejected(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[1] / "config.yaml"
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    raw["stt"]["engine"] = "faster" + "-whisper"
    destination = tmp_path / "invalid.yaml"
    destination.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="engine"):
        load_config(destination)


def test_reasoning_is_limited_to_low_or_medium(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[1] / "config.yaml"
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    raw["postprocess"]["codex"]["reasoning_effort"] = "high"
    destination = tmp_path / "invalid.yaml"
    destination.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="reasoning_effort"):
        load_config(destination)
