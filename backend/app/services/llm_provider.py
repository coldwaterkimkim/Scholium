from __future__ import annotations

from app.core.config import AppSettings, get_settings
from app.services.analysis_client import AnalysisClient
from app.services.codex_cli_client import CodexCLIClient
from app.services.mock_analysis_client import MockAnalysisClient
from app.services.openai_client import OpenAIResponsesClient
from app.services.storage import StorageService, get_storage_service


def get_analysis_client(
    settings: AppSettings | None = None,
    storage: StorageService | None = None,
) -> AnalysisClient:
    resolved_settings = settings or get_settings()
    resolved_storage = storage or get_storage_service()
    if resolved_settings.llm_provider == "codex_cli":
        return CodexCLIClient(settings=resolved_settings, storage=resolved_storage)
    if resolved_settings.llm_provider == "openai_api":
        return OpenAIResponsesClient(settings=resolved_settings, storage=resolved_storage)
    if resolved_settings.llm_provider == "mock":
        return MockAnalysisClient(settings=resolved_settings)
    return CodexCLIClient(settings=resolved_settings, storage=resolved_storage)
