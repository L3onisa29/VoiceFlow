from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Literal

import pytest

from voiceflow.config import PostprocessConfig
from voiceflow.postprocess.factory import create_postprocessor

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_MODULES = {
    "voiceflow.postprocess.codex",
    "voiceflow.postprocess.ollama",
    "voiceflow.postprocess.none",
}
NATIVE_MODULES = {"AppKit", "AVFoundation", "CoreAudio", "Foundation", "objc", "sounddevice"}


def run_isolated(source: str) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, "-c", source],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


@pytest.mark.parametrize(
    "imports",
    [
        "import voiceflow.postprocess",
        "import voiceflow.postprocess.base\nimport voiceflow.postprocess.factory",
        "import voiceflow.postprocess.factory",
    ],
)
def test_import_orders_do_not_eagerly_import_providers(imports: str) -> None:
    result = run_isolated(
        f"""
import json
import sys
{imports}
providers = {sorted(PROVIDER_MODULES)!r}
native = {sorted(NATIVE_MODULES)!r}
print(json.dumps({{
    "loaded": [name for name in providers if name in sys.modules],
    "native_loaded": [name for name in native if name in sys.modules],
}}))
"""
    )
    assert result == {"loaded": [], "native_loaded": []}


@pytest.mark.parametrize(
    ("provider", "expected_module", "expected_type"),
    [
        ("codex", "voiceflow.postprocess.codex", "CodexPostProcessor"),
        ("ollama", "voiceflow.postprocess.ollama", "OllamaPostProcessor"),
        ("none", "voiceflow.postprocess.none", "NoPostProcessor"),
    ],
)
def test_factory_constructs_only_the_selected_provider(
    provider: str,
    expected_module: str,
    expected_type: str,
) -> None:
    result = run_isolated(
        f"""
import json
import sys
from voiceflow.config import PostprocessConfig
from voiceflow.postprocess.factory import create_postprocessor

processor = create_postprocessor(PostprocessConfig(provider={provider!r}))
providers = {sorted(PROVIDER_MODULES)!r}
native = {sorted(NATIVE_MODULES)!r}
print(json.dumps({{
    "loaded": [name for name in providers if name in sys.modules],
    "native_loaded": [name for name in native if name in sys.modules],
    "type": type(processor).__name__,
}}))
processor.close()
"""
    )
    assert result == {
        "loaded": [expected_module],
        "native_loaded": [],
        "type": expected_type,
    }


@pytest.mark.parametrize(
    ("provider", "expected_type"),
    [
        ("codex", "CodexPostProcessor"),
        ("ollama", "OllamaPostProcessor"),
        ("none", "NoPostProcessor"),
    ],
)
def test_factory_branches_are_covered_in_process(
    provider: Literal["codex", "ollama", "none"],
    expected_type: str,
) -> None:
    processor = create_postprocessor(PostprocessConfig(provider=provider))
    try:
        assert type(processor).__name__ == expected_type
    finally:
        processor.close()


def test_package_keeps_the_factory_reexport() -> None:
    result = run_isolated(
        """
import json
import voiceflow.postprocess as postprocess
from voiceflow.postprocess.factory import create_postprocessor
print(json.dumps({"same": postprocess.create_postprocessor is create_postprocessor}))
"""
    )
    assert result == {"same": True}


def test_factory_is_in_the_declared_pyright_scope() -> None:
    config = json.loads((ROOT / "pyrightconfig.json").read_text())
    assert "src/voiceflow/postprocess/factory.py" in config["include"]
