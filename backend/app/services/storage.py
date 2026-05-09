from __future__ import annotations

import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, RLock
from typing import Callable, Sequence
from uuid import uuid4

from app.core.config import PROJECT_ROOT, AppSettings, get_settings
from app.models.document import (
    DocumentRecord,
    DocumentStatus,
    PageRecord,
    ProcessingStage,
    RenderStatus,
    StageStatus,
)
from app.models.parser import DocumentPageManifest, DocumentParseArtifact, PageParseArtifact
from app.models.pipeline_v2 import DocumentSpineArtifact, PageRoutingArtifact
from app.utils.validation import validate_payload


_UNSET = object()
_OPTIONAL_PASS1_META_KEYS = {"pass1_path", "route_label", "route_reason", "parser_source"}
_ALLOWED_PASS1_PATHS = {"parser_first", "hybrid_parser_first", "text-first", "multimodal", "escalated"}
_ALLOWED_ROUTE_LABELS = {"text-rich", "scan-like", "visual-rich"}
_ALLOWED_PIPELINE_MODES = {"legacy", "hybrid", "v2_spine"}
_ALLOWED_SPINE_MODES = {"off", "shadow", "active"}
_ALLOWED_SPINE_SHADOW_STATUSES = {"disabled", "skipped", "completed", "failed"}
_ALLOWED_SPINE_SHADOW_REASONS = {
    "disabled",
    "parse_unavailable",
    "manifest_unavailable",
    "builder_failed",
    "not_requested",
}
_ALLOWED_PASS2_GENERATION_MODES = {"llm", "compat"}
_ALLOWED_PASS2_EXECUTION_MODES = {"all_pages", "hard_pages_only"}
_ALLOWED_PASS2_PLANNER_STATUSES = {"disabled", "active", "fallback"}
_ALLOWED_PASS2_PLANNER_REASONS = {
    "not_requested",
    "routing_missing",
    "routing_invalid",
    "routing_coverage_mismatch",
    "compat_builder_failed_promoted",
}
_BENCHMARK_DURATION_FIELDS = {
    "total_processing_time_seconds",
    "upload_to_render_seconds",
    "upload_to_parser_map_ready_seconds",
    "upload_to_semantic_guide_ready_seconds",
    "upload_to_viewer_ready_seconds",
    "render_time_seconds",
    "parse_time_seconds",
    "triage_time_seconds",
    "spine_time_seconds",
    "pass1_time_seconds",
    "document_guide_time_seconds",
    "page_guide_chunks_time_seconds",
    "semantic_guide_time_seconds",
    "synthesis_time_seconds",
    "pass2_time_seconds",
}
_BENCHMARK_COUNT_FIELDS = {
    "rendered_pages",
    "hard_page_count",
    "page_element_count",
    "page_guide_count",
    "pass1_text_first_pages",
    "pass1_multimodal_pages",
    "pass1_escalated_pages",
    "pass1_parser_first_pages",
    "pass2_completed_pages",
    "pass2_failed_pages",
    "pass2_llm_count",
    "pass2_compat_count",
    "compat_promoted_to_llm_count",
    "openai_call_count_total",
    "openai_pass1_call_count",
    "openai_synthesis_call_count",
    "openai_pass2_call_count",
    "codex_cli_call_count",
    "codex_cli_document_guide_call_count",
    "codex_cli_page_guide_call_count",
    "codex_cli_pass1_call_count",
    "codex_cli_semantic_guide_call_count",
    "codex_cli_synthesis_call_count",
    "codex_cli_pass2_call_count",
    "codex_cli_selection_call_count",
    "codex_cli_follow_up_call_count",
    "codex_cli_error_count",
    "codex_cli_repair_count",
    "semantic_guide_completed_chunks",
    "semantic_guide_total_chunks",
    "semantic_guide_failed_chunks",
}
_OPENAI_CALL_COUNT_FIELDS_BY_STAGE = {
    "pass1": "openai_pass1_call_count",
    "document_synthesis": "openai_synthesis_call_count",
    "pass2": "openai_pass2_call_count",
}
_CODEX_CLI_CALL_COUNT_FIELDS_BY_STAGE = {
    "pass1": "codex_cli_pass1_call_count",
    "document_guide": "codex_cli_document_guide_call_count",
    "page_guide_chunk": "codex_cli_page_guide_call_count",
    "semantic_guide": "codex_cli_semantic_guide_call_count",
    "document_synthesis": "codex_cli_synthesis_call_count",
    "pass2": "codex_cli_pass2_call_count",
    "selection_explanation": "codex_cli_selection_call_count",
    "selection_follow_up": "codex_cli_follow_up_call_count",
}


