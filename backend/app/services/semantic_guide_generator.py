from __future__ import annotations

import json
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Callable

from app.models.document import RenderStatus, StageStatus
from app.services.analysis_client import AnalysisClient
from app.services.codex_cli_client import CodexCLIClient
from app.services.llm_provider import get_analysis_client
from app.services.page_context_builder import PageContextBuilder
from app.services.storage import StorageService, get_storage_service


class SemanticGuideGenerationError(RuntimeError):
    def __init__(self, message: str, *, attempts_used: int = 0) -> None:
        super().__init__(message)
        self.attempts_used = attempts_used


class SemanticGuideGenerator:
    """Generate required document/page semantic guides from compact parser digests."""

    def __init__(
        self,
        storage: StorageService | None = None,
        analysis_client: AnalysisClient | None = None,
        page_context_builder: PageContextBuilder | None = None,
    ) -> None:
        self.storage = storage or get_storage_service()
        if analysis_client is not None:
            self.analysis_client = analysis_client
        elif self.storage.settings.llm_provider == "mock":
            self.analysis_client = get_analysis_client(storage=self.storage)
        else:
            self.analysis_client = CodexCLIClient(settings=self.storage.settings, storage=self.storage)
        self.page_context_builder = page_context_builder or PageContextBuilder(settings=self.storage.settings)

    def generate_document(self, document_id: str) -> dict[str, Any]:
        if self.storage.settings.semantic_guide_mode == "legacy_single_call":
            return self._generate_legacy_single_call(document_id)
        return self.generate_full_chunked_document(document_id)

    def generate_full_chunked_document(self, document_id: str) -> dict[str, Any]:
        loaded = self._load_required_page_contexts(document_id)
        if loaded.get("error_message"):
            return self._failed_result(document_id, str(loaded["error_message"]))

        response_language = str(loaded["response_language"])
        rendered_pages = list(loaded["rendered_pages"])
        page_contexts = list(loaded["page_contexts"])
        expected_page_numbers = [int(page_context["page_number"]) for page_context in page_contexts]
        total_rendered_pages = len(rendered_pages)
        page_context_completed_pages = len(page_contexts)
        missing_pages = list(loaded["missing_pages"])
        if missing_pages:
            return self._failed_result(
                document_id,
                "Parser-first PageContext is missing for required pages: "
                + ", ".join(str(page) for page in missing_pages),
            )

        chunk_size = max(1, int(self.storage.settings.semantic_guide_page_chunk_size))
        chunks = [
            page_contexts[index : index + chunk_size]
            for index in range(0, len(page_contexts), chunk_size)
        ]
        total_chunks = len(chunks)
        retry_attempts = max(1, int(self.storage.settings.semantic_guide_retry_attempts))
        document_guide_call_count = 0
        page_guide_chunk_call_count = 0
        document_guide_time_seconds = 0.0
        page_guide_chunks_time_seconds = 0.0

        self._save_semantic_status(
            document_id,
            stage="document_guide",
            completed_chunks=0,
            total_chunks=total_chunks,
            failed_chunks=0,
        )

        try:
            digest = self._build_digest(
                document_id=document_id,
                page_contexts=page_contexts,
                response_language=response_language,
            )
            digest_size_chars = self._json_size(digest)

            document_guide_started = perf_counter()
            try:
                document_guide_envelope, attempts_used = self._run_with_retries(
                    lambda: self.generate_document_guide(
                        document_id=document_id,
                        document_digest=digest,
                    ),
                    retry_attempts=retry_attempts,
                )
            except SemanticGuideGenerationError as exc:
                document_guide_call_count += exc.attempts_used
                document_guide_time_seconds = round(perf_counter() - document_guide_started, 4)
                self._save_semantic_status(
                    document_id,
                    stage="failed",
                    completed_chunks=0,
                    total_chunks=total_chunks,
                    failed_chunks=1,
                )
                return self._failed_result(
                    document_id,
                    f"DocumentGuide failed after {exc.attempts_used} attempt(s): {exc}",
                    document_guide_call_count=document_guide_call_count,
                    page_guide_chunk_call_count=page_guide_chunk_call_count,
                    semantic_guide_completed_chunks=0,
                    semantic_guide_total_chunks=total_chunks,
                    semantic_guide_failed_chunks=1,
                    document_guide_time_seconds=document_guide_time_seconds,
                    page_guide_chunks_time_seconds=page_guide_chunks_time_seconds,
                    digest_size_chars=digest_size_chars,
                )
            document_guide_call_count += attempts_used
            document_guide_time_seconds = round(perf_counter() - document_guide_started, 4)
            document_guide_envelope = self._with_document_guide_meta(
                envelope=document_guide_envelope,
                total_rendered_pages=total_rendered_pages,
                page_context_completed_pages=page_context_completed_pages,
                digest_size_chars=digest_size_chars,
                response_language=response_language,
            )
            document_guide_path = self.storage.save_document_guide(
                document_id,
                document_guide_envelope,
            )
            normalized_document_guide = self.storage.load_document_guide(document_id)
            if normalized_document_guide is None:
                raise ValueError("Document guide was saved but could not be reloaded.")
            document_guide = dict(normalized_document_guide["result"]["document_guide"])

            page_guides: list[dict[str, Any]] = []
            chunk_paths: list[str] = []
            self._save_semantic_status(
                document_id,
                stage="page_guide_chunks",
                completed_chunks=0,
                total_chunks=total_chunks,
                failed_chunks=0,
            )
            page_chunks_started = perf_counter()
            for chunk_index, chunk_contexts in enumerate(chunks, start=1):
                page_numbers = [int(page_context["page_number"]) for page_context in chunk_contexts]
                chunk_digest = self._build_page_chunk_digest(
                    document_id=document_id,
                    page_contexts=chunk_contexts,
                    response_language=response_language,
                    page_numbers=page_numbers,
                    chunk_index=chunk_index,
                    total_chunks=total_chunks,
                    all_page_numbers=expected_page_numbers,
                )
                chunk_digest_size_chars = self._json_size(chunk_digest)
                try:
                    chunk_envelope, attempts_used = self._run_with_retries(
                        lambda: self.generate_page_guide_chunk(
                            document_id=document_id,
                            page_contexts_chunk=chunk_contexts,
                            document_guide=document_guide,
                            chunk_index=chunk_index,
                            total_chunks=total_chunks,
                            page_digest=chunk_digest,
                        ),
                        retry_attempts=retry_attempts,
                    )
                except SemanticGuideGenerationError as exc:
                    page_guide_chunk_call_count += exc.attempts_used
                    self._save_semantic_status(
                        document_id,
                        stage="failed",
                        completed_chunks=len(chunk_paths),
                        total_chunks=total_chunks,
                        failed_chunks=1,
                        failed_chunk_ranges=[self._page_range_label(page_numbers)],
                    )
                    return self._failed_result(
                        document_id,
                        (
                            f"PageGuide chunk {chunk_index}/{total_chunks} "
                            f"({self._page_range_label(page_numbers)}) failed after "
                            f"{exc.attempts_used} attempt(s): {exc}"
                        ),
                        document_guide_call_count=document_guide_call_count,
                        page_guide_chunk_call_count=page_guide_chunk_call_count,
                        page_guide_count=len(page_guides),
                        semantic_guide_completed_chunks=len(chunk_paths),
                        semantic_guide_total_chunks=total_chunks,
                        semantic_guide_failed_chunks=1,
                        document_guide_time_seconds=document_guide_time_seconds,
                        page_guide_chunks_time_seconds=round(
                            perf_counter() - page_chunks_started,
                            4,
                        ),
                        digest_size_chars=digest_size_chars,
                    )

                page_guide_chunk_call_count += attempts_used
                chunk_envelope = self._with_page_chunk_meta(
                    envelope=chunk_envelope,
                    chunk_index=chunk_index,
                    total_chunks=total_chunks,
                    page_numbers=page_numbers,
                    digest_size_chars=chunk_digest_size_chars,
                    response_language=response_language,
                )
                chunk_path = self.storage.save_page_guide_chunk(
                    document_id,
                    page_numbers,
                    chunk_envelope,
                )
                chunk_paths.append(chunk_path)
                normalized_chunk = self.storage.load_page_guide_chunk(document_id, page_numbers)
                if normalized_chunk is None:
                    raise ValueError(
                        f"PageGuide chunk {chunk_index}/{total_chunks} was saved but could not be reloaded."
                    )
                page_guides.extend(
                    dict(page_guide)
                    for page_guide in normalized_chunk["result"].get("page_guides", [])
                    if isinstance(page_guide, dict)
                )
                self._save_semantic_status(
                    document_id,
                    stage="page_guide_chunks",
                    completed_chunks=len(chunk_paths),
                    total_chunks=total_chunks,
                    failed_chunks=0,
                )

            page_guide_chunks_time_seconds = round(perf_counter() - page_chunks_started, 4)
            page_guides = self._merged_required_page_guides(
                document_id=document_id,
                expected_page_numbers=expected_page_numbers,
                page_guides=page_guides,
            )

            self._save_semantic_status(
                document_id,
                stage="merge",
                completed_chunks=total_chunks,
                total_chunks=total_chunks,
                failed_chunks=0,
            )
            semantic_envelope = self._combined_semantic_envelope(
                document_id=document_id,
                document_guide=document_guide,
                page_guides=page_guides,
                document_guide_envelope=normalized_document_guide,
                total_rendered_pages=total_rendered_pages,
                page_context_completed_pages=page_context_completed_pages,
                digest_size_chars=digest_size_chars,
                response_language=response_language,
                document_guide_call_count=document_guide_call_count,
                page_guide_chunk_call_count=page_guide_chunk_call_count,
                page_guide_chunk_size=chunk_size,
                page_guide_chunk_count=total_chunks,
            )
            semantic_saved_path = self.storage.save_semantic_guide(document_id, semantic_envelope)
            normalized_semantic = self.storage.load_semantic_guide(document_id)
            if normalized_semantic is None:
                raise ValueError("Semantic guide was saved but could not be reloaded.")

            summary_saved_path = self.storage.save_document_summary(
                document_id,
                self._document_summary_from_semantic(
                    document_id=document_id,
                    semantic_artifact=normalized_semantic,
                    total_rendered_pages=total_rendered_pages,
                    page_context_completed_pages=page_context_completed_pages,
                    missing_pages=missing_pages,
                ),
            )
            updated_page_guides = self._apply_page_guides_to_pass1(
                document_id=document_id,
                semantic_artifact=normalized_semantic,
            )
            if updated_page_guides != total_rendered_pages:
                raise ValueError(
                    f"Applied {updated_page_guides}/{total_rendered_pages} required PageGuides."
                )

            self._save_semantic_status(
                document_id,
                stage="completed",
                completed_chunks=total_chunks,
                total_chunks=total_chunks,
                failed_chunks=0,
            )
        except Exception as exc:
            self._save_semantic_status(
                document_id,
                stage="failed",
                completed_chunks=0,
                total_chunks=total_chunks,
                failed_chunks=1,
            )
            return self._failed_result(
                document_id,
                str(exc),
                document_guide_call_count=document_guide_call_count,
                page_guide_chunk_call_count=page_guide_chunk_call_count,
                page_guide_count=0,
                semantic_guide_completed_chunks=0,
                semantic_guide_total_chunks=total_chunks,
                semantic_guide_failed_chunks=1,
                document_guide_time_seconds=document_guide_time_seconds,
                page_guide_chunks_time_seconds=page_guide_chunks_time_seconds,
                digest_size_chars=locals().get("digest_size_chars", 0),
            )

        return {
            "document_id": document_id,
            "semantic_guide_status": StageStatus.COMPLETED.value,
            "synthesis_status": StageStatus.COMPLETED.value,
            "saved_path": summary_saved_path,
            "semantic_guide_path": semantic_saved_path,
            "document_guide_path": document_guide_path,
            "page_guide_chunk_paths": chunk_paths,
            "total_rendered_pages": total_rendered_pages,
            "pass1_completed_pages": page_context_completed_pages,
            "page_context_completed_pages": page_context_completed_pages,
            "missing_pages": sorted(set(missing_pages)),
            "coverage_ratio": round(page_context_completed_pages / max(1, total_rendered_pages), 4),
            "partial_input_used": bool(missing_pages),
            "coverage_threshold": 1,
            "used_pages": expected_page_numbers,
            "page_guide_count": updated_page_guides,
            "semantic_guide_call_count": document_guide_call_count + page_guide_chunk_call_count,
            "document_guide_call_count": document_guide_call_count,
            "page_guide_chunk_call_count": page_guide_chunk_call_count,
            "semantic_guide_completed_chunks": total_chunks,
            "semantic_guide_total_chunks": total_chunks,
            "semantic_guide_failed_chunks": 0,
            "document_guide_time_seconds": document_guide_time_seconds,
            "page_guide_chunks_time_seconds": page_guide_chunks_time_seconds,
            "digest_size_chars": digest_size_chars,
            "semantic_guide_mode": "chunked_full_required",
            "error_message": None,
        }

    def generate_document_guide(
        self,
        *,
        document_id: str,
        document_digest: dict[str, Any],
    ) -> dict[str, Any]:
        return self.analysis_client.run_document_guide(
            document_id=document_id,
            document_digest=document_digest,
        )

    def generate_page_guide_chunk(
        self,
        *,
        document_id: str,
        page_contexts_chunk: list[dict[str, Any]],
        document_guide: dict[str, Any],
        chunk_index: int,
        total_chunks: int,
        page_digest: dict[str, Any],
    ) -> dict[str, Any]:
        page_numbers = [int(page_context["page_number"]) for page_context in page_contexts_chunk]
        return self.analysis_client.run_page_guide_chunk(
            document_id=document_id,
            chunk_index=chunk_index,
            total_chunks=total_chunks,
            page_numbers=page_numbers,
            document_guide=document_guide,
            page_digest=page_digest,
        )

    def _generate_legacy_single_call(self, document_id: str) -> dict[str, Any]:
        loaded = self._load_required_page_contexts(document_id)
        if loaded.get("error_message"):
            return self._failed_result(document_id, str(loaded["error_message"]))

        response_language = str(loaded["response_language"])
        rendered_pages = list(loaded["rendered_pages"])
        page_contexts = list(loaded["page_contexts"])
        missing_pages = list(loaded["missing_pages"])
        if not page_contexts:
            return self._failed_result(document_id, "No parser-first page contexts are available.")

        digest = self._build_digest(
            document_id=document_id,
            page_contexts=page_contexts,
            response_language=response_language,
        )
        digest_size_chars = self._json_size(digest)

        try:
            envelope = self.analysis_client.run_semantic_guide(
                document_id=document_id,
                document_digest=digest,
            )
            semantic_envelope = self._with_legacy_semantic_meta(
                envelope=envelope,
                total_rendered_pages=len(rendered_pages),
                page_context_completed_pages=len(page_contexts),
                digest_size_chars=digest_size_chars,
                response_language=response_language,
            )
            semantic_saved_path = self.storage.save_semantic_guide(document_id, semantic_envelope)
            normalized_semantic = self.storage.load_semantic_guide(document_id)
            if normalized_semantic is None:
                raise ValueError("Semantic guide was saved but could not be reloaded.")

            summary_saved_path = self.storage.save_document_summary(
                document_id,
                self._document_summary_from_semantic(
                    document_id=document_id,
                    semantic_artifact=normalized_semantic,
                    total_rendered_pages=len(rendered_pages),
                    page_context_completed_pages=len(page_contexts),
                    missing_pages=missing_pages,
                ),
            )
            updated_page_guides = self._apply_page_guides_to_pass1(
                document_id=document_id,
                semantic_artifact=normalized_semantic,
            )
        except Exception as exc:
            return self._failed_result(document_id, str(exc), digest_size_chars=digest_size_chars)

        return {
            "document_id": document_id,
            "semantic_guide_status": StageStatus.COMPLETED.value,
            "synthesis_status": StageStatus.COMPLETED.value,
            "saved_path": summary_saved_path,
            "semantic_guide_path": semantic_saved_path,
            "total_rendered_pages": len(rendered_pages),
            "pass1_completed_pages": len(page_contexts),
            "page_context_completed_pages": len(page_contexts),
            "missing_pages": sorted(set(missing_pages)),
            "coverage_ratio": round(len(page_contexts) / max(1, len(rendered_pages)), 4),
            "partial_input_used": bool(missing_pages),
            "coverage_threshold": 1,
            "used_pages": [int(page_context["page_number"]) for page_context in page_contexts],
            "page_guide_count": updated_page_guides,
            "semantic_guide_call_count": 1,
            "document_guide_call_count": 0,
            "page_guide_chunk_call_count": 0,
            "semantic_guide_completed_chunks": 0,
            "semantic_guide_total_chunks": 0,
            "semantic_guide_failed_chunks": 0,
            "document_guide_time_seconds": 0.0,
            "page_guide_chunks_time_seconds": 0.0,
            "digest_size_chars": digest_size_chars,
            "semantic_guide_mode": "legacy_single_call",
            "error_message": None,
        }

    def _load_required_page_contexts(self, document_id: str) -> dict[str, Any]:
        document = self.storage.get_document(document_id)
        response_language = document.response_language if document is not None else "ko"
        page_records = self.storage.get_pages(document_id)
        rendered_pages = [
            page for page in page_records if page.render_status is RenderStatus.RENDERED
        ]
        if not rendered_pages:
            return {"error_message": "No rendered pages are available."}

        page_contexts: list[dict[str, Any]] = []
        missing_pages: list[int] = []
        for page in sorted(rendered_pages, key=lambda item: item.page_number):
            if page.pass1_status is not StageStatus.COMPLETED:
                missing_pages.append(page.page_number)
                continue
            try:
                page_context = self.storage.load_page_context(document_id, page.page_number)
            except ValueError:
                page_context = None
            if page_context is None:
                missing_pages.append(page.page_number)
                continue
            page_contexts.append(dict(page_context))

        return {
            "response_language": response_language,
            "rendered_pages": rendered_pages,
            "page_contexts": page_contexts,
            "missing_pages": sorted(set(missing_pages)),
        }

    def _build_digest(
        self,
        *,
        document_id: str,
        page_contexts: list[dict[str, Any]],
        response_language: str,
    ) -> dict[str, Any]:
        digest = self.page_context_builder.build_document_digest(
            document_id=document_id,
            page_contexts=page_contexts,
        )
        digest["response_language"] = response_language
        digest["response_language_instruction"] = (
            "Write explanatory guide prose in English. Preserve PDF/deck wording for source-derived "
            "concept names, page titles, section titles, captions, formulas, acronyms, and snippets."
            if response_language == "en"
            else "Write explanatory guide prose in Korean. Preserve PDF/deck wording for source-derived "
            "concept names, page titles, section titles, captions, formulas, acronyms, and snippets."
        )
        digest["source_text_policy"] = {
            "preserve_source_terms": True,
            "rule": (
                "Explanation prose follows response_language; source-derived labels and snippets keep the "
                "exact PDF/deck language and wording."
            ),
        }
        return digest

    def _build_page_chunk_digest(
        self,
        *,
        document_id: str,
        page_contexts: list[dict[str, Any]],
        response_language: str,
        page_numbers: list[int],
        chunk_index: int,
        total_chunks: int,
        all_page_numbers: list[int],
    ) -> dict[str, Any]:
        digest = self._build_digest(
            document_id=document_id,
            page_contexts=page_contexts,
            response_language=response_language,
        )
        digest["chunk_index"] = chunk_index
        digest["total_chunks"] = total_chunks
        digest["requested_page_numbers"] = page_numbers
        digest["previous_page_number"] = self._neighbor_page_number(
            all_page_numbers,
            page_numbers[0],
            offset=-1,
        )
        digest["next_page_number"] = self._neighbor_page_number(
            all_page_numbers,
            page_numbers[-1],
            offset=1,
        )
        return digest

    def _neighbor_page_number(
        self,
        all_page_numbers: list[int],
        page_number: int,
        *,
        offset: int,
    ) -> int | None:
        try:
            index = all_page_numbers.index(page_number)
        except ValueError:
            return None
        neighbor_index = index + offset
        if neighbor_index < 0 or neighbor_index >= len(all_page_numbers):
            return None
        return int(all_page_numbers[neighbor_index])

    def _run_with_retries(
        self,
        call: Callable[[], dict[str, Any]],
        *,
        retry_attempts: int,
    ) -> tuple[dict[str, Any], int]:
        attempts_used = 0
        last_error: Exception | None = None
        for _ in range(max(1, retry_attempts)):
            attempts_used += 1
            try:
                return call(), attempts_used
            except Exception as exc:
                last_error = exc
        raise SemanticGuideGenerationError(
            str(last_error) if last_error is not None else "Unknown provider failure.",
            attempts_used=attempts_used,
        )

    def _with_document_guide_meta(
        self,
        *,
        envelope: dict[str, Any],
        total_rendered_pages: int,
        page_context_completed_pages: int,
        digest_size_chars: int,
        response_language: str,
    ) -> dict[str, Any]:
        meta = dict(envelope.get("meta") or {})
        meta.update(
            {
                "total_rendered_pages": total_rendered_pages,
                "page_context_completed_pages": page_context_completed_pages,
                "digest_size_chars": digest_size_chars,
                "response_language": response_language,
            }
        )
        return {
            "meta": meta,
            "result": dict(envelope.get("result") or {}),
        }

    def _with_page_chunk_meta(
        self,
        *,
        envelope: dict[str, Any],
        chunk_index: int,
        total_chunks: int,
        page_numbers: list[int],
        digest_size_chars: int,
        response_language: str,
    ) -> dict[str, Any]:
        meta = dict(envelope.get("meta") or {})
        meta.update(
            {
                "chunk_index": chunk_index,
                "total_chunks": total_chunks,
                "page_numbers": page_numbers,
                "digest_size_chars": digest_size_chars,
                "response_language": response_language,
            }
        )
        return {
            "meta": meta,
            "result": dict(envelope.get("result") or {}),
        }

    def _with_legacy_semantic_meta(
        self,
        *,
        envelope: dict[str, Any],
        total_rendered_pages: int,
        page_context_completed_pages: int,
        digest_size_chars: int,
        response_language: str,
    ) -> dict[str, Any]:
        meta = dict(envelope.get("meta") or {})
        meta.update(
            {
                "total_rendered_pages": total_rendered_pages,
                "page_context_completed_pages": page_context_completed_pages,
                "semantic_guide_call_count": 1,
                "digest_size_chars": digest_size_chars,
                "response_language": response_language,
                "semantic_guide_mode": "legacy_single_call",
            }
        )
        return {
            "meta": meta,
            "result": dict(envelope.get("result") or {}),
        }

    def _combined_semantic_envelope(
        self,
        *,
        document_id: str,
        document_guide: dict[str, Any],
        page_guides: list[dict[str, Any]],
        document_guide_envelope: dict[str, Any],
        total_rendered_pages: int,
        page_context_completed_pages: int,
        digest_size_chars: int,
        response_language: str,
        document_guide_call_count: int,
        page_guide_chunk_call_count: int,
        page_guide_chunk_size: int,
        page_guide_chunk_count: int,
    ) -> dict[str, Any]:
        document_meta = dict(document_guide_envelope.get("meta") or {})
        return {
            "meta": {
                "schema_version": self.storage.settings.schema_version,
                "prompt_version": self.storage.settings.stage_config("semantic_guide").prompt_version,
                "model_name": str(document_meta.get("model_name") or "semantic-guide-chunked"),
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "total_rendered_pages": total_rendered_pages,
                "page_context_completed_pages": page_context_completed_pages,
                "semantic_guide_call_count": document_guide_call_count + page_guide_chunk_call_count,
                "document_guide_call_count": document_guide_call_count,
                "page_guide_chunk_call_count": page_guide_chunk_call_count,
                "page_guide_chunk_size": page_guide_chunk_size,
                "page_guide_chunk_count": page_guide_chunk_count,
                "semantic_guide_mode": "chunked_full_required",
                "digest_size_chars": digest_size_chars,
                "response_language": response_language,
            },
            "result": {
                "document_id": document_id,
                "document_guide": document_guide,
                "page_guides": page_guides,
            },
        }

    def _merged_required_page_guides(
        self,
        *,
        document_id: str,
        expected_page_numbers: list[int],
        page_guides: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        expected_pages = set(expected_page_numbers)
        by_page: dict[int, dict[str, Any]] = {}
        for page_guide in page_guides:
            page_number = int(page_guide.get("page_number") or 0)
            if page_number not in expected_pages:
                raise ValueError(f"Unexpected PageGuide for page {page_number}.")
            if page_number in by_page:
                raise ValueError(f"Duplicate PageGuide for page {page_number}.")
            normalized_page_guide = dict(page_guide)
            normalized_page_guide["document_id"] = document_id
            by_page[page_number] = normalized_page_guide
        missing_pages = expected_pages - set(by_page.keys())
        if missing_pages:
            raise ValueError(
                "Missing required PageGuides for pages: "
                + ", ".join(str(page) for page in sorted(missing_pages))
            )
        return [by_page[page_number] for page_number in expected_page_numbers]

    def _document_summary_from_semantic(
        self,
        *,
        document_id: str,
        semantic_artifact: dict[str, Any],
        total_rendered_pages: int,
        page_context_completed_pages: int,
        missing_pages: list[int],
    ) -> dict[str, Any]:
        semantic_meta = dict(semantic_artifact.get("meta") or {})
        result = dict(semantic_artifact["result"])
        document_guide = dict(result["document_guide"])
        sections = [
            {
                "section_id": str(section.get("section_id") or f"section-{index + 1}"),
                "title": str(section.get("title") or f"Section {index + 1}"),
                "pages": [int(page) for page in section.get("pages", [])],
            }
            for index, section in enumerate(document_guide.get("section_structure", []))
            if isinstance(section, dict) and section.get("pages")
        ]
        if not sections:
            used_pages = [
                int(page_guide["page_number"])
                for page_guide in result.get("page_guides", [])
                if isinstance(page_guide, dict) and page_guide.get("page_number")
            ]
            sections = [{"section_id": "section-1", "title": "Document overview", "pages": used_pages or [1]}]

        key_concepts = []
        for concept in document_guide.get("key_concepts", []):
            if not isinstance(concept, dict):
                continue
            term = str(concept.get("concept") or "").strip()
            description = str(concept.get("description") or "").strip()
            pages = [int(page) for page in concept.get("pages", [])]
            if term and description and pages:
                key_concepts.append(
                    {
                        "term": term,
                        "description": description,
                        "pages": pages,
                    }
                )
        if not key_concepts:
            key_concepts = [
                {
                    "term": str(document_guide.get("overall_topic") or "Document topic"),
                    "description": str(document_guide.get("overall_summary") or "Semantic guide topic."),
                    "pages": sections[0]["pages"][:3],
                }
            ]

        return {
            "meta": {
                "schema_version": str(semantic_meta["schema_version"]),
                "prompt_version": str(semantic_meta["prompt_version"]),
                "model_name": str(semantic_meta["model_name"]),
                "generated_at": str(semantic_meta["generated_at"]),
                "total_rendered_pages": total_rendered_pages,
                "pass1_completed_pages": page_context_completed_pages,
                "missing_pages": sorted(set(missing_pages)),
                "coverage_ratio": round(page_context_completed_pages / max(1, total_rendered_pages), 4),
                "partial_input_used": bool(missing_pages),
                "coverage_threshold": 1,
                "response_language": semantic_meta.get("response_language", "ko"),
            },
            "result": {
                "document_id": document_id,
                "overall_topic": document_guide["overall_topic"],
                "overall_summary": document_guide["overall_summary"],
                "sections": sections,
                "key_concepts": key_concepts,
                "difficult_pages": [int(page) for page in document_guide.get("difficult_pages", [])],
                "prerequisite_links": [
                    {
                        "from_page": int(link["from_page"]),
                        "to_page": int(link["to_page"]),
                        "reason": str(link["reason"]),
                    }
                    for link in document_guide.get("prerequisite_links", [])
                    if isinstance(link, dict)
                ],
            },
        }

    def _apply_page_guides_to_pass1(
        self,
        *,
        document_id: str,
        semantic_artifact: dict[str, Any],
    ) -> int:
        result = dict(semantic_artifact["result"])
        page_guides = {
            int(page_guide["page_number"]): dict(page_guide)
            for page_guide in result.get("page_guides", [])
            if isinstance(page_guide, dict) and page_guide.get("page_number")
        }
        updated_count = 0
        for page_number, page_guide in page_guides.items():
            pass1_artifact = self.storage.load_pass1_result(document_id, page_number)
            if pass1_artifact is None:
                continue
            pass1_result = dict(pass1_artifact["result"])
            guide_section = dict(page_guide.get("page_guide") or {})
            pass1_result["page_role"] = str(guide_section.get("page_role") or pass1_result["page_role"])
            pass1_result["page_summary"] = str(
                guide_section.get("one_line_thesis") or pass1_result["page_summary"]
            )
            pass1_result["page_guide"] = self._page_guide_for_pass1(page_guide)
            pass1_result["wrap_up"] = self._wrap_up_for_pass1(page_guide)
            self.storage.save_pass1_result(
                document_id,
                page_number,
                {
                    "meta": dict(pass1_artifact["meta"]),
                    "result": pass1_result,
                },
            )
            updated_count += 1
        return updated_count

    def _page_guide_for_pass1(self, page_guide: dict[str, Any]) -> dict[str, Any]:
        guide_section = page_guide.get("page_guide")
        if isinstance(guide_section, dict):
            return dict(guide_section)
        return {
            "page_role": page_guide.get("page_role"),
            "previous_slide_connection": page_guide.get("previous_slide_connection"),
            "one_line_thesis": page_guide.get("one_line_thesis"),
        }

    def _wrap_up_for_pass1(self, page_guide: dict[str, Any]) -> dict[str, Any]:
        wrap_up = page_guide.get("wrap_up")
        if isinstance(wrap_up, dict):
            return dict(wrap_up)
        return {
            "logic_flow": list(page_guide.get("logic_flow") or []),
            "study_focus": page_guide.get("study_focus"),
            "must_remember": list(page_guide.get("must_remember") or []),
            "next_slide_connection": page_guide.get("next_slide_connection"),
        }

    def _save_semantic_status(
        self,
        document_id: str,
        *,
        stage: str,
        completed_chunks: int,
        total_chunks: int,
        failed_chunks: int,
        failed_chunk_ranges: list[str] | None = None,
    ) -> None:
        self.storage.save_semantic_status(
            document_id,
            {
                "semantic_guide_stage": stage,
                "semantic_guide_completed_chunks": completed_chunks,
                "semantic_guide_total_chunks": total_chunks,
                "semantic_guide_failed_chunks": failed_chunks,
                "failed_chunk_ranges": failed_chunk_ranges or [],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    def _json_size(self, payload: dict[str, Any]) -> int:
        return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))

    def _page_range_label(self, page_numbers: list[int]) -> str:
        normalized_pages = sorted({int(page) for page in page_numbers})
        if not normalized_pages:
            return "pages unknown"
        if normalized_pages[0] == normalized_pages[-1]:
            return f"page {normalized_pages[0]}"
        return f"pages {normalized_pages[0]}-{normalized_pages[-1]}"

    def _failed_result(
        self,
        document_id: str,
        error_message: str,
        *,
        document_guide_call_count: int = 0,
        page_guide_chunk_call_count: int = 0,
        page_guide_count: int = 0,
        semantic_guide_completed_chunks: int = 0,
        semantic_guide_total_chunks: int = 0,
        semantic_guide_failed_chunks: int = 0,
        document_guide_time_seconds: float = 0.0,
        page_guide_chunks_time_seconds: float = 0.0,
        digest_size_chars: int = 0,
    ) -> dict[str, Any]:
        return {
            "document_id": document_id,
            "semantic_guide_status": StageStatus.FAILED.value,
            "synthesis_status": StageStatus.FAILED.value,
            "saved_path": None,
            "semantic_guide_path": None,
            "total_rendered_pages": 0,
            "pass1_completed_pages": 0,
            "page_context_completed_pages": 0,
            "missing_pages": [],
            "coverage_ratio": 0.0,
            "partial_input_used": False,
            "coverage_threshold": 1,
            "used_pages": [],
            "page_guide_count": page_guide_count,
            "semantic_guide_call_count": document_guide_call_count + page_guide_chunk_call_count,
            "document_guide_call_count": document_guide_call_count,
            "page_guide_chunk_call_count": page_guide_chunk_call_count,
            "semantic_guide_completed_chunks": semantic_guide_completed_chunks,
            "semantic_guide_total_chunks": semantic_guide_total_chunks,
            "semantic_guide_failed_chunks": semantic_guide_failed_chunks,
            "document_guide_time_seconds": round(float(document_guide_time_seconds), 4),
            "page_guide_chunks_time_seconds": round(float(page_guide_chunks_time_seconds), 4),
            "digest_size_chars": digest_size_chars,
            "semantic_guide_mode": self.storage.settings.semantic_guide_mode,
            "error_message": error_message,
        }
