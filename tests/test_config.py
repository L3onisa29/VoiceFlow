from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from voiceflow.config import AppConfig, load_config, resolve_config_path


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


def test_default_input_and_clipboard_policy() -> None:
    config = load_config(root=Path(__file__).resolve().parents[1], environ={})
    assert config.schema_version == 3
    assert config.audio.device == "MacBook Pro Microphone"
    assert not hasattr(config.audio, "preroll_ms")
    assert config.hotkey.key == "alt+space"
    assert not hasattr(config.inject, "restore_clipboard")


def test_legacy_preroll_is_ignored_to_keep_the_microphone_closed() -> None:
    config = AppConfig.model_validate(
        {
            "schema_version": 2,
            "stt": {},
            "postprocess": {"provider": "none"},
            "audio": {"preroll_ms": 500},
        }
    )
    assert not hasattr(config.audio, "preroll_ms")


def test_function_key_chords_are_configurable() -> None:
    config = AppConfig.model_validate(
        {
            "schema_version": 2,
            "stt": {},
            "postprocess": {"provider": "none"},
            "hotkey": {"key": "ctrl+shift+f12"},
        }
    )
    assert config.hotkey.key == "ctrl+shift+f12"


def test_option_space_is_configurable() -> None:
    config = AppConfig.model_validate(
        {
            "schema_version": 2,
            "stt": {},
            "postprocess": {"provider": "none"},
            "hotkey": {"key": "alt+space"},
        }
    )
    assert config.hotkey.key == "alt+space"


def test_schema_v1_is_migrated_without_preserving_unsafe_defaults(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[1] / "config.yaml"
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    raw["schema_version"] = 1
    raw["postprocess"]["codex"]["system_prompt"] = raw["postprocess"].pop("system_prompt")
    raw["inject"]["restore_clipboard"] = True
    raw["logging"]["include_transcripts"] = False
    raw["hotkey"]["key"] = "ctrl+shift"
    destination = tmp_path / "legacy.yaml"
    destination.write_text(yaml.safe_dump(raw), encoding="utf-8")

    config = load_config(destination)

    assert config.schema_version == 3
    assert config.hotkey.key == "alt+space"
    assert config.postprocess.system_prompt.startswith("Edit the supplied")


def test_none_provider_accepts_only_relevant_configuration() -> None:
    config = AppConfig.model_validate(
        {
            "schema_version": 2,
            "stt": {},
            "postprocess": {"provider": "none"},
        }
    )
    assert config.postprocess.provider == "none"


def test_schema_v2_migrates_to_privacy_first_schema_v3_defaults() -> None:
    config = AppConfig.model_validate(
        {
            "schema_version": 2,
            "stt": {},
            "postprocess": {"provider": "none"},
        }
    )
    assert config.schema_version == 3
    assert config.dictionary.enabled
    assert not config.history.enabled
    assert config.history.retention_hours == 24
    assert config.history.max_entries == 100
    assert not config.history.store_app_bundle_id
    assert config.feedback.recent_audio_seconds == 300
    assert config.postprocess.routing.mode == "auto"
    assert config.hotkey.key == "alt+space"


@pytest.mark.parametrize(
    ("section", "changes", "match"),
    [
        ("hotkey", {"key": "ctrl+shift"}, "non-modifier"),
        ("audio", {"sample_rate": 96000}, "sample_rate"),
        (
            "audio",
            {"minimum_recording_ms": 1000, "maximum_recording_seconds": 0.5},
            "maximum_recording_seconds",
        ),
    ],
)
def test_unsafe_runtime_configuration_is_rejected(
    tmp_path: Path,
    section: str,
    changes: dict[str, object],
    match: str,
) -> None:
    source = Path(__file__).resolve().parents[1] / "config.yaml"
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    raw[section].update(changes)
    destination = tmp_path / "invalid-runtime.yaml"
    destination.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValueError, match=match):
        load_config(destination)