class StorageService:
    def __init__(self, settings: AppSettings | None = None) -> None:
        self.settings = settings or get_settings()
        self.db_path = self._resolve_project_path(self.settings.document_db_path)
        self.raw_pdfs_dir = self._resolve_project_path(self.settings.raw_pdfs_dir)
        self.rendered_pages_dir = self._resolve_project_path(self.settings.rendered_pages_dir)
        self.analysis_dir = self._resolve_project_path(self.settings.analysis_dir)
        self.parsed_dir = self._resolve_project_path(os.getenv("PARSED_DIR", "./data/parsed"))
        self._benchmark_lock = RLock()
        self._benchmark_states: dict[str, dict[str, object]] = {}

    def init_storage(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.raw_pdfs_dir.mkdir(parents=True, exist_ok=True)
        self.rendered_pages_dir.mkdir(parents=True, exist_ok=True)
        self.analysis_dir.mkdir(parents=True, exist_ok=True)
        self.parsed_dir.mkdir(parents=True, exist_ok=True)

        with self._connect() as connection:
            document_status_values = self._enum_value_list(DocumentStatus)
            render_status_values = self._enum_value_list(RenderStatus)
            stage_status_values = self._enum_value_list(StageStatus)
            connection.executescript(
                f"""
                CREATE TABLE IF NOT EXISTS documents (
                    document_id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    original_path TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ({document_status_values})),
                    total_pages INTEGER NULL,
                    response_language TEXT NOT NULL DEFAULT 'ko',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    error_message TEXT NULL
                );

                CREATE TABLE IF NOT EXISTS pages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id TEXT NOT NULL,
                    page_number INTEGER NOT NULL,
                    image_path TEXT NOT NULL,
                    render_status TEXT NOT NULL CHECK (render_status IN ({render_status_values})),
                    width INTEGER NULL,
                    height INTEGER NULL,
                    pass1_status TEXT NULL CHECK (pass1_status IS NULL OR pass1_status IN ({stage_status_values})),
                    pass1_error_message TEXT NULL,
                    pass2_status TEXT NULL CHECK (pass2_status IS NULL OR pass2_status IN ({stage_status_values})),
                    pass2_error_message TEXT NULL,
                    FOREIGN KEY(document_id) REFERENCES documents(document_id) ON DELETE CASCADE,
                    UNIQUE(document_id, page_number)
                );
                """
            )
            self._ensure_document_columns(connection)
            self._ensure_pages_columns(connection)

    def save_uploaded_document(self, filename: str, file_bytes: bytes, response_language: str = "ko") -> DocumentRecord:
        if not filename:
            raise ValueError("PDF filename is required.")
        normalized_response_language = self._normalize_response_language(response_language)

        self.init_storage()

        existing_document = self.get_document_by_filename(filename)
        document_id = existing_document.document_id if existing_document else f"doc_{uuid4().hex}"
        original_file_path = self.raw_pdfs_dir / f"{document_id}.pdf"
        original_relative_path = self._stored_data_path(
            original_file_path,
            runtime_root=self.raw_pdfs_dir,
            stored_root=Path("data/raw_pdfs"),
        )

        if existing_document:
            self._clear_document_runtime_state(existing_document)

        original_file_path.write_bytes(file_bytes)

        timestamp = datetime.now(timezone.utc)
        document = DocumentRecord(
            document_id=document_id,
            filename=filename,
            original_path=original_relative_path,
            status=DocumentStatus.UPLOADED,
            total_pages=None,
            response_language=normalized_response_language,
            created_at=timestamp,
            updated_at=timestamp,
            error_message=None,
        )

        try:
            with self._connect() as connection:
                if existing_document:
                    connection.execute(
                        """
                        UPDATE documents
                        SET
                            filename = ?,
                            original_path = ?,
                            status = ?,
                            total_pages = ?,
                            response_language = ?,
                            created_at = ?,
                            updated_at = ?,
                            error_message = ?
                        WHERE document_id = ?
                        """,
                        (
                            document.filename,
                            document.original_path,
                            document.status.value,
                            document.total_pages,
                            document.response_language,
                            document.created_at.isoformat(),
                            document.updated_at.isoformat(),
                            document.error_message,
                            document.document_id,
                        ),
                    )
                else:
                    connection.execute(
                        """
                        INSERT INTO documents (
                            document_id,
                            filename,
                            original_path,
                            status,
                            total_pages,
                            response_language,
                            created_at,
                            updated_at,
                            error_message
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            document.document_id,
                            document.filename,
                            document.original_path,
                            document.status.value,
                            document.total_pages,
                            document.response_language,
                            document.created_at.isoformat(),
                            document.updated_at.isoformat(),
                            document.error_message,
                        ),
                    )
        except Exception:
            if original_file_path.exists():
                original_file_path.unlink()
            raise

        return document

    def get_document_by_filename(self, filename: str) -> DocumentRecord | None:
        self.init_storage()

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    document_id,
                    filename,
                    original_path,
                    status,
                    total_pages,
                    response_language,
                    created_at,
                    updated_at,
                    error_message
                FROM documents
                WHERE filename = ?
                ORDER BY updated_at DESC, created_at DESC
                LIMIT 1
                """,
                (filename,),
            ).fetchone()

        if row is None:
            return None

        return self._row_to_document(row)

    def get_document(self, document_id: str) -> DocumentRecord | None:
        self.init_storage()

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    document_id,
                    filename,
                    original_path,
                    status,
                    total_pages,
                    response_language,
                    created_at,
                    updated_at,
                    error_message
                FROM documents
                WHERE document_id = ?
                """,
                (document_id,),
            ).fetchone()

        if row is None:
            return None

        return self._row_to_document(row)

    def list_documents(self, *, limit: int = 50) -> list[DocumentRecord]:
        self.init_storage()

        safe_limit = max(1, min(int(limit), 200))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    document_id,
                    filename,
                    original_path,
                    status,
                    total_pages,
                    response_language,
                    created_at,
                    updated_at,
                    error_message
                FROM documents
                ORDER BY updated_at DESC, created_at DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()

        return [self._row_to_document(row) for row in rows]

    def delete_document(self, document_id: str) -> bool:
        self.init_storage()

        document = self.get_document(document_id)
        if document is None:
            return self.delete_orphan_document_state(document_id)

        with self._connect() as connection:
            connection.execute("DELETE FROM pages WHERE document_id = ?", (document_id,))
            self._delete_interaction_logs(connection, document_id)
            connection.execute("DELETE FROM documents WHERE document_id = ?", (document_id,))

        self._clear_document_runtime_state(document)
        return True

    def delete_orphan_document_state(self, document_id: str) -> bool:
        self.init_storage()

        if not self._is_safe_document_id(document_id):
            return False

        removed_anything = False
        with self._connect() as connection:
            removed_anything = self._delete_interaction_logs(connection, document_id) > 0

        for path, owner_dir in self._runtime_paths_for_document_id(document_id):
            if path.is_file():
                try:
                    path.resolve().relative_to(owner_dir.resolve())
                except ValueError:
                    continue
                path.unlink()
                removed_anything = True
            elif path.is_dir():
                before_exists = path.exists()
                self._remove_directory_if_owned(path, owner_dir)
                removed_anything = removed_anything or before_exists

        return removed_anything

    def prune_orphan_document_state(self) -> dict[str, object]:
        self.init_storage()

        with self._connect() as connection:
            live_document_ids = {
                str(row[0])
                for row in connection.execute("SELECT document_id FROM documents").fetchall()
            }

        with self._connect() as connection:
            orphan_log_counts = dict(self._orphan_interaction_log_counts(connection, live_document_ids))

        runtime_document_ids = self._collect_runtime_document_ids()
        removed_runtime_document_ids: list[str] = []
        removed_log_document_ids: list[str] = []
        removed_log_count = 0
        for document_id in sorted(runtime_document_ids - live_document_ids):
            if self.delete_orphan_document_state(document_id):
                removed_runtime_document_ids.append(document_id)
            deleted_log_count = orphan_log_counts.pop(document_id, 0)
            if deleted_log_count:
                removed_log_document_ids.append(document_id)
                removed_log_count += deleted_log_count

        with self._connect() as connection:
            for document_id, count in orphan_log_counts.items():
                removed = self._delete_interaction_logs(connection, document_id)
                if removed:
                    removed_log_document_ids.append(document_id)
                    removed_log_count += removed

        return {
            "removed_runtime_document_ids": removed_runtime_document_ids,
            "removed_log_document_ids": removed_log_document_ids,
            "removed_log_count": removed_log_count,
        }

    def update_document(
        self,
        document_id: str,
        *,
        status: DocumentStatus | None = None,
        total_pages: int | None | object = _UNSET,
        error_message: str | None | object = _UNSET,
    ) -> None:
        assignments: list[str] = []
        params: list[object] = []

        if status is not None:
            assignments.append("status = ?")
            params.append(status.value)
        if total_pages is not _UNSET:
            assignments.append("total_pages = ?")
            params.append(total_pages)
        if error_message is not _UNSET:
            assignments.append("error_message = ?")
            params.append(error_message)

        assignments.append("updated_at = ?")
        params.append(datetime.now(timezone.utc).isoformat())
        params.append(document_id)

        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE documents SET {', '.join(assignments)} WHERE document_id = ?",
                tuple(params),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"Document not found: {document_id}")

    def replace_pages(self, document_id: str, page_records: Sequence[PageRecord]) -> None:
        self.init_storage()

        with self._connect() as connection:
            connection.execute("DELETE FROM pages WHERE document_id = ?", (document_id,))
            connection.executemany(
                """
                INSERT INTO pages (
                    document_id,
                    page_number,
                    image_path,
                    render_status,
                    width,
                    height,
                    pass1_status,
                    pass1_error_message,
                    pass2_status,
                    pass2_error_message
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        page_record.document_id,
                        page_record.page_number,
                        page_record.image_path,
                        page_record.render_status.value,
                        page_record.width,
                        page_record.height,
                        page_record.pass1_status.value if page_record.pass1_status else None,
                        page_record.pass1_error_message,
                        page_record.pass2_status.value if page_record.pass2_status else None,
                        page_record.pass2_error_message,
                    )
                    for page_record in page_records
                ],
            )

    def get_pages(self, document_id: str) -> list[PageRecord]:
        self.init_storage()

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    id,
                    document_id,
                    page_number,
                    image_path,
                    render_status,
                    width,
                    height,
                    pass1_status,
                    pass1_error_message,
                    pass2_status,
                    pass2_error_message
                FROM pages
                WHERE document_id = ?
                ORDER BY page_number ASC
                """,
                (document_id,),
            ).fetchall()

        return [self._row_to_page(row) for row in rows]

    def get_page(self, document_id: str, page_number: int) -> PageRecord | None:
        self.init_storage()

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    id,
                    document_id,
                    page_number,
                    image_path,
                    render_status,
                    width,
                    height,
                    pass1_status,
                    pass1_error_message,
                    pass2_status,
                    pass2_error_message
                FROM pages
                WHERE document_id = ? AND page_number = ?
                """,
                (document_id, page_number),
            ).fetchone()

        if row is None:
            return None

        return self._row_to_page(row)

    def update_page_render(
        self,
        document_id: str,
        page_number: int,
        *,
        render_status: RenderStatus,
        width: int | None,
        height: int | None,
    ) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE pages
                SET render_status = ?, width = ?, height = ?
                WHERE document_id = ? AND page_number = ?
                """,
                (render_status.value, width, height, document_id, page_number),
            )
            if cursor.rowcount == 0:
                raise ValueError(
                    f"Page row not found for document_id={document_id}, page_number={page_number}",
                )

    def update_page_pass1_status(
        self,
        document_id: str,
        page_number: int,
        status: StageStatus | None,
        *,
        error_message: str | None | object = _UNSET,
    ) -> None:
        assignments = ["pass1_status = ?"]
        params: list[object] = [status.value if status else None]
        if error_message is not _UNSET:
            assignments.append("pass1_error_message = ?")
            params.append(error_message)
        params.extend([document_id, page_number])

        with self._connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE pages
                SET {", ".join(assignments)}
                WHERE document_id = ? AND page_number = ?
                """,
                tuple(params),
            )
            if cursor.rowcount == 0:
                raise ValueError(
                    f"Page row not found for document_id={document_id}, page_number={page_number}",
                )

    def update_page_pass2_status(
        self,
        document_id: str,
        page_number: int,
        status: StageStatus | None,
        *,
        error_message: str | None | object = _UNSET,
    ) -> None:
        assignments = ["pass2_status = ?"]
        params: list[object] = [status.value if status else None]
        if error_message is not _UNSET:
            assignments.append("pass2_error_message = ?")
            params.append(error_message)
        params.extend([document_id, page_number])

        with self._connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE pages
                SET {", ".join(assignments)}
                WHERE document_id = ? AND page_number = ?
                """,
                tuple(params),
            )
            if cursor.rowcount == 0:
                raise ValueError(
                    f"Page row not found for document_id={document_id}, page_number={page_number}",
                )

    def get_pass1_result_path(self, document_id: str, page_number: int) -> Path:
        return self.analysis_dir / document_id / "pages" / str(page_number) / "page_analysis_pass1.json"

    def save_pass1_result(
        self,
        document_id: str,
        page_number: int,
        payload: dict[str, object],
    ) -> str:
        target_path = self.get_pass1_result_path(document_id, page_number)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        normalized_payload = self._normalize_pass1_artifact(
            document_id,
            page_number,
            payload,
            include_page_elements_alias=False,
        )
        temp_path = target_path.parent / f".{target_path.name}.{uuid4().hex}.tmp"

        try:
            temp_path.write_text(
                json.dumps(normalized_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            loaded_payload = json.loads(temp_path.read_text(encoding="utf-8"))
            self._normalize_pass1_artifact(
                document_id,
                page_number,
                loaded_payload,
                include_page_elements_alias=False,
            )
            os.replace(temp_path, target_path)
        except Exception:
            if temp_path.exists():
                temp_path.unlink()
            raise

        return target_path.relative_to(PROJECT_ROOT).as_posix()

    def load_pass1_result(self, document_id: str, page_number: int) -> dict[str, object] | None:
        target_path = self.get_pass1_result_path(document_id, page_number)
        if not target_path.exists():
            return None

        payload = json.loads(target_path.read_text(encoding="utf-8"))
        return self._normalize_pass1_artifact(document_id, page_number, payload)

    def get_pass2_result_path(self, document_id: str, page_number: int) -> Path:
        return self.analysis_dir / document_id / "pages" / str(page_number) / "page_analysis_pass2.json"

    def save_pass2_result(
        self,
        document_id: str,
        page_number: int,
        payload: dict[str, object],
    ) -> str:
        target_path = self.get_pass2_result_path(document_id, page_number)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        normalized_payload = self._normalize_pass2_artifact(document_id, page_number, payload)
        temp_path = target_path.parent / f".{target_path.name}.{uuid4().hex}.tmp"

        try:
            temp_path.write_text(
                json.dumps(normalized_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            loaded_payload = json.loads(temp_path.read_text(encoding="utf-8"))
            self._normalize_pass2_artifact(document_id, page_number, loaded_payload)
            os.replace(temp_path, target_path)
        except Exception:
            if temp_path.exists():
                temp_path.unlink()
            raise

        return target_path.relative_to(PROJECT_ROOT).as_posix()

    def load_pass2_result(self, document_id: str, page_number: int) -> dict[str, object] | None:
        target_path = self.get_pass2_result_path(document_id, page_number)
        if not target_path.exists():
            return None

        payload = json.loads(target_path.read_text(encoding="utf-8"))
        return self._normalize_pass2_artifact(document_id, page_number, payload)

    def get_selection_explanation_path(
        self,
        document_id: str,
        page_number: int,
        selection_id: str,
    ) -> Path:
        if "/" in selection_id or "\\" in selection_id or ".." in selection_id:
            raise ValueError("selection_id contains invalid path characters.")
        return (
            self.analysis_dir
            / document_id
            / "pages"
            / str(page_number)
            / "selection_explanations"
            / f"{selection_id}.json"
        )

    def save_selection_explanation(
        self,
        document_id: str,
        page_number: int,
        selection_id: str,
        payload: dict[str, object],
    ) -> str:
        target_path = self.get_selection_explanation_path(document_id, page_number, selection_id)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        normalized_payload = self._normalize_selection_explanation_artifact(
            document_id,
            page_number,
            selection_id,
            payload,
        )
        temp_path = target_path.parent / f".{target_path.name}.{uuid4().hex}.tmp"

        try:
            temp_path.write_text(
                json.dumps(normalized_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            loaded_payload = json.loads(temp_path.read_text(encoding="utf-8"))
            self._normalize_selection_explanation_artifact(
                document_id,
                page_number,
                selection_id,
                loaded_payload,
            )
            os.replace(temp_path, target_path)
        except Exception:
            if temp_path.exists():
                temp_path.unlink()
            raise

        return target_path.relative_to(PROJECT_ROOT).as_posix()

    def load_selection_explanation(
        self,
        document_id: str,
        page_number: int,
        selection_id: str,
    ) -> dict[str, object] | None:
        target_path = self.get_selection_explanation_path(document_id, page_number, selection_id)
        if not target_path.exists():
            return None

        payload = json.loads(target_path.read_text(encoding="utf-8"))
        return self._normalize_selection_explanation_artifact(
            document_id,
            page_number,
            selection_id,
            payload,
        )

    def update_selection_explanation_state(
        self,
        document_id: str,
        page_number: int,
        selection_id: str,
        *,
        is_important: bool | None = None,
    ) -> dict[str, object]:
        artifact = self.load_selection_explanation(document_id, page_number, selection_id)
        if artifact is None:
            raise FileNotFoundError(f"Selection explanation not found: {selection_id}")

        meta = dict(artifact.get("meta") or {})
        viewer_state = self._selection_explanation_viewer_state(artifact)
        if is_important is not None:
            viewer_state["is_important"] = bool(is_important)

        meta["viewer_state"] = viewer_state
        self.save_selection_explanation(
            document_id,
            page_number,
            selection_id,
            {
                "meta": meta,
                "result": artifact["result"],
            },
        )
        return {
            "selection_id": selection_id,
            **viewer_state,
        }

    def delete_selection_explanation(
        self,
        document_id: str,
        page_number: int,
        selection_id: str,
    ) -> bool:
        target_path = self.get_selection_explanation_path(document_id, page_number, selection_id)
        if not target_path.exists():
            return False

        target_path.unlink()
        return True

    def list_selection_explanations(
        self,
        document_id: str,
        page_number: int,
    ) -> list[dict[str, object]]:
        target_dir = self.analysis_dir / document_id / "pages" / str(page_number) / "selection_explanations"
        if not target_dir.exists():
            return []

        artifacts: list[dict[str, object]] = []
        artifact_paths = sorted(target_dir.glob("*.json"), key=lambda path: path.stat().st_mtime)
        for artifact_path in artifact_paths:
            artifact = self.load_selection_explanation(document_id, page_number, artifact_path.stem)
            if artifact is not None:
                artifacts.append(
                    {
                        "explanation": dict(artifact["result"]),
                        **self._selection_explanation_viewer_state(artifact),
                    }
                )
        return artifacts

    def get_document_summary_path(self, document_id: str) -> Path:
        return self.analysis_dir / document_id / "document_summary.json"

    def save_document_summary(
        self,
        document_id: str,
        payload: dict[str, object],
    ) -> str:
        target_path = self.get_document_summary_path(document_id)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        normalized_payload = self._normalize_document_summary_artifact(document_id, payload)
        temp_path = target_path.parent / f".{target_path.name}.{uuid4().hex}.tmp"

        try:
            temp_path.write_text(
                json.dumps(normalized_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            loaded_payload = json.loads(temp_path.read_text(encoding="utf-8"))
            self._normalize_document_summary_artifact(document_id, loaded_payload)
            os.replace(temp_path, target_path)
        except Exception:
            if temp_path.exists():
                temp_path.unlink()
            raise

        return target_path.relative_to(PROJECT_ROOT).as_posix()

    def load_document_summary(self, document_id: str) -> dict[str, object] | None:
        target_path = self.get_document_summary_path(document_id)
        if not target_path.exists():
            return None

        payload = json.loads(target_path.read_text(encoding="utf-8"))
        return self._normalize_document_summary_artifact(document_id, payload)

    def get_semantic_guide_path(self, document_id: str) -> Path:
        return self.analysis_dir / document_id / "semantic_guide.json"

    def save_semantic_guide(
        self,
        document_id: str,
        payload: dict[str, object],
    ) -> str:
        target_path = self.get_semantic_guide_path(document_id)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        normalized_payload = self._normalize_semantic_guide_artifact(document_id, payload)
        self._write_validated_json_artifact(
            target_path,
            normalized_payload,
            lambda loaded_payload: self._normalize_semantic_guide_artifact(document_id, loaded_payload),
        )
        return target_path.relative_to(PROJECT_ROOT).as_posix()

    def load_semantic_guide(self, document_id: str) -> dict[str, object] | None:
        target_path = self.get_semantic_guide_path(document_id)
        if not target_path.exists():
            return None

        payload = json.loads(target_path.read_text(encoding="utf-8"))
        return self._normalize_semantic_guide_artifact(document_id, payload)

    def get_semantic_work_dir(self, document_id: str) -> Path:
        return self.analysis_dir / document_id / "semantic"

    def get_semantic_status_path(self, document_id: str) -> Path:
        return self.get_semantic_work_dir(document_id) / "status.json"

    def save_semantic_status(self, document_id: str, payload: dict[str, object]) -> str:
        target_path = self.get_semantic_status_path(document_id)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        normalized_payload = {
            "document_id": document_id,
            "semantic_guide_stage": str(payload.get("semantic_guide_stage") or "unknown"),
            "semantic_guide_completed_chunks": int(payload.get("semantic_guide_completed_chunks") or 0),
            "semantic_guide_total_chunks": int(payload.get("semantic_guide_total_chunks") or 0),
            "semantic_guide_failed_chunks": int(payload.get("semantic_guide_failed_chunks") or 0),
            "failed_chunk_ranges": list(payload.get("failed_chunk_ranges") or []),
            "updated_at": str(payload.get("updated_at") or datetime.now(timezone.utc).isoformat()),
        }
        self._write_validated_json_artifact(
            target_path,
            normalized_payload,
            lambda loaded_payload: dict(loaded_payload),
        )
        return target_path.relative_to(PROJECT_ROOT).as_posix()

    def load_semantic_status(self, document_id: str) -> dict[str, object] | None:
        target_path = self.get_semantic_status_path(document_id)
        if not target_path.exists():
            return None
        payload = json.loads(target_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        return dict(payload)

    def get_document_guide_path(self, document_id: str) -> Path:
        return self.get_semantic_work_dir(document_id) / "document_guide.json"

    def save_document_guide(self, document_id: str, payload: dict[str, object]) -> str:
        target_path = self.get_document_guide_path(document_id)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        normalized_payload = self._normalize_document_guide_artifact(document_id, payload)
        self._write_validated_json_artifact(
            target_path,
            normalized_payload,
            lambda loaded_payload: self._normalize_document_guide_artifact(document_id, loaded_payload),
        )
        return target_path.relative_to(PROJECT_ROOT).as_posix()

    def load_document_guide(self, document_id: str) -> dict[str, object] | None:
        target_path = self.get_document_guide_path(document_id)
        if not target_path.exists():
            return None
        payload = json.loads(target_path.read_text(encoding="utf-8"))
        return self._normalize_document_guide_artifact(document_id, payload)

    def get_page_guide_chunk_path(
        self,
        document_id: str,
        page_numbers: Sequence[int],
    ) -> Path:
        normalized_pages = sorted({int(page) for page in page_numbers})
        if not normalized_pages:
            raise ValueError("Page guide chunk path requires at least one page number.")
        filename = f"pages_{normalized_pages[0]:03d}_{normalized_pages[-1]:03d}.json"
        return self.get_semantic_work_dir(document_id) / "page_guide_chunks" / filename

    def save_page_guide_chunk(
        self,
        document_id: str,
        page_numbers: Sequence[int],
        payload: dict[str, object],
    ) -> str:
        target_path = self.get_page_guide_chunk_path(document_id, page_numbers)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        normalized_payload = self._normalize_page_guide_chunk_artifact(document_id, payload)
        self._write_validated_json_artifact(
            target_path,
            normalized_payload,
            lambda loaded_payload: self._normalize_page_guide_chunk_artifact(document_id, loaded_payload),
        )
        return target_path.relative_to(PROJECT_ROOT).as_posix()

    def load_page_guide_chunk(
        self,
        document_id: str,
        page_numbers: Sequence[int],
    ) -> dict[str, object] | None:
        target_path = self.get_page_guide_chunk_path(document_id, page_numbers)
        if not target_path.exists():
            return None
        payload = json.loads(target_path.read_text(encoding="utf-8"))
        return self._normalize_page_guide_chunk_artifact(document_id, payload)

    def get_page_context_path(self, document_id: str, page_number: int) -> Path:
        return self.analysis_dir / document_id / "pages" / str(page_number) / "page_context.json"

    def save_page_context(
        self,
        document_id: str,
        page_number: int,
        payload: dict[str, object],
    ) -> str:
        target_path = self.get_page_context_path(document_id, page_number)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        normalized_payload = self._normalize_page_context_artifact(document_id, page_number, payload)
        self._write_validated_json_artifact(
            target_path,
            normalized_payload,
            lambda loaded_payload: self._normalize_page_context_artifact(
                document_id,
                page_number,
                loaded_payload,
            ),
        )
        return target_path.relative_to(PROJECT_ROOT).as_posix()

    def load_page_context(self, document_id: str, page_number: int) -> dict[str, object] | None:
        target_path = self.get_page_context_path(document_id, page_number)
        if not target_path.exists():
            return None

        payload = json.loads(target_path.read_text(encoding="utf-8"))
        return self._normalize_page_context_artifact(document_id, page_number, payload)

    def get_parse_artifact_path(self, document_id: str) -> Path:
        return self.parsed_dir / document_id / "document_parse.json"

    def save_parse_artifact(
        self,
        document_id: str,
        payload: dict[str, object],
        *,
        materialize_page_mirrors: bool = False,
    ) -> str:
        target_path = self.get_parse_artifact_path(document_id)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        normalized_payload = self._normalize_parse_artifact(document_id, payload)
        self._write_validated_json_artifact(
            target_path,
            normalized_payload,
            lambda loaded_payload: self._normalize_parse_artifact(document_id, loaded_payload),
        )

        if materialize_page_mirrors:
            for page_payload in normalized_payload["pages"]:
                page_number = int(page_payload["page_number"])
                self.save_page_parse_artifact(
                    document_id,
                    page_number,
                    self._build_page_parse_artifact_payload(normalized_payload, page_number),
                )

        return target_path.relative_to(PROJECT_ROOT).as_posix()

    def load_parse_artifact(self, document_id: str) -> dict[str, object] | None:
        target_path = self.get_parse_artifact_path(document_id)
        if not target_path.exists():
            return None

        payload = json.loads(target_path.read_text(encoding="utf-8"))
        return self._normalize_parse_artifact(document_id, payload)

    def get_page_parse_artifact_path(self, document_id: str, page_number: int) -> Path:
        return self.parsed_dir / document_id / "pages" / f"{page_number}.json"

    def get_page_manifest_path(self, document_id: str) -> Path:
        return self.parsed_dir / document_id / "page_manifest.json"

    def get_document_spine_path(self, document_id: str) -> Path:
        return self.analysis_dir / document_id / "document_spine.json"

    def get_page_routing_path(self, document_id: str) -> Path:
        return self.analysis_dir / document_id / "page_routing.json"

    def save_page_parse_artifact(
        self,
        document_id: str,
        page_number: int,
        payload: dict[str, object],
    ) -> str:
        target_path = self.get_page_parse_artifact_path(document_id, page_number)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        normalized_payload = self._normalize_page_parse_artifact(document_id, page_number, payload)
        self._write_validated_json_artifact(
            target_path,
            normalized_payload,
            lambda loaded_payload: self._normalize_page_parse_artifact(
                document_id,
                page_number,
                loaded_payload,
            ),
        )
        return target_path.relative_to(PROJECT_ROOT).as_posix()

    def load_page_parse_artifact(
        self,
        document_id: str,
        page_number: int,
        *,
        materialize_if_missing: bool = False,
    ) -> dict[str, object] | None:
        target_path = self.get_page_parse_artifact_path(document_id, page_number)
        if target_path.exists():
            payload = json.loads(target_path.read_text(encoding="utf-8"))
            return self._normalize_page_parse_artifact(document_id, page_number, payload)

        document_payload = self.load_parse_artifact(document_id)
        if document_payload is None:
            return None

        page_payload = self._build_page_parse_artifact_payload(document_payload, page_number)
        if page_payload is None:
            return None

        if materialize_if_missing:
            self.save_page_parse_artifact(document_id, page_number, page_payload)

        return page_payload

    def save_page_manifest(
        self,
        document_id: str,
        payload: dict[str, object],
    ) -> str:
        target_path = self.get_page_manifest_path(document_id)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        normalized_payload = self._normalize_page_manifest(document_id, payload)
        self._write_validated_json_artifact(
            target_path,
            normalized_payload,
            lambda loaded_payload: self._normalize_page_manifest(document_id, loaded_payload),
        )
        return target_path.relative_to(PROJECT_ROOT).as_posix()

    def load_page_manifest(self, document_id: str) -> dict[str, object] | None:
        target_path = self.get_page_manifest_path(document_id)
        if not target_path.exists():
            return None

        payload = json.loads(target_path.read_text(encoding="utf-8"))
        return self._normalize_page_manifest(document_id, payload)

    def save_document_spine(
        self,
        document_id: str,
        payload: dict[str, object],
    ) -> str:
        target_path = self.get_document_spine_path(document_id)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        normalized_payload = self._normalize_document_spine(document_id, payload)
        self._write_validated_json_artifact(
            target_path,
            normalized_payload,
            lambda loaded_payload: self._normalize_document_spine(document_id, loaded_payload),
        )
        return target_path.relative_to(PROJECT_ROOT).as_posix()

    def load_document_spine(self, document_id: str) -> dict[str, object] | None:
        target_path = self.get_document_spine_path(document_id)
        if not target_path.exists():
            return None

        payload = json.loads(target_path.read_text(encoding="utf-8"))
        return self._normalize_document_spine(document_id, payload)

    def save_page_routing(
        self,
        document_id: str,
        payload: dict[str, object],
    ) -> str:
        target_path = self.get_page_routing_path(document_id)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        normalized_payload = self._normalize_page_routing(document_id, payload)
        self._write_validated_json_artifact(
            target_path,
            normalized_payload,
            lambda loaded_payload: self._normalize_page_routing(document_id, loaded_payload),
        )
        return target_path.relative_to(PROJECT_ROOT).as_posix()

    def load_page_routing(self, document_id: str) -> dict[str, object] | None:
        target_path = self.get_page_routing_path(document_id)
        if not target_path.exists():
            return None

        payload = json.loads(target_path.read_text(encoding="utf-8"))
        return self._normalize_page_routing(document_id, payload)

    def get_processing_benchmark_path(self, document_id: str) -> Path:
        return self.analysis_dir / document_id / "processing_benchmark.json"

    def save_processing_benchmark(
        self,
        document_id: str,
        payload: dict[str, object],
    ) -> str:
        target_path = self.get_processing_benchmark_path(document_id)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        normalized_payload = self._normalize_processing_benchmark(document_id, payload)
        self._write_validated_json_artifact(
            target_path,
            normalized_payload,
            lambda loaded_payload: self._normalize_processing_benchmark(document_id, loaded_payload),
        )
        return target_path.relative_to(PROJECT_ROOT).as_posix()

    def load_processing_benchmark(self, document_id: str) -> dict[str, object] | None:
        target_path = self.get_processing_benchmark_path(document_id)
        if not target_path.exists():
            return None

        payload = json.loads(target_path.read_text(encoding="utf-8"))
        return self._normalize_processing_benchmark(document_id, payload)

    def start_processing_benchmark(
        self,
        document_id: str,
        initial_payload: dict[str, object],
    ) -> None:
        with self._benchmark_lock:
            state = self._benchmark_defaults(document_id)
            state.update(dict(initial_payload))
            state["document_id"] = document_id
            self._benchmark_states[document_id] = state

    def record_stage_duration(
        self,
        document_id: str,
        field_name: str,
        seconds: float,
    ) -> None:
        if field_name not in _BENCHMARK_DURATION_FIELDS:
            raise ValueError(f"Unknown benchmark duration field: {field_name}")

        with self._benchmark_lock:
            state = self._benchmark_states.get(document_id)
            if state is None:
                return
            state[field_name] = max(0.0, round(float(seconds), 4))

    def update_processing_benchmark_state(
        self,
        document_id: str,
        payload: dict[str, object],
    ) -> None:
        with self._benchmark_lock:
            state = self._benchmark_states.get(document_id)
            if state is None:
                return
            state.update(dict(payload))

    def record_pass1_path_counts(
        self,
        document_id: str,
        pass1_summary: dict[str, object],
    ) -> None:
        with self._benchmark_lock:
            state = self._benchmark_states.get(document_id)
            if state is None:
                return
            state["pass1_parser_first_pages"] = len(pass1_summary.get("parser_first_pages", []))
            state["pass1_text_first_pages"] = len(pass1_summary.get("text_first_pages", []))
            state["pass1_multimodal_pages"] = len(pass1_summary.get("multimodal_pages", []))
            state["pass1_escalated_pages"] = len(pass1_summary.get("escalated_pages", []))
            if pass1_summary.get("page_element_count") is not None:
                state["page_element_count"] = int(pass1_summary.get("page_element_count") or 0)

    def record_pass2_counts(
        self,
        document_id: str,
        pass2_summary: dict[str, object],
    ) -> None:
        with self._benchmark_lock:
            state = self._benchmark_states.get(document_id)
            if state is None:
                return
            state["pass2_completed_pages"] = len(pass2_summary.get("completed_pages", []))
            state["pass2_failed_pages"] = len(pass2_summary.get("failed_pages", []))
            pass2_llm_pages = self._normalize_sorted_unique_int_list(pass2_summary.get("llm_pages", []))
            pass2_compat_pages = self._normalize_sorted_unique_int_list(
                pass2_summary.get("compat_pages", [])
            )
            pass2_selected_pages = self._normalize_sorted_unique_int_list(
                pass2_summary.get("selected_pages", [])
            )
            pass2_skipped_llm_pages = self._normalize_sorted_unique_int_list(
                pass2_summary.get("skipped_llm_pages", [])
            )
            compat_promoted_to_llm_pages = self._normalize_sorted_unique_int_list(
                pass2_summary.get("compat_promoted_to_llm_pages", [])
            )

            state["pass2_execution_mode"] = str(
                pass2_summary.get("pass2_execution_mode", state.get("pass2_execution_mode", "all_pages"))
            )
            state["pass2_llm_pages"] = pass2_llm_pages
            state["pass2_compat_pages"] = pass2_compat_pages
            state["pass2_llm_count"] = len(pass2_llm_pages)
            state["pass2_compat_count"] = len(pass2_compat_pages)
            state["pass2_selected_pages"] = pass2_selected_pages
            state["pass2_skipped_llm_pages"] = pass2_skipped_llm_pages
            state["compat_promoted_to_llm_pages"] = compat_promoted_to_llm_pages
            state["compat_promoted_to_llm_count"] = len(compat_promoted_to_llm_pages)
            state["pass2_planner_status"] = str(
                pass2_summary.get(
                    "pass2_planner_status",
                    state.get("pass2_planner_status", "disabled"),
                )
            )
            state["pass2_planner_reason"] = pass2_summary.get(
                "pass2_planner_reason",
                state.get("pass2_planner_reason"),
            )

    def increment_openai_call_count(self, document_id: str, stage_name: str) -> None:
        counter_field = _OPENAI_CALL_COUNT_FIELDS_BY_STAGE.get(stage_name)
        if counter_field is None:
            return

        with self._benchmark_lock:
            state = self._benchmark_states.get(document_id)
            if state is None:
                return
            state[counter_field] = int(state.get(counter_field, 0)) + 1

    def increment_codex_cli_call_count(self, document_id: str, stage_name: str) -> None:
        counter_field = _CODEX_CLI_CALL_COUNT_FIELDS_BY_STAGE.get(stage_name)
        if counter_field is None:
            return

        with self._benchmark_lock:
            state = self._benchmark_states.get(document_id)
            if state is None:
                return
            state[counter_field] = int(state.get(counter_field, 0)) + 1
            state["codex_cli_call_count"] = int(state.get("codex_cli_call_count", 0)) + 1

    def increment_codex_cli_error_count(self, document_id: str) -> None:
        with self._benchmark_lock:
            state = self._benchmark_states.get(document_id)
            if state is None:
                return
            state["codex_cli_error_count"] = int(state.get("codex_cli_error_count", 0)) + 1

    def increment_codex_cli_repair_count(self, document_id: str, stage_name: str) -> None:
        if stage_name not in _CODEX_CLI_CALL_COUNT_FIELDS_BY_STAGE:
            return

        with self._benchmark_lock:
            state = self._benchmark_states.get(document_id)
            if state is None:
                return
            state["codex_cli_repair_count"] = int(state.get("codex_cli_repair_count", 0)) + 1

    def finalize_processing_benchmark(
        self,
        document_id: str,
        final_payload: dict[str, object],
    ) -> str:
        with self._benchmark_lock:
            state = dict(self._benchmark_states.get(document_id, self._benchmark_defaults(document_id)))
            state.update(dict(final_payload))
            state["document_id"] = document_id
            state["generated_at"] = datetime.now(timezone.utc).isoformat()

        try:
            saved_path = self.save_processing_benchmark(document_id, state)
        finally:
            with self._benchmark_lock:
                self._benchmark_states.pop(document_id, None)

        return saved_path

    def get_document_processing_snapshot(
        self,
        document_id: str,
        *,
        current_stage: ProcessingStage | None = None,
    ) -> dict[str, object] | None:
        document = self.get_document(document_id)
        if document is None:
            return None

        pages = self.get_pages(document_id)
        total_pages = document.total_pages
        rendered_pages = sum(1 for page in pages if page.render_status is RenderStatus.RENDERED)
        pass1_completed_pages = sum(1 for page in pages if page.pass1_status is StageStatus.COMPLETED)
        pass1_failed_page_numbers = {
            page.page_number for page in pages if page.pass1_status is StageStatus.FAILED
        }
        pass1_failed_pages = len(pass1_failed_page_numbers)
        pass1_processed_pages = pass1_completed_pages + pass1_failed_pages
        pass2_completed_pages = sum(1 for page in pages if page.pass2_status is StageStatus.COMPLETED)
        pass2_failed_page_numbers = {
            page.page_number for page in pages if page.pass2_status is StageStatus.FAILED
        }
        render_failed_page_numbers = {
            page.page_number for page in pages if page.render_status is RenderStatus.FAILED
        }
        failed_page_numbers = (
            render_failed_page_numbers | pass1_failed_page_numbers | pass2_failed_page_numbers
        )
        failed_page_count = len(failed_page_numbers)
        completed_page_count = (
            pass2_completed_pages
            if self.settings.precompute_anchored_explanations
            else pass1_completed_pages
        )
        completion_ratio = (
            round(completed_page_count / total_pages, 4)
            if total_pages and total_pages > 0
            else 0.0
        )

        synthesis_ready, summary_error_message = self._get_document_summary_health(document_id)
        semantic_guide_ready, semantic_guide_error_message = self._get_semantic_guide_health(document_id)
        semantic_status = self.load_semantic_status(document_id) or {}
        page_guide_count = 0
        try:
            semantic_artifact = self.load_semantic_guide(document_id)
            if semantic_artifact is not None and isinstance(semantic_artifact.get("result"), dict):
                page_guides = semantic_artifact["result"].get("page_guides", [])
                if isinstance(page_guides, list):
                    page_guide_count = len(page_guides)
        except ValueError:
            page_guide_count = 0
        error_message = document.error_message
        if (
            not error_message
            and document.status in {DocumentStatus.COMPLETED, DocumentStatus.FAILED}
            and summary_error_message is not None
        ):
            error_message = summary_error_message
        if (
            not error_message
            and document.status in {DocumentStatus.COMPLETED, DocumentStatus.FAILED}
            and semantic_guide_error_message is not None
        ):
            error_message = semantic_guide_error_message

        has_errors = failed_page_count > 0 or bool(error_message)
        render_ready_for_viewer = bool(total_pages and total_pages > 0 and rendered_pages == total_pages)
        page_context_ready_pages = pass1_completed_pages
        document_context_ready = synthesis_ready
        ready_for_viewer = (
            render_ready_for_viewer
            and page_context_ready_pages == rendered_pages
            and document_context_ready
            and semantic_guide_ready
            and page_guide_count == rendered_pages
        )
        resolved_stage = current_stage or self._derive_processing_stage(
            status=document.status,
            rendered_pages=rendered_pages,
            pass1_completed_pages=pass1_completed_pages,
            pass1_failed_page_numbers=pass1_failed_page_numbers,
            pass2_completed_pages=pass2_completed_pages,
            pass2_failed_page_numbers=pass2_failed_page_numbers,
            synthesis_ready=synthesis_ready,
            error_message=error_message,
        )
        current_page_number = self._get_current_page_number(
            pages=pages,
            stage=resolved_stage,
            status=document.status,
        )
        recent_failures = self._build_recent_failures(pages)

        return {
            "document_id": document.document_id,
            "status": document.status,
            "stage": resolved_stage,
            "current_stage": resolved_stage,
            "total_pages": total_pages,
            "rendered_pages": rendered_pages,
            "pass1_completed_pages": pass1_completed_pages,
            "pass1_failed_pages": pass1_failed_pages,
            "pass1_processed_pages": pass1_processed_pages,
            "synthesis_ready": synthesis_ready,
            "semantic_guide_ready": semantic_guide_ready,
            "pass2_completed_pages": pass2_completed_pages,
            "pass2_failed_pages": len(pass2_failed_page_numbers),
            "render_ready_for_viewer": render_ready_for_viewer,
            "page_context_ready_pages": page_context_ready_pages,
            "parser_map_ready_pages": page_context_ready_pages,
            "document_context_ready": document_context_ready,
            "viewer_ready": ready_for_viewer,
            "ready_for_viewer": ready_for_viewer,
            "page_guide_count": page_guide_count,
            "semantic_guide_stage": str(semantic_status.get("semantic_guide_stage") or "not_started"),
            "semantic_guide_completed_chunks": int(
                semantic_status.get("semantic_guide_completed_chunks") or 0
            ),
            "semantic_guide_total_chunks": int(
                semantic_status.get("semantic_guide_total_chunks") or 0
            ),
            "semantic_guide_failed_chunks": int(
                semantic_status.get("semantic_guide_failed_chunks") or 0
            ),
            "current_page_number": current_page_number,
            "error_message": error_message,
            "has_errors": has_errors,
            "failed_page_count": failed_page_count,
            "completed_page_count": completed_page_count,
            "completion_ratio": completion_ratio,
            "recent_failures": recent_failures,
        }

    def resolve_relative_path(self, relative_path: str) -> Path:
        stored_path = Path(relative_path)
        if stored_path.is_absolute():
            return stored_path.resolve()

        for stored_root, runtime_root in (
            (Path("data/raw_pdfs"), self.raw_pdfs_dir),
            (Path("data/rendered_pages"), self.rendered_pages_dir),
            (Path("data/analysis"), self.analysis_dir),
            (Path("data/parsed"), self.parsed_dir),
            (Path("data/logs"), self._resolve_project_path(self.settings.logs_dir)),
        ):
            try:
                suffix = stored_path.relative_to(stored_root)
            except ValueError:
                continue
            return (runtime_root / suffix).resolve()

        return self._resolve_project_path(relative_path)

    def get_rendered_image_subpath(self, image_path: str) -> str:
        resolved_image_path = self.resolve_relative_path(image_path)
        try:
            return resolved_image_path.relative_to(self.rendered_pages_dir).as_posix()
        except ValueError as exc:
            raise ValueError("Rendered page image is outside the rendered_pages directory.") from exc

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _resolve_project_path(self, configured_path: str) -> Path:
        configured = Path(configured_path)
        if configured.is_absolute():
            return configured.resolve()
        return (PROJECT_ROOT / configured).resolve()

    def _clear_document_runtime_state(self, document: DocumentRecord) -> None:
        with self._benchmark_lock:
            self._benchmark_states.pop(document.document_id, None)

        with self._connect() as connection:
            connection.execute("DELETE FROM pages WHERE document_id = ?", (document.document_id,))
            self._delete_interaction_logs(connection, document.document_id)

        self._remove_file_if_owned(document.original_path, self.raw_pdfs_dir)
        for directory, owner_dir in (
            (self.rendered_pages_dir / document.document_id, self.rendered_pages_dir),
            (self.analysis_dir / document.document_id, self.analysis_dir),
            (self.parsed_dir / document.document_id, self.parsed_dir),
        ):
            self._remove_directory_if_owned(directory, owner_dir)

    def _runtime_paths_for_document_id(self, document_id: str) -> list[tuple[Path, Path]]:
        return [
            (self.raw_pdfs_dir / f"{document_id}.pdf", self.raw_pdfs_dir),
            (self.rendered_pages_dir / document_id, self.rendered_pages_dir),
            (self.analysis_dir / document_id, self.analysis_dir),
            (self.parsed_dir / document_id, self.parsed_dir),
        ]

    def _collect_runtime_document_ids(self) -> set[str]:
        document_ids: set[str] = set()
        for directory in (self.rendered_pages_dir, self.analysis_dir, self.parsed_dir):
            if not directory.exists():
                continue
            for child in directory.iterdir():
                if child.is_dir() and self._is_safe_document_id(child.name):
                    document_ids.add(child.name)

        if self.raw_pdfs_dir.exists():
            for pdf_path in self.raw_pdfs_dir.glob("doc_*.pdf"):
                document_id = pdf_path.stem
                if self._is_safe_document_id(document_id):
                    document_ids.add(document_id)

        return document_ids

    def _delete_interaction_logs(self, connection: sqlite3.Connection, document_id: str) -> int:
        if not self._table_exists(connection, "interaction_logs"):
            return 0
        cursor = connection.execute(
            "DELETE FROM interaction_logs WHERE document_id = ?",
            (document_id,),
        )
        return int(cursor.rowcount if cursor.rowcount is not None else 0)

    def _orphan_interaction_log_counts(
        self,
        connection: sqlite3.Connection,
        live_document_ids: set[str],
    ) -> list[tuple[str, int]]:
        if not self._table_exists(connection, "interaction_logs"):
            return []

        rows = connection.execute(
            """
            SELECT document_id, COUNT(*)
            FROM interaction_logs
            GROUP BY document_id
            """
        ).fetchall()
        return [
            (str(row[0]), int(row[1]))
            for row in rows
            if self._is_safe_document_id(str(row[0])) and str(row[0]) not in live_document_ids
        ]

    def _table_exists(self, connection: sqlite3.Connection, table_name: str) -> bool:
        row = connection.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = ?
            LIMIT 1
            """,
            (table_name,),
        ).fetchone()
        return row is not None

    def _is_safe_document_id(self, document_id: str) -> bool:
        if not document_id.startswith("doc_"):
            return False
        if "/" in document_id or "\\" in document_id or ".." in document_id:
            return False
        return all(character.isalnum() or character == "_" for character in document_id)

    def _remove_file_if_owned(self, relative_path: str, owner_dir: Path) -> None:
        target_path = self.resolve_relative_path(relative_path)
        try:
            target_path.relative_to(owner_dir)
        except ValueError:
            return

        if target_path.exists() and target_path.is_file():
            target_path.unlink()

    def _remove_directory_if_owned(self, directory: Path, owner_dir: Path) -> None:
        try:
            directory.resolve().relative_to(owner_dir.resolve())
        except ValueError:
            return

        if directory.exists() and directory.is_dir():
            shutil.rmtree(directory)

    def _stored_data_path(self, path: Path, *, runtime_root: Path, stored_root: Path) -> str:
        try:
            return path.relative_to(PROJECT_ROOT).as_posix()
        except ValueError:
            suffix = path.resolve().relative_to(runtime_root.resolve())
            return (stored_root / suffix).as_posix()

    def _enum_value_list(self, enum_type: type[DocumentStatus] | type[RenderStatus] | type[StageStatus]) -> str:
        return ", ".join(f"'{member.value}'" for member in enum_type)

    def _normalize_response_language(self, value: object) -> str:
        return "en" if str(value or "").strip().lower() == "en" else "ko"

    def _row_to_document(self, row: tuple[object, ...]) -> DocumentRecord:
        return DocumentRecord(
            document_id=str(row[0]),
            filename=str(row[1]),
            original_path=str(row[2]),
            status=DocumentStatus(str(row[3])),
            total_pages=int(row[4]) if row[4] is not None else None,
            response_language=self._normalize_response_language(row[5]),
            created_at=datetime.fromisoformat(str(row[6])),
            updated_at=datetime.fromisoformat(str(row[7])),
            error_message=str(row[8]) if row[8] is not None else None,
        )

    def _row_to_page(self, row: tuple[object, ...]) -> PageRecord:
        return PageRecord(
            id=int(row[0]) if row[0] is not None else None,
            document_id=str(row[1]),
            page_number=int(row[2]),
            image_path=str(row[3]),
            render_status=RenderStatus(str(row[4])),
            width=int(row[5]) if row[5] is not None else None,
            height=int(row[6]) if row[6] is not None else None,
            pass1_status=StageStatus(str(row[7])) if row[7] is not None else None,
            pass1_error_message=str(row[8]) if row[8] is not None else None,
            pass2_status=StageStatus(str(row[9])) if row[9] is not None else None,
            pass2_error_message=str(row[10]) if row[10] is not None else None,
        )

    def _normalize_pass1_artifact(
        self,
        document_id: str,
        page_number: int,
        payload: dict[str, object],
        *,
        include_page_elements_alias: bool = True,
    ) -> dict[str, object]:
        if not isinstance(payload, dict):
            raise ValueError("Pass1 artifact must be a JSON object.")

        meta = payload.get("meta")
        result = payload.get("result")
        if not isinstance(meta, dict):
            raise ValueError("Pass1 artifact must include a meta object.")
        if not isinstance(result, dict):
            raise ValueError("Pass1 artifact must include a result object.")

        required_meta_keys = {"schema_version", "prompt_version", "model_name", "generated_at"}
        missing_meta_keys = [key for key in required_meta_keys if not meta.get(key)]
        if missing_meta_keys:
            raise ValueError(f"Pass1 artifact meta is missing required fields: {', '.join(missing_meta_keys)}")

        normalized_result = dict(result)
        normalized_result["document_id"] = document_id
        normalized_result["page_number"] = page_number
        normalized_result["candidate_anchors"] = self._legacy_candidate_anchors_from_pass1_result(
            normalized_result,
        )
        normalized_result.pop("page_elements", None)
        normalized_result.pop("candidate_regions", None)
        normalized_result["page_guide"] = self._page_guide_from_pass1_result(normalized_result)
        validated_result = validate_payload("pass1", normalized_result)
        if include_page_elements_alias:
            validated_result["page_elements"] = [
                self._page_element_from_legacy_candidate(candidate)
                for candidate in validated_result["candidate_anchors"]
            ]

        return {
            "meta": self._normalize_pass1_meta(meta),
            "result": validated_result,
        }

    def _page_guide_from_pass1_result(self, result: dict[str, object]) -> object:
        raw_page_guide = result.get("page_guide")
        if isinstance(raw_page_guide, dict):
            return raw_page_guide

        page_role = str(result.get("page_role") or "").strip() or "Rendered PDF page"
        one_line_thesis = str(result.get("page_summary") or "").strip() or None
        return {
            "page_role": page_role,
            "one_line_thesis": one_line_thesis,
            "key_question": None,
            "reading_path": [],
            "logic_flow": [],
            "key_concepts": [],
            "omitted_context": [],
            "study_focus": [],
            "common_confusions": [],
            "example_or_application": None,
            "must_remember": [],
            "self_check_questions": [],
            "before_next_connection": {
                "previous": None,
                "next": None,
            },
        }

    def _legacy_candidate_anchors_from_pass1_result(
        self,
        result: dict[str, object],
    ) -> object:
        raw_candidates = result.get("candidate_anchors")
        if not isinstance(raw_candidates, list):
            raw_candidates = result.get("page_elements")
        if not isinstance(raw_candidates, list):
            raw_candidates = result.get("candidate_regions")
        if not isinstance(raw_candidates, list):
            return raw_candidates

        return [self._legacy_candidate_from_page_element(candidate) for candidate in raw_candidates]

    def _legacy_candidate_from_page_element(self, candidate: object) -> object:
        if not isinstance(candidate, dict):
            return candidate

        normalized = dict(candidate)
        if not normalized.get("anchor_id") and normalized.get("element_id"):
            normalized["anchor_id"] = normalized["element_id"]
        if not normalized.get("anchor_id") and normalized.get("region_id"):
            normalized["anchor_id"] = normalized["region_id"]
        if not normalized.get("anchor_type") and normalized.get("element_type"):
            normalized["anchor_type"] = normalized["element_type"]
        if not normalized.get("anchor_type") and normalized.get("region_type"):
            normalized["anchor_type"] = normalized["region_type"]
        normalized.pop("element_id", None)
        normalized.pop("element_type", None)
        normalized.pop("region_id", None)
        normalized.pop("region_type", None)
        return normalized

    def _page_element_from_legacy_candidate(self, candidate: dict[str, object]) -> dict[str, object]:
        return {
            **candidate,
            "element_id": candidate["anchor_id"],
            "element_type": candidate["anchor_type"],
        }

    def _normalize_pass1_meta(self, meta: dict[str, object]) -> dict[str, object]:
        normalized_meta: dict[str, object] = {
            "schema_version": str(meta["schema_version"]),
            "prompt_version": str(meta["prompt_version"]),
            "model_name": str(meta["model_name"]),
            "generated_at": str(meta["generated_at"]),
        }

        for key in _OPTIONAL_PASS1_META_KEYS:
            value = meta.get(key)
            if value is None:
                continue
            normalized_value = str(value).strip()
            if not normalized_value:
                continue
            if key == "pass1_path" and normalized_value not in _ALLOWED_PASS1_PATHS:
                raise ValueError(
                    "Pass1 artifact meta pass1_path must be one of: "
                    + ", ".join(sorted(_ALLOWED_PASS1_PATHS))
                )
            if key == "route_label" and normalized_value not in _ALLOWED_ROUTE_LABELS:
                raise ValueError(
                    "Pass1 artifact meta route_label must be one of: "
                    + ", ".join(sorted(_ALLOWED_ROUTE_LABELS))
                )
            normalized_meta[key] = normalized_value

        return normalized_meta

    def _normalize_parse_artifact(
        self,
        document_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        if not isinstance(payload, dict):
            raise ValueError("Parse artifact must be a JSON object.")

        normalized_payload = DocumentParseArtifact.model_validate(payload).model_dump(mode="json")
        if normalized_payload["document_id"] != document_id:
            raise ValueError("Parse artifact document_id does not match the requested document.")
        return normalized_payload

    def _normalize_page_parse_artifact(
        self,
        document_id: str,
        page_number: int,
        payload: dict[str, object],
    ) -> dict[str, object]:
        if not isinstance(payload, dict):
            raise ValueError("Page parse artifact must be a JSON object.")

        normalized_payload = PageParseArtifact.model_validate(payload).model_dump(mode="json")
        if normalized_payload["document_id"] != document_id:
            raise ValueError("Page parse artifact document_id does not match the requested document.")
        if int(normalized_payload["page_number"]) != page_number:
            raise ValueError("Page parse artifact page_number does not match the requested page.")
        return normalized_payload

    def _normalize_page_manifest(
        self,
        document_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        if not isinstance(payload, dict):
            raise ValueError("Page manifest must be a JSON object.")

        normalized_payload = DocumentPageManifest.model_validate(payload).model_dump(mode="json")
        if normalized_payload["document_id"] != document_id:
            raise ValueError("Page manifest document_id does not match the requested document.")
        return normalized_payload

    def _normalize_document_spine(
        self,
        document_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        if not isinstance(payload, dict):
            raise ValueError("Document spine artifact must be a JSON object.")

        normalized_payload = DocumentSpineArtifact.model_validate(payload).model_dump(mode="json")
        if normalized_payload["result"]["document_id"] != document_id:
            raise ValueError("Document spine document_id does not match the requested document.")
        return normalized_payload

    def _normalize_page_routing(
        self,
        document_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        if not isinstance(payload, dict):
            raise ValueError("Page routing artifact must be a JSON object.")

        normalized_payload = PageRoutingArtifact.model_validate(payload).model_dump(mode="json")
        if normalized_payload["result"]["document_id"] != document_id:
            raise ValueError("Page routing document_id does not match the requested document.")
        return normalized_payload

    def _normalize_processing_benchmark(
        self,
        document_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        if not isinstance(payload, dict):
            raise ValueError("Processing benchmark must be a JSON object.")

        normalized_payload = self._benchmark_defaults(document_id)
        normalized_payload.update(dict(payload))
        normalized_payload["document_id"] = document_id

        if str(normalized_payload["document_id"]) != document_id:
            raise ValueError("Processing benchmark document_id does not match the requested document.")

        for field_name in _BENCHMARK_DURATION_FIELDS:
            seconds = round(float(normalized_payload[field_name]), 4)
            if seconds < 0:
                raise ValueError(f"{field_name} must be >= 0.")
            normalized_payload[field_name] = seconds

        for field_name in _BENCHMARK_COUNT_FIELDS - {"openai_call_count_total", "codex_cli_call_count"}:
            count = int(normalized_payload[field_name])
            if count < 0:
                raise ValueError(f"{field_name} must be >= 0.")
            normalized_payload[field_name] = count

        normalized_payload["openai_call_count_total"] = (
            int(normalized_payload["openai_pass1_call_count"])
            + int(normalized_payload["openai_synthesis_call_count"])
            + int(normalized_payload["openai_pass2_call_count"])
        )
        normalized_payload["codex_cli_call_count"] = (
            int(normalized_payload["codex_cli_document_guide_call_count"])
            + int(normalized_payload["codex_cli_page_guide_call_count"])
            + int(normalized_payload["codex_cli_pass1_call_count"])
            + int(normalized_payload["codex_cli_semantic_guide_call_count"])
            + int(normalized_payload["codex_cli_synthesis_call_count"])
            + int(normalized_payload["codex_cli_pass2_call_count"])
            + int(normalized_payload["codex_cli_selection_call_count"])
            + int(normalized_payload["codex_cli_follow_up_call_count"])
        )

        final_status = str(normalized_payload["final_status"]).strip()
        if final_status not in {status.value for status in DocumentStatus}:
            raise ValueError("final_status must be a valid DocumentStatus value.")
        normalized_payload["final_status"] = final_status

        document_parser_backend = str(normalized_payload["document_parser_backend"]).strip()
        if document_parser_backend not in {"stub", "pymupdf4llm"}:
            raise ValueError("document_parser_backend must be 'stub' or 'pymupdf4llm'.")
        normalized_payload["document_parser_backend"] = document_parser_backend

        pass1_mode = str(normalized_payload["pass1_mode"]).strip()
        if pass1_mode not in {"parser_first", "legacy_llm", "hybrid"}:
            raise ValueError("pass1_mode must be 'parser_first', 'legacy_llm', or 'hybrid'.")
        normalized_payload["pass1_mode"] = pass1_mode

        pass1_routing_mode = str(normalized_payload["pass1_routing_mode"]).strip()
        if pass1_routing_mode not in {"legacy", "hybrid"}:
            raise ValueError("pass1_routing_mode must be 'legacy' or 'hybrid'.")
        normalized_payload["pass1_routing_mode"] = pass1_routing_mode

        pipeline_mode = str(normalized_payload["pipeline_mode"]).strip()
        if pipeline_mode not in _ALLOWED_PIPELINE_MODES:
            raise ValueError("pipeline_mode must be 'legacy', 'hybrid', or 'v2_spine'.")
        normalized_payload["pipeline_mode"] = pipeline_mode

        spine_mode = str(normalized_payload["spine_mode"]).strip()
        if spine_mode not in _ALLOWED_SPINE_MODES:
            raise ValueError("spine_mode must be 'off', 'shadow', or 'active'.")
        normalized_payload["spine_mode"] = spine_mode

        for field_name in (
            "openai_model_pass1",
            "semantic_guide_model",
            "openai_model_synthesis",
            "openai_model_pass2",
            "reasoning_effort_pass1",
            "reasoning_effort_semantic_guide",
            "reasoning_effort_synthesis",
            "reasoning_effort_pass2",
            "generated_at",
        ):
            normalized_value = str(normalized_payload[field_name]).strip()
            if not normalized_value:
                raise ValueError(f"{field_name} must be a non-empty string.")
            normalized_payload[field_name] = normalized_value

        openai_timeout_seconds = int(normalized_payload["openai_timeout_seconds"])
        openai_max_retries = int(normalized_payload["openai_max_retries"])
        analysis_image_long_edge = int(normalized_payload["analysis_image_long_edge"])
        pass1_max_workers = int(normalized_payload["pass1_max_workers"])
        if openai_timeout_seconds < 0:
            raise ValueError("openai_timeout_seconds must be >= 0.")
        if openai_max_retries < 0:
            raise ValueError("openai_max_retries must be >= 0.")
        if analysis_image_long_edge < 0:
            raise ValueError("analysis_image_long_edge must be >= 0.")
        if pass1_max_workers < 1:
            raise ValueError("pass1_max_workers must be >= 1.")
        normalized_payload["openai_timeout_seconds"] = openai_timeout_seconds
        normalized_payload["openai_max_retries"] = openai_max_retries
        normalized_payload["analysis_image_long_edge"] = analysis_image_long_edge
        normalized_payload["pass1_max_workers"] = pass1_max_workers

        normalized_payload["parse_artifact_reused"] = bool(normalized_payload["parse_artifact_reused"])
        normalized_payload["page_manifest_reused"] = bool(normalized_payload["page_manifest_reused"])
        normalized_payload["document_spine_generated"] = bool(normalized_payload["document_spine_generated"])
        normalized_payload["page_routing_generated"] = bool(normalized_payload["page_routing_generated"])

        spine_shadow_status = str(normalized_payload["spine_shadow_status"]).strip()
        if spine_shadow_status not in _ALLOWED_SPINE_SHADOW_STATUSES:
            raise ValueError(
                "spine_shadow_status must be one of disabled, skipped, completed, failed."
            )
        normalized_payload["spine_shadow_status"] = spine_shadow_status

        spine_shadow_reason = normalized_payload.get("spine_shadow_reason")
        if spine_shadow_reason is None:
            normalized_payload["spine_shadow_reason"] = None
        else:
            normalized_reason = str(spine_shadow_reason).strip()
            if normalized_reason not in _ALLOWED_SPINE_SHADOW_REASONS:
                raise ValueError(
                    "spine_shadow_reason must be disabled, parse_unavailable, "
                    "manifest_unavailable, builder_failed, not_requested, or null."
                )
            normalized_payload["spine_shadow_reason"] = normalized_reason

        routing_counts_by_label = normalized_payload.get("routing_counts_by_label")
        if not isinstance(routing_counts_by_label, dict):
            raise ValueError("routing_counts_by_label must be a JSON object.")
        normalized_routing_counts: dict[str, int] = {}
        for label in sorted(_ALLOWED_ROUTE_LABELS):
            count = int(routing_counts_by_label.get(label, 0))
            if count < 0:
                raise ValueError(f"routing_counts_by_label[{label}] must be >= 0.")
            normalized_routing_counts[label] = count
        extra_labels = set(routing_counts_by_label) - _ALLOWED_ROUTE_LABELS
        if extra_labels:
            raise ValueError("routing_counts_by_label contains unsupported labels.")
        normalized_payload["routing_counts_by_label"] = normalized_routing_counts

        pass2_execution_mode = str(normalized_payload["pass2_execution_mode"]).strip()
        if pass2_execution_mode not in _ALLOWED_PASS2_EXECUTION_MODES:
            raise ValueError("pass2_execution_mode must be 'all_pages' or 'hard_pages_only'.")
        normalized_payload["pass2_execution_mode"] = pass2_execution_mode

        for field_name in (
            "pass2_llm_pages",
            "pass2_compat_pages",
            "pass2_selected_pages",
            "pass2_skipped_llm_pages",
            "compat_promoted_to_llm_pages",
        ):
            normalized_payload[field_name] = self._normalize_sorted_unique_int_list(
                normalized_payload.get(field_name, [])
            )

        normalized_payload["pass2_llm_count"] = len(normalized_payload["pass2_llm_pages"])
        normalized_payload["pass2_compat_count"] = len(normalized_payload["pass2_compat_pages"])
        normalized_payload["compat_promoted_to_llm_count"] = len(
            normalized_payload["compat_promoted_to_llm_pages"]
        )

        pass2_planner_status = str(normalized_payload["pass2_planner_status"]).strip()
        if pass2_planner_status not in _ALLOWED_PASS2_PLANNER_STATUSES:
            raise ValueError(
                "pass2_planner_status must be one of disabled, active, fallback."
            )
        normalized_payload["pass2_planner_status"] = pass2_planner_status

        pass2_planner_reason = normalized_payload.get("pass2_planner_reason")
        if pass2_planner_reason is None:
            normalized_payload["pass2_planner_reason"] = None
        else:
            normalized_reason = str(pass2_planner_reason).strip()
            if normalized_reason not in _ALLOWED_PASS2_PLANNER_REASONS:
                raise ValueError(
                    "pass2_planner_reason must be not_requested, routing_missing, "
                    "routing_invalid, routing_coverage_mismatch, compat_builder_failed_promoted, or null."
                )
            normalized_payload["pass2_planner_reason"] = normalized_reason

        final_error_message = normalized_payload.get("final_error_message")
        if final_error_message is None:
            normalized_payload["final_error_message"] = None
        else:
            normalized_payload["final_error_message"] = str(final_error_message).strip() or None

        return normalized_payload

    def _normalize_document_summary_artifact(
        self,
        document_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        if not isinstance(payload, dict):
            raise ValueError("Document summary artifact must be a JSON object.")

        meta = payload.get("meta")
        result = payload.get("result")
        if not isinstance(meta, dict):
            raise ValueError("Document summary artifact must include a meta object.")
        if not isinstance(result, dict):
            raise ValueError("Document summary artifact must include a result object.")

        required_meta_keys = {
            "schema_version",
            "prompt_version",
            "model_name",
            "generated_at",
            "total_rendered_pages",
            "pass1_completed_pages",
            "missing_pages",
            "coverage_ratio",
            "partial_input_used",
            "coverage_threshold",
        }
        missing_meta_keys = [key for key in required_meta_keys if meta.get(key) is None]
        if missing_meta_keys:
            raise ValueError(
                "Document summary artifact meta is missing required fields: "
                + ", ".join(missing_meta_keys)
            )

        total_rendered_pages = int(meta["total_rendered_pages"])
        pass1_completed_pages = int(meta["pass1_completed_pages"])
        coverage_threshold = int(meta["coverage_threshold"])
        coverage_ratio = float(meta["coverage_ratio"])
        if total_rendered_pages < 0:
            raise ValueError("Document summary artifact total_rendered_pages must be >= 0.")
        if pass1_completed_pages < 0:
            raise ValueError("Document summary artifact pass1_completed_pages must be >= 0.")
        if coverage_threshold < 0:
            raise ValueError("Document summary artifact coverage_threshold must be >= 0.")
        if pass1_completed_pages > total_rendered_pages:
            raise ValueError("pass1_completed_pages must be <= total_rendered_pages.")
        if coverage_ratio < 0 or coverage_ratio > 1:
            raise ValueError("coverage_ratio must be between 0 and 1.")

        missing_pages = sorted({int(page) for page in meta["missing_pages"]})
        if any(page < 1 for page in missing_pages):
            raise ValueError("missing_pages must contain only positive page numbers.")

        normalized_result = dict(result)
        normalized_result["document_id"] = document_id
        validated_result = validate_payload("document_synthesis", normalized_result)

        return {
            "meta": {
                "schema_version": str(meta["schema_version"]),
                "prompt_version": str(meta["prompt_version"]),
                "model_name": str(meta["model_name"]),
                "generated_at": str(meta["generated_at"]),
                "total_rendered_pages": total_rendered_pages,
                "pass1_completed_pages": pass1_completed_pages,
                "missing_pages": missing_pages,
                "coverage_ratio": coverage_ratio,
                "partial_input_used": bool(meta["partial_input_used"]),
                "coverage_threshold": coverage_threshold,
            },
            "result": validated_result,
        }

    def _normalize_document_guide_artifact(
        self,
        document_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        if not isinstance(payload, dict):
            raise ValueError("Document guide artifact must be a JSON object.")

        meta = payload.get("meta")
        result = payload.get("result")
        if not isinstance(meta, dict):
            raise ValueError("Document guide artifact must include a meta object.")
        if not isinstance(result, dict):
            raise ValueError("Document guide artifact must include a result object.")

        required_meta_keys = {"schema_version", "prompt_version", "model_name", "generated_at"}
        missing_meta_keys = [key for key in required_meta_keys if not meta.get(key)]
        if missing_meta_keys:
            raise ValueError(
                "Document guide artifact meta is missing required fields: "
                + ", ".join(missing_meta_keys)
            )

        normalized_result = dict(result)
        normalized_result["document_id"] = document_id
        if isinstance(normalized_result.get("document_guide"), dict):
            normalized_document_guide = dict(normalized_result["document_guide"])
            normalized_document_guide["document_id"] = document_id
            normalized_result["document_guide"] = normalized_document_guide

        validated_result = validate_payload("document_guide", normalized_result)
        return {
            "meta": {
                "schema_version": str(meta["schema_version"]),
                "prompt_version": str(meta["prompt_version"]),
                "model_name": str(meta["model_name"]),
                "generated_at": str(meta["generated_at"]),
                "total_rendered_pages": int(meta.get("total_rendered_pages", 0)),
                "page_context_completed_pages": int(meta.get("page_context_completed_pages", 0)),
                "digest_size_chars": int(meta.get("digest_size_chars", 0)),
                "response_language": str(meta.get("response_language", "ko")),
            },
            "result": validated_result,
        }

    def _normalize_page_guide_chunk_artifact(
        self,
        document_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        if not isinstance(payload, dict):
            raise ValueError("Page guide chunk artifact must be a JSON object.")

        meta = payload.get("meta")
        result = payload.get("result")
        if not isinstance(meta, dict):
            raise ValueError("Page guide chunk artifact must include a meta object.")
        if not isinstance(result, dict):
            raise ValueError("Page guide chunk artifact must include a result object.")

        required_meta_keys = {"schema_version", "prompt_version", "model_name", "generated_at"}
        missing_meta_keys = [key for key in required_meta_keys if not meta.get(key)]
        if missing_meta_keys:
            raise ValueError(
                "Page guide chunk artifact meta is missing required fields: "
                + ", ".join(missing_meta_keys)
            )

        normalized_result = dict(result)
        normalized_result["document_id"] = document_id
        normalized_result["page_numbers"] = self._normalize_sorted_unique_int_list(
            normalized_result.get("page_numbers")
        )
        for page_guide in normalized_result.get("page_guides", []):
            if isinstance(page_guide, dict):
                page_guide["document_id"] = document_id

        validated_result = validate_payload("page_guide_chunk", normalized_result)
        return {
            "meta": {
                "schema_version": str(meta["schema_version"]),
                "prompt_version": str(meta["prompt_version"]),
                "model_name": str(meta["model_name"]),
                "generated_at": str(meta["generated_at"]),
                "chunk_index": int(meta.get("chunk_index", validated_result["chunk_index"])),
                "total_chunks": int(meta.get("total_chunks", 0)),
                "page_numbers": self._normalize_sorted_unique_int_list(
                    meta.get("page_numbers", validated_result["page_numbers"])
                ),
                "digest_size_chars": int(meta.get("digest_size_chars", 0)),
                "response_language": str(meta.get("response_language", "ko")),
            },
            "result": validated_result,
        }

    def _normalize_semantic_guide_artifact(
        self,
        document_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        if not isinstance(payload, dict):
            raise ValueError("Semantic guide artifact must be a JSON object.")

        meta = payload.get("meta")
        result = payload.get("result")
        if not isinstance(meta, dict):
            raise ValueError("Semantic guide artifact must include a meta object.")
        if not isinstance(result, dict):
            raise ValueError("Semantic guide artifact must include a result object.")

        required_meta_keys = {"schema_version", "prompt_version", "model_name", "generated_at"}
        missing_meta_keys = [key for key in required_meta_keys if not meta.get(key)]
        if missing_meta_keys:
            raise ValueError(
                "Semantic guide artifact meta is missing required fields: "
                + ", ".join(missing_meta_keys)
            )

        normalized_result = dict(result)
        normalized_result["document_id"] = document_id
        if isinstance(normalized_result.get("document_guide"), dict):
            normalized_document_guide = dict(normalized_result["document_guide"])
            normalized_document_guide["document_id"] = document_id
            normalized_result["document_guide"] = normalized_document_guide

        validated_result = validate_payload("semantic_guide", normalized_result)
        return {
            "meta": {
                "schema_version": str(meta["schema_version"]),
                "prompt_version": str(meta["prompt_version"]),
                "model_name": str(meta["model_name"]),
                "generated_at": str(meta["generated_at"]),
                "total_rendered_pages": int(meta.get("total_rendered_pages", 0)),
                "page_context_completed_pages": int(meta.get("page_context_completed_pages", 0)),
                "semantic_guide_call_count": int(meta.get("semantic_guide_call_count", 1)),
                "digest_size_chars": int(meta.get("digest_size_chars", 0)),
                "semantic_guide_mode": str(meta.get("semantic_guide_mode", "legacy_single_call")),
                "document_guide_call_count": int(meta.get("document_guide_call_count", 0)),
                "page_guide_chunk_call_count": int(meta.get("page_guide_chunk_call_count", 0)),
                "page_guide_chunk_size": int(meta.get("page_guide_chunk_size", 0)),
                "page_guide_chunk_count": int(meta.get("page_guide_chunk_count", 0)),
                "response_language": str(meta.get("response_language", "ko")),
            },
            "result": validated_result,
        }

    def _normalize_page_context_artifact(
        self,
        document_id: str,
        page_number: int,
        payload: dict[str, object],
    ) -> dict[str, object]:
        if not isinstance(payload, dict):
            raise ValueError("Page context artifact must be a JSON object.")

        normalized = dict(payload)
        normalized["document_id"] = document_id
        normalized["page_number"] = page_number
        if not str(normalized.get("parser_source") or "").strip():
            normalized["parser_source"] = self.settings.document_parser_backend
        if not str(normalized.get("generated_at") or "").strip():
            normalized["generated_at"] = datetime.now(timezone.utc).isoformat()
        if not str(normalized.get("schema_version") or "").strip():
            normalized["schema_version"] = self.settings.schema_version
        if not str(normalized.get("parser_schema_version") or "").strip():
            normalized["parser_schema_version"] = self.settings.parser_schema_version

        page_elements = normalized.get("page_elements")
        if not isinstance(page_elements, list):
            normalized["page_elements"] = []

        for field_name in (
            "text_blocks",
            "visual_blocks",
            "table_blocks",
            "figure_blocks",
            "caption_blocks",
            "formula_like_blocks",
            "heading_chain",
            "source_candidates",
            "parser_quality_notes",
        ):
            if not isinstance(normalized.get(field_name), list):
                normalized[field_name] = []

        for field_name in ("text_density", "image_coverage", "scan_like_score", "table_like_score", "figure_like_score"):
            try:
                normalized[field_name] = round(float(normalized.get(field_name, 0.0)), 4)
            except (TypeError, ValueError):
                normalized[field_name] = 0.0

        normalized["reading_order_quality"] = str(
            normalized.get("reading_order_quality") or "unknown"
        )
        return normalized

    def _normalize_pass2_artifact(
        self,
        document_id: str,
        page_number: int,
        payload: dict[str, object],
    ) -> dict[str, object]:
        if not isinstance(payload, dict):
            raise ValueError("Pass2 artifact must be a JSON object.")

        meta = payload.get("meta")
        result = payload.get("result")
        if not isinstance(meta, dict):
            raise ValueError("Pass2 artifact must include a meta object.")
        if not isinstance(result, dict):
            raise ValueError("Pass2 artifact must include a result object.")

        required_meta_keys = {"schema_version", "prompt_version", "model_name", "generated_at"}
        missing_meta_keys = [key for key in required_meta_keys if not meta.get(key)]
        if missing_meta_keys:
            raise ValueError(f"Pass2 artifact meta is missing required fields: {', '.join(missing_meta_keys)}")

        pass1_artifact = self.load_pass1_result(document_id, page_number)
        if pass1_artifact is None:
            raise ValueError("Pass2 artifact requires a valid pass1 artifact for the same page.")

        candidate_map = {
            str(candidate["anchor_id"]): dict(candidate)
            for candidate in pass1_artifact["result"]["candidate_anchors"]
        }
        valid_pass1_page_numbers = self._get_valid_pass1_page_numbers(document_id)

        normalized_result = dict(result)
        normalized_result["document_id"] = document_id
        normalized_result["page_number"] = page_number
        if isinstance(normalized_result.get("final_anchors"), list):
            normalized_result["final_anchors"] = [
                self._with_legacy_final_anchor_defaults(anchor)
                for anchor in normalized_result["final_anchors"]
            ]
        validated_result = validate_payload("pass2", normalized_result)

        normalized_final_anchors = []
        for anchor in validated_result["final_anchors"]:
            anchor_id = str(anchor["anchor_id"])
            if anchor_id not in candidate_map:
                raise ValueError(f"Pass2 artifact contains anchor_id not found in pass1 candidates: {anchor_id}")

            candidate = candidate_map[anchor_id]
            related_pages = sorted({int(page) for page in anchor["related_pages"]})
            if any(page == page_number for page in related_pages):
                raise ValueError("Pass2 artifact related_pages must not include the current page.")

            invalid_pages = [page for page in related_pages if page not in valid_pass1_page_numbers]
            if invalid_pages:
                raise ValueError(
                    "Pass2 artifact related_pages contains pages without valid pass1 artifacts: "
                    + ", ".join(map(str, invalid_pages))
                )

            related_concepts = anchor.get("related_concepts_and_pages") or []
            for concept_index, related_concept in enumerate(related_concepts):
                if not isinstance(related_concept, dict):
                    continue
                related_page_number = related_concept.get("page_number")
                if related_page_number is None:
                    continue
                related_page_number = int(related_page_number)
                if related_page_number not in valid_pass1_page_numbers:
                    raise ValueError(
                        "Pass2 artifact related_concepts_and_pages contains a page without "
                        f"valid pass1 artifacts: anchor_id={anchor_id}, index={concept_index}, "
                        f"page_number={related_page_number}"
                    )

            source_cues = anchor.get("source_cues") or []
            for cue_index, source_cue in enumerate(source_cues):
                if not isinstance(source_cue, dict):
                    continue
                source_page_number = source_cue.get("page_number")
                if source_page_number is None:
                    continue
                source_page_number = int(source_page_number)
                if source_page_number not in valid_pass1_page_numbers:
                    raise ValueError(
                        "Pass2 artifact source_cues contains a page without valid pass1 artifacts: "
                        f"anchor_id={anchor_id}, index={cue_index}, page_number={source_page_number}"
                    )

            normalized_final_anchors.append(
                {
                    **anchor,
                    "anchor_id": candidate["anchor_id"],
                    "anchor_type": candidate["anchor_type"],
                    "bbox": candidate["bbox"],
                    "related_pages": related_pages,
                }
            )

        normalized_result["final_anchors"] = normalized_final_anchors
        validated_result = validate_payload("pass2", normalized_result)

        return {
            "meta": self._normalize_pass2_meta(meta),
            "result": validated_result,
        }

    def _normalize_selection_explanation_artifact(
        self,
        document_id: str,
        page_number: int,
        selection_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        if not isinstance(payload, dict):
            raise ValueError("Selection explanation artifact must be a JSON object.")

        meta = payload.get("meta", {})
        result = payload.get("result")
        if not isinstance(meta, dict):
            raise ValueError("Selection explanation meta must be a JSON object.")
        if not isinstance(result, dict):
            raise ValueError("Selection explanation result must be a JSON object.")

        normalized_result = dict(result)
        normalized_result["document_id"] = document_id
        normalized_result["page_number"] = page_number
        normalized_result["selection_id"] = selection_id
        normalized_result["anchor_id"] = selection_id
        normalized_result["explanation_mode"] = "selection"
        if not normalized_result.get("concept_title") and normalized_result.get("label"):
            normalized_result["concept_title"] = normalized_result["label"]
        if not normalized_result.get("label") and normalized_result.get("concept_title"):
            normalized_result["label"] = normalized_result["concept_title"]
        if "selected_bbox" in normalized_result:
            normalized_result["bbox"] = normalized_result["selected_bbox"]
        elif "bbox" in normalized_result:
            normalized_result["selected_bbox"] = normalized_result["bbox"]

        validated_result = validate_payload("selection_explanation", normalized_result)
        valid_page_numbers = {page.page_number for page in self.get_pages(document_id)}

        related_pages = sorted({int(page) for page in validated_result["related_pages"]})
        for related_page in related_pages:
            if related_page not in valid_page_numbers:
                raise ValueError(
                    "Selection explanation contains related_pages value without a rendered page: "
                    f"selection_id={selection_id}, page_number={related_page}"
                )

        for concept_index, concept in enumerate(validated_result.get("related_concepts_and_pages") or []):
            concept_page_number = concept.get("page_number")
            if concept_page_number is not None and int(concept_page_number) not in valid_page_numbers:
                raise ValueError(
                    "Selection explanation contains related_concepts_and_pages page_number without a rendered page: "
                    f"selection_id={selection_id}, index={concept_index}, page_number={concept_page_number}"
                )

        for cue_index, cue in enumerate(validated_result.get("source_cues") or []):
            source_page_number = cue.get("page_number")
            if source_page_number is not None and int(source_page_number) not in valid_page_numbers:
                raise ValueError(
                    "Selection explanation contains source_cues page_number without a rendered page: "
                    f"selection_id={selection_id}, index={cue_index}, page_number={source_page_number}"
                )

        validated_result["related_pages"] = related_pages
        return {
            "meta": dict(meta),
            "result": validated_result,
        }

    def _selection_explanation_viewer_state(self, artifact: dict[str, object]) -> dict[str, bool]:
        meta = artifact.get("meta")
        viewer_state = meta.get("viewer_state") if isinstance(meta, dict) else None
        if not isinstance(viewer_state, dict):
            viewer_state = {}

        return {
            "is_important": bool(viewer_state.get("is_important", False)),
        }

    def _with_legacy_final_anchor_defaults(self, anchor: object) -> object:
        if not isinstance(anchor, dict):
            return anchor

        normalized_anchor = dict(anchor)
        for field_name in (
            "study_importance",
            "meaning_in_context",
            "why_it_matters_here",
            "related_concepts_and_pages",
            "source_cues",
        ):
            normalized_anchor.setdefault(field_name, None)
        return normalized_anchor

    def _normalize_pass2_meta(self, meta: dict[str, object]) -> dict[str, object]:
        normalized_meta: dict[str, object] = {
            "schema_version": str(meta["schema_version"]),
            "prompt_version": str(meta["prompt_version"]),
            "model_name": str(meta["model_name"]),
            "generated_at": str(meta["generated_at"]),
        }
        generation_mode = meta.get("pass2_generation_mode")
        if generation_mode is not None:
            normalized_generation_mode = str(generation_mode).strip()
            if normalized_generation_mode not in _ALLOWED_PASS2_GENERATION_MODES:
                raise ValueError("pass2_generation_mode must be 'llm' or 'compat'.")
            normalized_meta["pass2_generation_mode"] = normalized_generation_mode
        return normalized_meta

    def _get_valid_pass1_page_numbers(self, document_id: str) -> set[int]:
        valid_page_numbers: set[int] = set()
        for page in self.get_pages(document_id):
            try:
                artifact = self.load_pass1_result(document_id, page.page_number)
            except ValueError:
                continue
            if artifact is not None:
                valid_page_numbers.add(page.page_number)
        return valid_page_numbers

    def _get_document_summary_health(self, document_id: str) -> tuple[bool, str | None]:
        try:
            artifact = self.load_document_summary(document_id)
        except ValueError:
            return False, "Stored document summary is invalid."

        if artifact is None:
            return False, None
        return True, None

    def _get_semantic_guide_health(self, document_id: str) -> tuple[bool, str | None]:
        try:
            artifact = self.load_semantic_guide(document_id)
        except ValueError:
            return False, "Stored semantic guide is invalid."

        if artifact is None:
            return False, None
        pages = self.get_pages(document_id)
        rendered_page_numbers = {
            int(page.page_number)
            for page in pages
            if page.render_status is RenderStatus.RENDERED
        }
        result = artifact.get("result")
        page_guides = result.get("page_guides", []) if isinstance(result, dict) else []
        page_guide_numbers = {
            int(page_guide.get("page_number"))
            for page_guide in page_guides
            if isinstance(page_guide, dict) and page_guide.get("page_number") is not None
        }
        if rendered_page_numbers and page_guide_numbers != rendered_page_numbers:
            return False, "Stored semantic guide is missing required page guides."
        return True, None

    def _derive_processing_stage(
        self,
        *,
        status: DocumentStatus,
        rendered_pages: int,
        pass1_completed_pages: int,
        pass1_failed_page_numbers: set[int],
        pass2_completed_pages: int,
        pass2_failed_page_numbers: set[int],
        synthesis_ready: bool,
        error_message: str | None = None,
    ) -> ProcessingStage | None:
        analyzed_pass1_pages = pass1_completed_pages + len(pass1_failed_page_numbers)
        normalized_error = (error_message or "").lower()

        if status in {DocumentStatus.UPLOADED, DocumentStatus.RENDERING}:
            return ProcessingStage.RENDER

        if status is DocumentStatus.ANALYZING:
            if analyzed_pass1_pages < rendered_pages:
                return ProcessingStage.PASS1
            if not synthesis_ready:
                return ProcessingStage.SYNTHESIS
            if not self.settings.precompute_anchored_explanations:
                return ProcessingStage.SYNTHESIS
            return ProcessingStage.PASS2

        if status in {DocumentStatus.COMPLETED, DocumentStatus.FAILED}:
            if "document synthesis" in normalized_error or "coverage_threshold" in normalized_error:
                return ProcessingStage.SYNTHESIS
            if "pass2" in normalized_error:
                return ProcessingStage.PASS2
            if not self.settings.precompute_anchored_explanations and synthesis_ready:
                return ProcessingStage.SYNTHESIS
            if pass2_completed_pages > 0 or pass2_failed_page_numbers or synthesis_ready:
                return ProcessingStage.PASS2
            if rendered_pages > 0 and analyzed_pass1_pages < rendered_pages:
                return ProcessingStage.PASS1
            if rendered_pages > 0 and analyzed_pass1_pages >= rendered_pages:
                return ProcessingStage.SYNTHESIS
            return ProcessingStage.RENDER

        return None

    def _build_recent_failures(self, pages: Sequence[PageRecord]) -> list[dict[str, object]]:
        failures: list[dict[str, object]] = []
        for page in pages:
            if page.render_status is RenderStatus.FAILED:
                failures.append(
                    {
                        "page_number": page.page_number,
                        "stage": ProcessingStage.RENDER,
                        "error_message": "Page render failed.",
                    }
                )
            if page.pass1_status is StageStatus.FAILED and page.pass1_error_message:
                failures.append(
                    {
                        "page_number": page.page_number,
                        "stage": ProcessingStage.PASS1,
                        "error_message": page.pass1_error_message,
                    }
                )
            if page.pass2_status is StageStatus.FAILED and page.pass2_error_message:
                failures.append(
                    {
                        "page_number": page.page_number,
                        "stage": ProcessingStage.PASS2,
                        "error_message": page.pass2_error_message,
                    }
                )

        failures.sort(key=lambda item: (int(item["page_number"]), str(item["stage"])), reverse=True)
        return failures[:5]

    def _build_page_parse_artifact_payload(
        self,
        document_payload: dict[str, object],
        page_number: int,
    ) -> dict[str, object] | None:
        for page_payload in document_payload.get("pages", []):
            if int(page_payload["page_number"]) != page_number:
                continue

            return PageParseArtifact(
                document_id=str(document_payload["document_id"]),
                parser_source=str(document_payload["parser_source"]),
                schema_version=str(document_payload["schema_version"]),
                page_number=int(page_payload["page_number"]),
                width=float(page_payload["width"]),
                height=float(page_payload["height"]),
                ocr_used=bool(page_payload["ocr_used"]),
                blocks=list(page_payload["blocks"]),
            ).model_dump(mode="json")

        return None

    def _write_validated_json_artifact(
        self,
        target_path: Path,
        payload: dict[str, object],
        validator: Callable[[dict[str, object]], dict[str, object]],
    ) -> None:
        temp_path = target_path.parent / f".{target_path.name}.{uuid4().hex}.tmp"

        try:
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            loaded_payload = json.loads(temp_path.read_text(encoding="utf-8"))
            validator(loaded_payload)
            os.replace(temp_path, target_path)
        except Exception:
            if temp_path.exists():
                temp_path.unlink()
            raise

    def _normalize_sorted_unique_int_list(self, values: object) -> list[int]:
        if values is None:
            return []
        if not isinstance(values, list):
            raise ValueError("Expected a JSON array of page numbers.")
        normalized_values = sorted({int(value) for value in values})
        if any(value < 1 for value in normalized_values):
            raise ValueError("Page number lists must contain only positive integers.")
        return normalized_values

    def _benchmark_defaults(self, document_id: str) -> dict[str, object]:
        return {
            "document_id": document_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_processing_time_seconds": 0.0,
            "upload_to_render_seconds": 0.0,
            "upload_to_parser_map_ready_seconds": 0.0,
            "upload_to_semantic_guide_ready_seconds": 0.0,
            "upload_to_viewer_ready_seconds": 0.0,
            "render_time_seconds": 0.0,
            "parse_time_seconds": 0.0,
            "triage_time_seconds": 0.0,
            "spine_time_seconds": 0.0,
            "pass1_time_seconds": 0.0,
            "document_guide_time_seconds": 0.0,
            "page_guide_chunks_time_seconds": 0.0,
            "semantic_guide_time_seconds": 0.0,
            "synthesis_time_seconds": 0.0,
            "pass2_time_seconds": 0.0,
            "rendered_pages": 0,
            "hard_page_count": 0,
            "page_element_count": 0,
            "page_guide_count": 0,
            "pass1_parser_first_pages": 0,
            "pass1_text_first_pages": 0,
            "pass1_multimodal_pages": 0,
            "pass1_escalated_pages": 0,
            "pass2_completed_pages": 0,
            "pass2_failed_pages": 0,
            "pass2_execution_mode": "all_pages",
            "pass2_llm_pages": [],
            "pass2_compat_pages": [],
            "pass2_llm_count": 0,
            "pass2_compat_count": 0,
            "pass2_selected_pages": [],
            "pass2_skipped_llm_pages": [],
            "pass2_planner_status": "disabled",
            "pass2_planner_reason": "not_requested",
            "compat_promoted_to_llm_pages": [],
            "compat_promoted_to_llm_count": 0,
            "openai_call_count_total": 0,
            "openai_pass1_call_count": 0,
            "openai_synthesis_call_count": 0,
            "openai_pass2_call_count": 0,
            "codex_cli_call_count": 0,
            "codex_cli_document_guide_call_count": 0,
            "codex_cli_page_guide_call_count": 0,
            "codex_cli_pass1_call_count": 0,
            "codex_cli_semantic_guide_call_count": 0,
            "codex_cli_synthesis_call_count": 0,
            "codex_cli_pass2_call_count": 0,
            "codex_cli_selection_call_count": 0,
            "codex_cli_follow_up_call_count": 0,
            "codex_cli_error_count": 0,
            "codex_cli_repair_count": 0,
            "semantic_guide_completed_chunks": 0,
            "semantic_guide_total_chunks": 0,
            "semantic_guide_failed_chunks": 0,
            "document_parser_backend": self.settings.document_parser_backend,
            "pass1_mode": self.settings.pass1_mode,
            "pass1_routing_mode": self.settings.pass1_routing_mode,
            "semantic_guide_mode": self.settings.semantic_guide_mode,
            "semantic_guide_page_chunk_size": self.settings.semantic_guide_page_chunk_size,
            "semantic_guide_page_chunk_max_workers": self.settings.semantic_guide_page_chunk_max_workers,
            "semantic_guide_retry_attempts": self.settings.semantic_guide_retry_attempts,
            "pass1_max_workers": self.settings.pass1_max_workers,
            "pipeline_mode": self.settings.pipeline_mode,
            "spine_mode": self.settings.v2_spine_mode,
            "openai_model_pass1": self.settings.stage_config("pass1").model_name,
            "semantic_guide_model": self.settings.stage_config("semantic_guide").model_name,
            "document_guide_model": self.settings.stage_config("document_guide").model_name,
            "page_guide_chunk_model": self.settings.stage_config("page_guide_chunk").model_name,
            "openai_model_synthesis": self.settings.stage_config("document_synthesis").model_name,
            "openai_model_pass2": self.settings.stage_config("pass2").model_name,
            "reasoning_effort_pass1": self.settings.stage_config("pass1").reasoning_effort,
            "reasoning_effort_semantic_guide": self.settings.stage_config("semantic_guide").reasoning_effort,
            "reasoning_effort_document_guide": self.settings.stage_config("document_guide").reasoning_effort,
            "reasoning_effort_page_guide_chunk": self.settings.stage_config("page_guide_chunk").reasoning_effort,
            "reasoning_effort_synthesis": self.settings.stage_config("document_synthesis").reasoning_effort,
            "reasoning_effort_pass2": self.settings.stage_config("pass2").reasoning_effort,
            "openai_timeout_seconds": self.settings.openai_timeout_seconds,
            "openai_max_retries": self.settings.openai_max_retries,
            "analysis_image_long_edge": 0,
            "parse_artifact_reused": False,
            "page_manifest_reused": False,
            "document_spine_generated": False,
            "page_routing_generated": False,
            "routing_counts_by_label": {
                "text-rich": 0,
                "visual-rich": 0,
                "scan-like": 0,
            },
            "spine_shadow_status": "disabled",
            "spine_shadow_reason": (
                "disabled" if self.settings.v2_spine_mode == "off" else "not_requested"
            ),
            "final_status": DocumentStatus.UPLOADED.value,
            "final_error_message": None,
        }

    def _get_current_page_number(
        self,
        *,
        pages: Sequence[PageRecord],
        stage: ProcessingStage | None,
        status: DocumentStatus,
    ) -> int | None:
        if status in {DocumentStatus.COMPLETED, DocumentStatus.FAILED}:
            return None

        ordered_pages = sorted(pages, key=lambda page: page.page_number)
        if stage is ProcessingStage.PASS1:
            for page in ordered_pages:
                if page.render_status is not RenderStatus.RENDERED:
                    continue
                if page.pass1_status in {None, StageStatus.PENDING}:
                    return page.page_number
            return None

        if stage is ProcessingStage.PASS2:
            for page in ordered_pages:
                if page.pass1_status is not StageStatus.COMPLETED:
                    continue
                if page.pass2_status in {None, StageStatus.PENDING}:
                    return page.page_number
            return None

        return None

    def _ensure_document_columns(self, connection: sqlite3.Connection) -> None:
        existing_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(documents)").fetchall()
        }
        if "response_language" not in existing_columns:
            connection.execute("ALTER TABLE documents ADD COLUMN response_language TEXT NOT NULL DEFAULT 'ko'")

    def _ensure_pages_columns(self, connection: sqlite3.Connection) -> None:
        existing_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(pages)").fetchall()
        }
        if "pass1_error_message" not in existing_columns:
            connection.execute("ALTER TABLE pages ADD COLUMN pass1_error_message TEXT NULL")
        if "pass2_error_message" not in existing_columns:
            connection.execute("ALTER TABLE pages ADD COLUMN pass2_error_message TEXT NULL")


_storage_service: StorageService | None = None
_storage_service_lock = Lock()


def get_storage_service() -> StorageService:
    global _storage_service
    if _storage_service is None:
        with _storage_service_lock:
            if _storage_service is None:
                _storage_service = StorageService()
    return _storage_service


def init_storage() -> None:
    get_storage_service().init_storage()
