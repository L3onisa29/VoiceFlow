from __future__ import annotations

import json
from pathlib import Path

import pytest

from voiceflow.config import AppConfig, load_config


@pytest.fixture
def app_config() -> AppConfig:
    return load_config(root=Path(__file__).resolve().parents[1], environ={})


@pytest.fixture
def valid_model(tmp_path: Path) -> Path:
    from voiceflow.stt.model_locator import MODEL_FILES

    path = tmp_path / "model"
    path.mkdir()
    for filename in MODEL_FILES:
        content = (
            json.dumps({"model_type": "nemo-conformer-tdt"})
            if filename == "config.json"
            else "data"
        )
        (path / filename).write_text(content, encoding="utf-8")
    return path
