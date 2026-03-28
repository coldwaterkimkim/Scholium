from __future__ import annotations

import json
import os
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
from app.utils.validation import validate_payload


_UNSET = object()
_OPTIONAL_PASS1_META_KEYS = {"pass1_path", "route_label", "route_reason", "parser_source"}
_ALLOWED_PASS1_PATHS = {"text-first", "multimodal", "escalated"}
_ALLOWED_ROUTE_LABELS = {"text-rich", "scan-like", "visual-rich"}
_BENCHMARK_DURATION_FIELDS = {
    "total_processing_time_seconds",
    "render_time_seconds",
    "parse_time_seconds",
    "triage_time_seconds",
    "pass1_time_seconds",
    "synthesis_time_seconds",
    "pass2_time_seconds",
}
_BENCHMARK_COUNT_FIELDS = {
    "rendered_pages",
    "pass1_text_first_pages",
    "pass1_multimodal_pages",
    "pass1_escalated_pages",
    "pass2_completed_pages",
    "pass2_failed_pages",
    "openai_call_count_total",
    "openai_pass1_call_count",
    "openai_synthesis_call_count",
    "openai_pass2_call_count",
}
_OPENAI_CALL_COUNT_FIELDS_BY_STAGE = {
    "pass1": "openai_pass1_call_count",
    "document_synthesis": "openai_synthesis_call_count",
    "pass2": "openai_pass2_call_count",
}


