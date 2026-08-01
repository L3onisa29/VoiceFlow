from __future__ import annotations

from pathlib import Path

import pytest

from voiceflow.config import AppConfig
from voiceflow.stt.model_locator import ModelLocator, ModelValidationError, validate_model


def test_explicit_path_has_priority(app_config: AppConfig, valid_model: Path) -> None:
    app_config.stt.model_path = valid_model
    result = ModelLocator(
        app_config.stt,
        environ={"VOICEFLOW_MODEL_PATH": "/invalid"},
        handy_path=Path("/invalid"),
    ).resolve()
    assert result.path == valid_model
    assert result.source == "config"


def test_invalid_explicit_and_environment_paths_are_errors(
    app_config: AppConfig, tmp_path: Path
) -> None:
    app_config.stt.model_path = tmp_path / "missing"
    with pytest.raises(ModelValidationError):
        ModelLocator(app_config.stt, environ={}).resolve()
    app_config.stt.model_path = None
    with pytest.raises(ModelValidationError):
        ModelLocator(
            app_config.stt, environ={"VOICEFLOW_MODEL_PATH": str(tmp_path / "missing")}
        ).resolve()


def test_handy_is_reused_in_place(app_config: AppConfig, valid_model: Path, tmp_path: Path) -> None:
    result = ModelLocator(
        app_config.stt,
        environ={},
        handy_path=valid_model,
        cache_root=tmp_path / "cache",
    ).resolve()
    assert result.path is valid_model
    assert result.source == "handy"
    assert not result.needs_download


def test_cache_then_download(app_config: AppConfig, valid_model: Path, tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    cached = cache_root / "parakeet-tdt-0.6b-v3-int8"
    valid_model.rename(cached)
    cached_result = ModelLocator(
        app_config.stt,
        environ={},
        handy_path=tmp_path / "handy",
        cache_root=cache_root,
    ).resolve()
    assert cached_result.source == "voiceflow-cache"
    for child in cached.iterdir():
        child.unlink()
    cached.rmdir()
    download = ModelLocator(
        app_config.stt,
        environ={},
        handy_path=tmp_path / "handy",
        cache_root=cache_root,
    ).resolve()
    assert download.path == cached
    assert download.needs_download


def test_corrupt_cache_is_repaired_only_when_download_is_enabled(
    app_config: AppConfig, tmp_path: Path
) -> None:
    cached = tmp_path / "cache" / "parakeet-tdt-0.6b-v3-int8"
    cached.mkdir(parents=True)
    (cached / "config.json").write_text("{}", encoding="utf-8")
    result = ModelLocator(
        app_config.stt,
        environ={},
        handy_path=tmp_path / "handy",
        cache_root=tmp_path / "cache",
    ).resolve()
    assert result.path == cached and result.needs_download
    assert result.source == "repair-download"
    app_config.stt.auto_download = False
    with pytest.raises(ModelValidationError, match="Invalid Parakeet"):
        ModelLocator(
            app_config.stt,
            environ={},
            handy_path=tmp_path / "handy",
            cache_root=tmp_path / "cache",
        ).resolve()


def test_wrong_model_type_is_rejected(valid_model: Path) -> None:
    (valid_model / "config.json").write_text('{"model_type":"whisper"}', encoding="utf-8")
    with pytest.raises(ModelValidationError, match="Unexpected model_type"):
        validate_model(valid_model)


@pytest.mark.parametrize("config", ["[]", '"model"', "42", "null"])
def test_non_object_model_config_is_rejected(valid_model: Path, config: str) -> None:
    (valid_model / "config.json").write_text(config, encoding="utf-8")
    with pytest.raises(ModelValidationError, match="expected a JSON object"):
        validate_model(valid_model)


@pytest.mark.parametrize(
    "config",
    [
        "{}",
        '{"model_type": null}',
        '{"model_type": 42}',
        '{"model_type": {"name": "nemo-conformer-tdt"}}',
    ],
)
def test_invalid_model_type_fields_are_rejected(valid_model: Path, config: str) -> None:
    (valid_model / "config.json").write_text(config, encoding="utf-8")
    with pytest.raises(ModelValidationError, match="Unexpected model_type"):
        validate_model(valid_model)
