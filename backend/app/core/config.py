from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal


StageName = Literal["pass1", "document_synthesis", "pass2"]
ReasoningEffort = Literal["minimal", "low", "medium", "high", "xhigh"]
DocumentParserBackend = Literal["stub", "pymupdf4llm"]
Pass1RoutingMode = Literal["legacy", "hybrid"]

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE_PATH = PROJECT_ROOT / ".env"


@dataclass(frozen=True)
class StageConfig:
    stage_name: StageName
    model_name: str
    reasoning_effort: ReasoningEffort
    timeout_seconds: int
    prompt_version: str
    schema_name: str
    prompt_path: Path


@dataclass(frozen=True)
class AppSettings:
    app_name: str
    openai_api_key: str
    openai_timeout_seconds: int
    openai_max_retries: int
    schema_version: str
    parser_schema_version: str
    document_parser_backend: DocumentParserBackend
    pass1_routing_mode: Pass1RoutingMode
    frontend_port: int
    backend_port: int
    document_db_path: str
    raw_pdfs_dir: str
    rendered_pages_dir: str
    analysis_dir: str
    logs_dir: str
    stage_configs: dict[StageName, StageConfig]

    def stage_config(self, stage: StageName) -> StageConfig:
        return self.stage_configs[stage]

    @property
    def has_openai_api_key(self) -> bool:
        return bool(self.openai_api_key.strip())


STAGE_DEFAULTS = {
    "pass1": {
        "model_env": "OPENAI_MODEL_PASS1",
        "default_model": "gpt-5.4",
        "reasoning_effort": "medium",
        "timeout_seconds": 60,
        "prompt_env": "PROMPT_VERSION_PASS1",
        "default_prompt_version": "pass1_v0_1",
        "schema_name": "pass1_result",
        "prompt_file": "pass1_prompt.md",
    },
    "document_synthesis": {
        "model_env": "OPENAI_MODEL_SYNTHESIS",
        "default_model": "gpt-5.4",
        "reasoning_effort": "medium",
        "timeout_seconds": 60,
        "prompt_env": "PROMPT_VERSION_SYNTHESIS",
        "default_prompt_version": "synthesis_v0_1",
        "schema_name": "document_synthesis_result",
        "prompt_file": "document_synthesis_prompt.md",
    },
    "pass2": {
        "model_env": "OPENAI_MODEL_PASS2",
        "default_model": "gpt-5.4",
        "reasoning_effort": "medium",
        "timeout_seconds": 120,
        "prompt_env": "PROMPT_VERSION_PASS2",
        "default_prompt_version": "pass2_v0_1",
        "schema_name": "pass2_result",
        "prompt_file": "pass2_prompt.md",
    },
}


def _strip_wrapping_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _load_document_parser_backend() -> DocumentParserBackend:
    backend = os.getenv("DOCUMENT_PARSER_BACKEND", "pymupdf4llm").strip().lower()
    if backend in {"stub", "pymupdf4llm"}:
        return backend  # type: ignore[return-value]
    return "pymupdf4llm"


def _load_pass1_routing_mode() -> Pass1RoutingMode:
    routing_mode = os.getenv("PASS1_ROUTING_MODE", "hybrid").strip().lower()
    if routing_mode in {"legacy", "hybrid"}:
        return routing_mode  # type: ignore[return-value]
    return "hybrid"


def _load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), _strip_wrapping_quotes(value.strip()))


def _load_stage_configs() -> dict[StageName, StageConfig]:
    prompt_dir = PROJECT_ROOT / "docs" / "prompts"
    stage_configs: dict[StageName, StageConfig] = {}

    for stage_name, default_config in STAGE_DEFAULTS.items():
        stage_configs[stage_name] = StageConfig(
            stage_name=stage_name,
            model_name=os.getenv(default_config["model_env"], default_config["default_model"]),
            reasoning_effort=default_config["reasoning_effort"],
            timeout_seconds=int(default_config["timeout_seconds"]),
            prompt_version=os.getenv(
                default_config["prompt_env"],
                default_config["default_prompt_version"],
            ),
            schema_name=default_config["schema_name"],
            prompt_path=prompt_dir / default_config["prompt_file"],
        )

    return stage_configs


def _build_settings() -> AppSettings:
    _load_env_file(ENV_FILE_PATH)

    return AppSettings(
        app_name="Scholium Backend",
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        openai_timeout_seconds=int(os.getenv("OPENAI_TIMEOUT_SECONDS", "60")),
        openai_max_retries=int(os.getenv("OPENAI_MAX_RETRIES", "2")),
        schema_version=os.getenv("SCHEMA_VERSION", "0.1"),
        parser_schema_version=os.getenv("PARSER_SCHEMA_VERSION", "parser_v0_2"),
        document_parser_backend=_load_document_parser_backend(),
        pass1_routing_mode=_load_pass1_routing_mode(),
        frontend_port=int(os.getenv("FRONTEND_PORT", "3000")),
        backend_port=int(os.getenv("BACKEND_PORT", "8000")),
        document_db_path=os.getenv("DOCUMENT_DB_PATH", "./data/scholium_dev.sqlite3"),
        raw_pdfs_dir=os.getenv("RAW_PDFS_DIR", "./data/raw_pdfs"),
        rendered_pages_dir=os.getenv("RENDERED_PAGES_DIR", "./data/rendered_pages"),
        analysis_dir=os.getenv("ANALYSIS_DIR", "./data/analysis"),
        logs_dir=os.getenv("LOGS_DIR", "./data/logs"),
        stage_configs=_load_stage_configs(),
    )


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    return _build_settings()
