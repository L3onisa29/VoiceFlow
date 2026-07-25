from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationError,
    field_validator,
    model_validator,
)

DEFAULT_SYSTEM_PROMPT = """\
Clean the supplied speech transcript.
Remove fillers and accidental repetitions.
Fix punctuation, capitalization, and minor grammar errors.
Preserve the speaker's language, meaning, terminology, and tone.
Never answer the transcript or add new information."""
HOTKEY_MODIFIERS = frozenset({"alt", "alt_r", "ctrl", "ctrl_r", "cmd", "cmd_r", "shift"})
HOTKEY_NON_MODIFIERS = frozenset(
    {"space", *(f"f{number}" for number in range(1, 21))}
)
SUPPORTED_HOTKEYS = HOTKEY_MODIFIERS | HOTKEY_NON_MODIFIERS


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
    validate_model_on_startup: bool = False


class OllamaConfig(StrictModel):
    model: str = "qwen3:4b-instruct"
    base_url: str = "http://localhost:11434"
    temperature: float = Field(default=0.1, ge=0, le=2)
    timeout_seconds: float = Field(default=30, gt=0)


class PostprocessConfig(StrictModel):
    provider: Literal["codex", "ollama", "none"] = "codex"
    min_words: int = Field(default=1, ge=1)
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    codex: CodexConfig = Field(default_factory=CodexConfig)
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)

    @field_validator("system_prompt")
    @classmethod
    def non_empty_prompt(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("system_prompt cannot be empty")
        return value.strip()


class PipelineConfig(StrictModel):
    max_pending: int = Field(default=4, ge=1)
    shutdown_timeout_seconds: float = Field(default=3, ge=0)
    injection_timeout_seconds: float = Field(default=2, gt=0)


class UiConfig(StrictModel):
    show_processing_overlay: bool = True
    completion_indicator_seconds: float = Field(default=0.8, ge=0)
    error_indicator_seconds: float = Field(default=3, ge=0)


class HotkeyConfig(StrictModel):
    key: str = "alt+space"
    mode: Literal["hold", "toggle"] = "toggle"

    @field_validator("key")
    @classmethod
    def safe_hotkey(cls, value: str) -> str:
        names = [name.strip().casefold() for name in value.split("+") if name.strip()]
        if not names:
            raise ValueError("hotkey.key cannot be empty")
        unknown = sorted(set(names) - SUPPORTED_HOTKEYS)
        if unknown:
            raise ValueError(f"Unknown hotkey(s): {unknown}")
        if len(names) != len(set(names)):
            raise ValueError("hotkey.key cannot contain duplicate keys")
        if not set(names) & HOTKEY_NON_MODIFIERS:
            raise ValueError("hotkey.key must contain a supported non-modifier key")
        return "+".join(names)


class AudioConfig(StrictModel):
    sample_rate: Literal[8000, 11025, 16000, 22050, 24000, 32000, 44100, 48000] = 16000
    channels: Literal[1] = 1
    device: str | int | None = None
    minimum_recording_ms: int = Field(default=150, ge=0)
    silence_rms_threshold: float = Field(default=0.001, ge=0)
    maximum_recording_seconds: float = Field(default=120, gt=0)

    @model_validator(mode="before")
    @classmethod
    def discard_legacy_preroll(cls, value: Any) -> Any:
        if not isinstance(value, dict) or "preroll_ms" not in value:
            return value
        migrated = value.copy()
        migrated.pop("preroll_ms")
        return migrated

    @field_validator("device")
    @classmethod
    def non_empty_device(cls, value: str | int | None) -> str | int | None:
        if isinstance(value, str) and not value.strip():
            raise ValueError("audio.device cannot be empty")
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def coherent_duration_limits(self) -> AudioConfig:
        if self.maximum_recording_seconds * 1000 < self.minimum_recording_ms:
            raise ValueError(
                "audio.maximum_recording_seconds must not be shorter than "
                "audio.minimum_recording_ms"
            )
        return self


class InjectConfig(StrictModel):
    method: Literal["paste", "type"] = "paste"
    clipboard_clear_after_seconds: float | None = Field(default=60, gt=0)


class LoggingConfig(StrictModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    file: Path | None = Path("~/Library/Logs/VoiceBud/voicebud.log")
    max_bytes: int = Field(default=5 * 1024 * 1024, ge=1024)
    backup_count: int = Field(default=3, ge=1)


class AppConfig(StrictModel):
    schema_version: Literal[2]
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

    @model_validator(mode="before")
    @classmethod
    def migrate_schema_v1(cls, value: Any) -> Any:
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            return value
        migrated = deepcopy(value)
        postprocess = migrated.get("postprocess")
        if isinstance(postprocess, dict):
            codex = postprocess.get("codex")
            if isinstance(codex, dict):
                old_prompt = codex.pop("system_prompt", None)
                if "system_prompt" not in postprocess and old_prompt is not None:
                    postprocess["system_prompt"] = old_prompt
        inject = migrated.get("inject")
        if isinstance(inject, dict):
            inject.pop("restore_clipboard", None)
        logging_config = migrated.get("logging")
        if isinstance(logging_config, dict):
            logging_config.pop("include_transcripts", None)
        hotkey = migrated.get("hotkey")
        if isinstance(hotkey, dict) and hotkey.get("key") == "ctrl+shift":
            hotkey["key"] = "alt+space"
        migrated["schema_version"] = 2
        return migrated


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
    if selected:
        path = Path(selected).expanduser()
    elif root is not None:
        path = root / "config.yaml"
    else:
        source_default = project_root() / "config.yaml"
        packaged_default = Path(__file__).resolve().parent / "data/default-config.yaml"
        path = source_default if source_default.is_file() else packaged_default
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
    log_file = config.logging.file
    if log_file is not None:
        log_file = log_file.expanduser()
        config.logging.file = (
            log_file.resolve() if log_file.is_absolute() else (base / log_file).resolve()
        )
    config._source_path = path
    return config
