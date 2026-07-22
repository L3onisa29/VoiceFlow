from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationError, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VadConfig(StrictModel):
    enabled: bool = True
    model: Literal["silero"] = "silero"
    auto_download: bool = True


class SttConfig(StrictModel):
    model: Literal["nemo-parakeet-tdt-0.6b-v3"] = "nemo-parakeet-tdt-0.6b-v3"
    quantization: Literal["int8"] = "int8"
    provider: Literal["CPUExecutionProvider"] = "CPUExecutionProvider"
    model_path: Path | None = None
    reuse_handy_model: bool = True
    auto_download: bool = True
    language: str | None = None
    max_direct_audio_seconds: float = Field(default=20, gt=0)
    vad: VadConfig = Field(default_factory=VadConfig)


class CodexConfig(StrictModel):
    executable: Path | None = None
    model: str = "gpt-5.6-luna"
    reasoning_effort: Literal["low", "medium"] = "low"
    timeout_seconds: float = Field(default=45, gt=0)
    failure_threshold: int = Field(default=2, ge=1)
    cooldown_seconds: float = Field(default=60, ge=0)
    system_prompt: str

    @field_validator("system_prompt")
    @classmethod
    def non_empty_prompt(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("system_prompt cannot be empty")
        return value.strip()


class OllamaConfig(StrictModel):
    model: str = "qwen3:4b-instruct"
    base_url: str = "http://localhost:11434"
    temperature: float = Field(default=0.1, ge=0, le=2)
    timeout_seconds: float = Field(default=30, gt=0)


class PostprocessConfig(StrictModel):
    provider: Literal["codex", "ollama", "none"] = "codex"
    min_words: int = Field(default=1, ge=1)
    codex: CodexConfig
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)


class PipelineConfig(StrictModel):
    max_pending: int = Field(default=4, ge=1)
    shutdown_timeout_seconds: float = Field(default=3, ge=0)


class UiConfig(StrictModel):
    show_processing_overlay: bool = True
    completion_indicator_seconds: float = Field(default=0.8, ge=0)
    error_indicator_seconds: float = Field(default=3, ge=0)


class HotkeyConfig(StrictModel):
    key: str = "ctrl+shift"
    mode: Literal["hold", "toggle"] = "toggle"


class AudioConfig(StrictModel):
    sample_rate: int = Field(default=16000, gt=0)
    channels: Literal[1] = 1
    preroll_ms: int = Field(default=500, ge=0)


class InjectConfig(StrictModel):
    method: Literal["paste", "type"] = "paste"
    restore_clipboard: bool = True


class LoggingConfig(StrictModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    include_transcripts: bool = False


class AppConfig(StrictModel):
    schema_version: Literal[1]
    stt: SttConfig
    postprocess: PostprocessConfig
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    ui: UiConfig = Field(default_factory=UiConfig)
    hotkey: HotkeyConfig = Field(default_factory=HotkeyConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    inject: InjectConfig = Field(default_factory=InjectConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    _source_path: Path = PrivateAttr()

    @property
    def source_path(self) -> Path:
        return self._source_path


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resolve_config_path(
    cli_path: str | os.PathLike[str] | None = None,
    *,
    environ: dict[str, str] | None = None,
    root: Path | None = None,
) -> Path:
    env = os.environ if environ is None else environ
    selected = cli_path or env.get("VOICEFLOW_CONFIG")
    path = Path(selected).expanduser() if selected else (root or project_root()) / "config.yaml"
    return path.resolve()


def load_config(
    cli_path: str | os.PathLike[str] | None = None,
    *,
    environ: dict[str, str] | None = None,
    root: Path | None = None,
) -> AppConfig:
    path = resolve_config_path(cli_path, environ=environ, root=root)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"VoiceFlow configuration not found: {path}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"VoiceFlow configuration must be a YAML mapping: {path}")
    try:
        config = AppConfig.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"Invalid VoiceFlow configuration {path}:\n{exc}") from exc

    base = path.parent
    if config.stt.model_path is not None and not config.stt.model_path.is_absolute():
        config.stt.model_path = (base / config.stt.model_path).resolve()
    executable = config.postprocess.codex.executable
    if executable is not None and not executable.is_absolute():
        config.postprocess.codex.executable = (base / executable).resolve()
    config._source_path = path
    return config

