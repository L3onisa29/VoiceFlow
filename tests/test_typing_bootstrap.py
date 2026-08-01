from __future__ import annotations

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INITIAL_PYRIGHT_SCOPE = [
    "src/voiceflow/processing.py",
    "src/voiceflow/state.py",
    "src/voiceflow/normalization/contracts.py",
    "src/voiceflow/transcription/contracts.py",
    "src/voiceflow/postprocess/router.py",
    "src/voiceflow/benchmarking.py",
    "src/voiceflow/feedback.py",
]


def test_pyright_is_locked_to_the_approved_initial_scope() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert "pyright>=1.1,<2" in project["dependency-groups"]["dev"]

    config = json.loads((ROOT / "pyrightconfig.json").read_text())
    assert config["pythonVersion"] == "3.12"
    assert config["typeCheckingMode"] == "standard"
    assert set(config) == {"pythonVersion", "typeCheckingMode", "include"}
    assert set(INITIAL_PYRIGHT_SCOPE).issubset(config["include"])


def test_ci_runs_the_locked_pyright_scope() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "uv run --locked pyright" in workflow
