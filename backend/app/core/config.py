from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal


StageName = Literal[
    "pass1",
    "document_guide",
    "page_guide_chunk",
    "semantic_guide",
    "document_synthesis",
    "pass2",
    "selection_explanation",
    "selection_follow_up",
]
ReasoningEffort = Literal["minimal", "low", "medium", "high", "xhigh"]
DocumentParserBackend = Literal["stub", "pymupdf4llm"]
Pass1Mode = Literal["parser_first", "legacy_llm", "hybrid"]
Pass1RoutingMode = Literal["legacy", "hybrid"]
PipelineMode = Literal["legacy", "hybrid", "v2_spine"]
V2SpineMode = Literal["off", "shadow", "active"]
Pass2ExecutionMode = Literal["all_pages", "hard_pages_only"]
LLMProvider = Literal["codex_cli", "openai_api", "mock"]
SemanticGuideMode = Literal["chunked_full_required", "legacy_single_call"]

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
    llm_provider: LLMProvider
    openai_api_key: str
    openai_timeout_seconds: int
    openai_max_retries: int
    codex_cli_bin: str
    codex_cli_timeout_seconds: int
    codex_cli_model: str | None
    codex_cli_reasoning_effort: ReasoningEffort
    precompute_anchored_explanations: bool
    schema_version: str
    parser_schema_version: str
    document_parser_backend: DocumentParserBackend
    pass1_mode: Pass1Mode
    pass1_routing_mode: Pass1RoutingMode
    pass1_max_workers: int
    pipeline_mode: PipelineMode
    v2_spine_mode: V2SpineMode
    pass2_execution_mode: Pass2ExecutionMode
    semantic_guide_mode: SemanticGuideMode
    semantic_guide_page_chunk_size: int
    semantic_guide_page_chunk_max_workers: int
    semantic_guide_retry_attempts: int
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
    "document_guide": {
        "model_env": "DOCUMENT_GUIDE_MODEL",
        "default_model": "gpt-5.5",
        "reasoning_effort": "medium",
        "timeout_seconds": 180,
        "prompt_env": "PROMPT_VERSION_DOCUMENT_GUIDE",
        "default_prompt_version": "document_guide_v0_1",
        "schema_name": "document_guide_result",
        "prompt_file": "document_guide_prompt.md",
    },
    "page_guide_chunk": {
        "model_env": "PAGE_GUIDE_CHUNK_MODEL",
        "default_model": "gpt-5.5",
        "reasoning_effort": "medium",
        "timeout_seconds": 180,
        "prompt_env": "PROMPT_VERSION_PAGE_GUIDE_CHUNK",
        "default_prompt_version": "page_guide_chunk_v0_1",
        "schema_name": "page_guide_chunk_result",
        "prompt_file": "page_guide_chunk_prompt.md",
    },
    "semantic_guide": {
        "model_env": "SEMANTIC_GUIDE_MODEL",
        "default_model": "gpt-5.5",
        "reasoning_effort": "medium",
        "timeout_seconds": 180,
        "prompt_env": "PROMPT_VERSION_SEMANTIC_GUIDE",
        "default_prompt_version": "semantic_guide_v0_2",
        "schema_name": "semantic_guide_result",
        "prompt_file": "semantic_guide_prompt.md",
    },
    "pass2": {
        "model_env": "OPENAI_MODEL_PASS2",
        "default_model": "gpt-5.4",
        "reasoning_effort": "medium",
        "timeout_seconds": 120,
        "prompt_env": "PROMPT_VERSION_PASS2",
        "default_prompt_version": "pass2_v0_2",
        "schema_name": "pass2_result",
        "prompt_file": "pass2_prompt.md",
    },
    "selection_explanation": {
        "model_env": "OPENAI_MODEL_SELECTION",
        "default_model": "gpt-5.5",
        "reasoning_effort": "medium",
        "timeout_seconds": 180,
        "prompt_env": "PROMPT_VERSION_SELECTION",
        "default_prompt_version": "selection_explanation_v0_2",
        "schema_name": "selection_explanation_result",
        "prompt_file": "selection_explanation_prompt.md",
    },
    "selection_follow_up": {
        "model_env": "OPENAI_MODEL_SELECTION_FOLLOW_UP",
        "default_model": "gpt-5.5",
        "reasoning_effort": "medium",
        "timeout_seconds": 180,
        "prompt_env": "PROMPT_VERSION_SELECTION_FOLLOW_UP",
        "default_prompt_version": "selection_follow_up_v0_1",
        "schema_name": "selection_follow_up_result",
        "prompt_file": "selection_follow_up_prompt.md",
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


def _load_pass1_mode() -> Pass1Mode:
    pass1_mode = os.getenv("PASS1_MODE", "parser_first").strip().lower()
    if pass1_mode in {"parser_first", "legacy_llm", "hybrid"}:
        return pass1_mode  # type: ignore[return-value]
    return "parser_first"


def _load_pass1_routing_mode() -> Pass1RoutingMode:
    routing_mode = os.getenv("PASS1_ROUTING_MODE", "hybrid").strip().lower()
    if routing_mode in {"legacy", "hybrid"}:
        return routing_mode  # type: ignore[return-value]
    return "hybrid"


def _load_pipeline_mode() -> PipelineMode:
    pipeline_mode = os.getenv("PIPELINE_MODE", "hybrid").strip().lower()
    if pipeline_mode in {"legacy", "hybrid", "v2_spine"}:
        return pipeline_mode  # type: ignore[return-value]
    return "hybrid"


def _load_v2_spine_mode() -> V2SpineMode:
    spine_mode = os.getenv("V2_SPINE_MODE", "shadow").strip().lower()
    if spine_mode in {"off", "shadow", "active"}:
        return spine_mode  # type: ignore[return-value]
    return "shadow"


def _load_pass2_execution_mode() -> Pass2ExecutionMode:
    execution_mode = os.getenv("PASS2_EXECUTION_MODE", "all_pages").strip().lower()
    if execution_mode in {"all_pages", "hard_pages_only"}:
        return execution_mode  # type: ignore[return-value]
    return "all_pages"


def _load_semantic_guide_mode() -> SemanticGuideMode:
    guide_mode = os.getenv("SEMANTIC_GUIDE_MODE", "chunked_full_required").strip().lower()
    if guide_mode in {"chunked_full_required", "legacy_single_call"}:
        return guide_mode  # type: ignore[return-value]
    return "chunked_full_required"


def _load_llm_provider() -> LLMProvider:
    provider = os.getenv("SCHOLIUM_LLM_PROVIDER", "codex_cli").strip().lower()
    if provider in {"codex_cli", "openai_api", "mock"}:
        return provider  # type: ignore[return-value]
    return "codex_cli"


def _load_bool_env(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    normalized_value = raw_value.strip().lower()
    if normalized_value in {"1", "true", "yes", "on"}:
        return True
    if normalized_value in {"0", "false", "no", "off"}:
        return False
    return default


def _load_positive_int_env(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value.strip())
    except ValueError:
        return default
    return max(1, value)


def _load_reasoning_effort_env(name: str, default: ReasoningEffort) -> ReasoningEffort:
    raw_value = os.getenv(name, default).strip().lower()
    if raw_value in {"minimal", "low", "medium", "high", "xhigh"}:
        return raw_value  # type: ignore[return-value]
    return default


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
        llm_provider=_load_llm_provider(),
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        openai_timeout_seconds=int(os.getenv("OPENAI_TIMEOUT_SECONDS", "60")),
        openai_max_retries=int(os.getenv("OPENAI_MAX_RETRIES", "2")),
        codex_cli_bin=os.getenv("CODEX_CLI_BIN", "codex"),
        codex_cli_timeout_seconds=int(os.getenv("CODEX_CLI_TIMEOUT_SECONDS", "300")),
        codex_cli_model=(os.getenv("CODEX_CLI_MODEL", "gpt-5.5") or "gpt-5.5").strip() or "gpt-5.5",
        codex_cli_reasoning_effort=_load_reasoning_effort_env(
            "CODEX_CLI_REASONING",
            _load_reasoning_effort_env("CODEX_CLI_REASONING_EFFORT", "medium"),
        ),
        precompute_anchored_explanations=_load_bool_env("SCHOLIUM_PRECOMPUTE_ANCHORED_EXPLANATIONS", False),
        schema_version=os.getenv("SCHEMA_VERSION", "0.2"),
        parser_schema_version=os.getenv("PARSER_SCHEMA_VERSION", "parser_v0_2"),
        document_parser_backend=_load_document_parser_backend(),
        pass1_mode=_load_pass1_mode(),
        pass1_routing_mode=_load_pass1_routing_mode(),
        pass1_max_workers=_load_positive_int_env("PASS1_MAX_WORKERS", 3),
        pipeline_mode=_load_pipeline_mode(),
        v2_spine_mode=_load_v2_spine_mode(),
        pass2_execution_mode=_load_pass2_execution_mode(),
        semantic_guide_mode=_load_semantic_guide_mode(),
        semantic_guide_page_chunk_size=_load_positive_int_env("SEMANTIC_GUIDE_PAGE_CHUNK_SIZE", 5),
        semantic_guide_page_chunk_max_workers=_load_positive_int_env(
            "SEMANTIC_GUIDE_PAGE_CHUNK_MAX_WORKERS",
            1,
        ),
        semantic_guide_retry_attempts=_load_positive_int_env("SEMANTIC_GUIDE_RETRY_ATTEMPTS", 1),
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
