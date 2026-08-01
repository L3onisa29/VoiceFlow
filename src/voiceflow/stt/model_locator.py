from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from voiceflow.config import SttConfig

MODEL_FILES = (
    "config.json",
    "encoder-model.int8.onnx",
    "decoder_joint-model.int8.onnx",
    "vocab.txt",
)
EXPECTED_MODEL_TYPE = "nemo-conformer-tdt"
HANDY_MODEL_PATH = Path(
    "~/Library/Application Support/com.pais.handy/models/parakeet-tdt-0.6b-v3-int8"
).expanduser()
VOICEFLOW_MODEL_CACHE = Path("~/Library/Application Support/VoiceFlow/models").expanduser()


class ModelValidationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ModelResolution:
    path: Path
    source: str
    needs_download: bool = False


def validate_model(path: Path) -> None:
    if not path.is_dir():
        raise ModelValidationError(f"Model directory does not exist: {path}")
    missing = [name for name in MODEL_FILES if not (path / name).is_file()]
    empty = [
        name
        for name in MODEL_FILES
        if (path / name).is_file() and (path / name).stat().st_size == 0
    ]
    if missing or empty:
        details = []
        if missing:
            details.append(f"missing: {', '.join(missing)}")
        if empty:
            details.append(f"empty: {', '.join(empty)}")
        raise ModelValidationError(f"Invalid Parakeet model at {path} ({'; '.join(details)})")
    try:
        model_config = json.loads((path / "config.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelValidationError(f"Invalid model config.json at {path}: {exc}") from exc
    if not isinstance(model_config, dict):
        raise ModelValidationError(f"Invalid model config.json at {path}: expected a JSON object")
    if model_config.get("model_type") != EXPECTED_MODEL_TYPE:
        raise ModelValidationError(
            f"Unexpected model_type at {path}: {model_config.get('model_type')!r}; "
            f"expected {EXPECTED_MODEL_TYPE!r}"
        )


class ModelLocator:
    def __init__(
        self,
        config: SttConfig,
        *,
        environ: dict[str, str] | None = None,
        handy_path: Path = HANDY_MODEL_PATH,
        cache_root: Path = VOICEFLOW_MODEL_CACHE,
    ) -> None:
        self.config = config
        self.environ = os.environ if environ is None else environ
        self.handy_path = handy_path
        self.cache_root = cache_root

    def resolve(self, *, skip_handy: bool = False) -> ModelResolution:
        if self.config.model_path is not None:
            path = self.config.model_path.expanduser().resolve()
            validate_model(path)
            return ModelResolution(path, "config")

        env_path = self.environ.get("VOICEFLOW_MODEL_PATH")
        if env_path:
            path = Path(env_path).expanduser().resolve()
            validate_model(path)
            return ModelResolution(path, "environment")

        if self.config.reuse_handy_model and not skip_handy:
            try:
                validate_model(self.handy_path)
            except ModelValidationError:
                pass
            else:
                return ModelResolution(self.handy_path, "handy")

        cache_path = self.cache_root / "parakeet-tdt-0.6b-v3-int8"
        try:
            validate_model(cache_path)
        except ModelValidationError:
            if cache_path.exists():
                if not self.config.auto_download:
                    raise
                return ModelResolution(cache_path, "repair-download", needs_download=True)
        else:
            return ModelResolution(cache_path, "voiceflow-cache")

        if not self.config.auto_download:
            raise ModelValidationError(
                "Parakeet model was not found and stt.auto_download is disabled"
            )
        return ModelResolution(cache_path, "download", needs_download=True)