class StorageService:
    def __init__(self, settings: AppSettings | None = None) -> None:
        self.settings = settings or get_settings()
        self.db_path = self._resolve_project_path(self.settings.document_db_path)
        self.raw_pdfs_dir = self._resolve_project_path(self.settings.raw_pdfs_dir)
        self.rendered_pages_dir = self._resolve_project_path(self.settings.rendered_pages_dir)
        self.analysis_dir = self._resolve_project_path(self.settings.analysis_dir)
        self.parsed_dir = self._resolve_project_path("./data/parsed")
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
            self._ensure_pages_columns(connection)

    def save_uploaded_document(self, filename: str, file_bytes: bytes) -> DocumentRecord:
        if not filename:
            raise ValueError("PDF filename is required.")

        self.init_storage()

        document_id = f"doc_{uuid4().hex}"
        original_file_path = self.raw_pdfs_dir / f"{document_id}.pdf"
        original_relative_path = original_file_path.relative_to(PROJECT_ROOT).as_posix()

        original_file_path.write_bytes(file_bytes)

        timestamp = datetime.now(timezone.utc)
        document = DocumentRecord(
            document_id=document_id,
            filename=filename,
            original_path=original_relative_path,
            status=DocumentStatus.UPLOADED,
            total_pages=None,
            created_at=timestamp,
            updated_at=timestamp,
            error_message=None,
        )

        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO documents (
                        document_id,
                        filename,
                        original_path,
                        status,
                        total_pages,
                        created_at,
                        updated_at,
                        error_message
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        document.document_id,
                        document.filename,
                        document.original_path,
                        document.status.value,
                        document.total_pages,
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

        normalized_payload = self._normalize_pass1_artifact(document_id, page_number, payload)
        temp_path = target_path.parent / f".{target_path.name}.{uuid4().hex}.tmp"

        try:
            temp_path.write_text(
                json.dumps(normalized_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            loaded_payload = json.loads(temp_path.read_text(encoding="utf-8"))
            self._normalize_pass1_artifact(document_id, page_number, loaded_payload)
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
            state["pass1_text_first_pages"] = len(pass1_summary.get("text_first_pages", []))
            state["pass1_multimodal_pages"] = len(pass1_summary.get("multimodal_pages", []))
            state["pass1_escalated_pages"] = len(pass1_summary.get("escalated_pages", []))

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

    def increment_openai_call_count(self, document_id: str, stage_name: str) -> None:
        counter_field = _OPENAI_CALL_COUNT_FIELDS_BY_STAGE.get(stage_name)
        if counter_field is None:
            return

        with self._benchmark_lock:
            state = self._benchmark_states.get(document_id)
            if state is None:
                return
            state[counter_field] = int(state.get(counter_field, 0)) + 1

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
        completed_page_count = pass2_completed_pages
        completion_ratio = (
            round(completed_page_count / total_pages, 4)
            if total_pages and total_pages > 0
            else 0.0
        )

        synthesis_ready, summary_error_message = self._get_document_summary_health(document_id)
        error_message = document.error_message
        if (
            not error_message
            and document.status in {DocumentStatus.COMPLETED, DocumentStatus.FAILED}
            and summary_error_message is not None
        ):
            error_message = summary_error_message

        has_errors = failed_page_count > 0 or bool(error_message)
        ready_for_viewer = document.status is DocumentStatus.COMPLETED
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
            "pass2_completed_pages": pass2_completed_pages,
            "pass2_failed_pages": len(pass2_failed_page_numbers),
            "ready_for_viewer": ready_for_viewer,
            "current_page_number": current_page_number,
            "error_message": error_message,
            "has_errors": has_errors,
            "failed_page_count": failed_page_count,
            "completed_page_count": completed_page_count,
            "completion_ratio": completion_ratio,
            "recent_failures": recent_failures,
        }

    def resolve_relative_path(self, relative_path: str) -> Path:
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
        return (PROJECT_ROOT / configured_path).resolve()

    def _enum_value_list(self, enum_type: type[DocumentStatus] | type[RenderStatus] | type[StageStatus]) -> str:
        return ", ".join(f"'{member.value}'" for member in enum_type)

    def _row_to_document(self, row: tuple[object, ...]) -> DocumentRecord:
        return DocumentRecord(
            document_id=str(row[0]),
            filename=str(row[1]),
            original_path=str(row[2]),
            status=DocumentStatus(str(row[3])),
            total_pages=int(row[4]) if row[4] is not None else None,
            created_at=datetime.fromisoformat(str(row[5])),
            updated_at=datetime.fromisoformat(str(row[6])),
            error_message=str(row[7]) if row[7] is not None else None,
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
        validated_result = validate_payload("pass1", normalized_result)

        return {
            "meta": self._normalize_pass1_meta(meta),
            "result": validated_result,
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

        for field_name in _BENCHMARK_COUNT_FIELDS - {"openai_call_count_total"}:
            count = int(normalized_payload[field_name])
            if count < 0:
                raise ValueError(f"{field_name} must be >= 0.")
            normalized_payload[field_name] = count

        normalized_payload["openai_call_count_total"] = (
            int(normalized_payload["openai_pass1_call_count"])
            + int(normalized_payload["openai_synthesis_call_count"])
            + int(normalized_payload["openai_pass2_call_count"])
        )

        final_status = str(normalized_payload["final_status"]).strip()
        if final_status not in {status.value for status in DocumentStatus}:
            raise ValueError("final_status must be a valid DocumentStatus value.")
        normalized_payload["final_status"] = final_status

        document_parser_backend = str(normalized_payload["document_parser_backend"]).strip()
        if document_parser_backend not in {"stub", "pymupdf4llm"}:
            raise ValueError("document_parser_backend must be 'stub' or 'pymupdf4llm'.")
        normalized_payload["document_parser_backend"] = document_parser_backend

        pass1_routing_mode = str(normalized_payload["pass1_routing_mode"]).strip()
        if pass1_routing_mode not in {"legacy", "hybrid"}:
            raise ValueError("pass1_routing_mode must be 'legacy' or 'hybrid'.")
        normalized_payload["pass1_routing_mode"] = pass1_routing_mode

        for field_name in (
            "openai_model_pass1",
            "openai_model_synthesis",
            "openai_model_pass2",
            "reasoning_effort_pass1",
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
        if openai_timeout_seconds < 0:
            raise ValueError("openai_timeout_seconds must be >= 0.")
        if openai_max_retries < 0:
            raise ValueError("openai_max_retries must be >= 0.")
        if analysis_image_long_edge < 0:
            raise ValueError("analysis_image_long_edge must be >= 0.")
        normalized_payload["openai_timeout_seconds"] = openai_timeout_seconds
        normalized_payload["openai_max_retries"] = openai_max_retries
        normalized_payload["analysis_image_long_edge"] = analysis_image_long_edge

        normalized_payload["parse_artifact_reused"] = bool(normalized_payload["parse_artifact_reused"])
        normalized_payload["page_manifest_reused"] = bool(normalized_payload["page_manifest_reused"])

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
            "meta": {
                "schema_version": str(meta["schema_version"]),
                "prompt_version": str(meta["prompt_version"]),
                "model_name": str(meta["model_name"]),
                "generated_at": str(meta["generated_at"]),
            },
            "result": validated_result,
        }

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
            return ProcessingStage.PASS2

        if status in {DocumentStatus.COMPLETED, DocumentStatus.FAILED}:
            if "document synthesis" in normalized_error or "coverage_threshold" in normalized_error:
                return ProcessingStage.SYNTHESIS
            if "pass2" in normalized_error:
                return ProcessingStage.PASS2
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

    def _benchmark_defaults(self, document_id: str) -> dict[str, object]:
        return {
            "document_id": document_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_processing_time_seconds": 0.0,
            "render_time_seconds": 0.0,
            "parse_time_seconds": 0.0,
            "triage_time_seconds": 0.0,
            "pass1_time_seconds": 0.0,
            "synthesis_time_seconds": 0.0,
            "pass2_time_seconds": 0.0,
            "rendered_pages": 0,
            "pass1_text_first_pages": 0,
            "pass1_multimodal_pages": 0,
            "pass1_escalated_pages": 0,
            "pass2_completed_pages": 0,
            "pass2_failed_pages": 0,
            "openai_call_count_total": 0,
            "openai_pass1_call_count": 0,
            "openai_synthesis_call_count": 0,
            "openai_pass2_call_count": 0,
            "document_parser_backend": self.settings.document_parser_backend,
            "pass1_routing_mode": self.settings.pass1_routing_mode,
            "openai_model_pass1": self.settings.stage_config("pass1").model_name,
            "openai_model_synthesis": self.settings.stage_config("document_synthesis").model_name,
            "openai_model_pass2": self.settings.stage_config("pass2").model_name,
            "reasoning_effort_pass1": self.settings.stage_config("pass1").reasoning_effort,
            "reasoning_effort_synthesis": self.settings.stage_config("document_synthesis").reasoning_effort,
            "reasoning_effort_pass2": self.settings.stage_config("pass2").reasoning_effort,
            "openai_timeout_seconds": self.settings.openai_timeout_seconds,
            "openai_max_retries": self.settings.openai_max_retries,
            "analysis_image_long_edge": 0,
            "parse_artifact_reused": False,
            "page_manifest_reused": False,
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
