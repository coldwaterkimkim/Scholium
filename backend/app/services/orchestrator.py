from __future__ import annotations

import logging
from threading import Lock, Thread
from time import perf_counter

from starlette.concurrency import run_in_threadpool

from app.models.document import DocumentStatus, ProcessingStage, StageStatus
from app.models.parser import DocumentParseArtifact
from app.services.document_parser import DocumentParser, get_default_document_parser
from app.services.document_synthesizer import DocumentSynthesizer
from app.services.pass1_analyzer import Pass1Analyzer
from app.services.pass2_refiner import Pass2Refiner
from app.services.pdf_triage import PdfTriageService, get_pdf_triage_service
from app.services.pdf_render import RENDER_LONG_EDGE_PIXELS
from app.services.storage import StorageService, get_storage_service
from app.workers.render_worker import RenderWorker


logger = logging.getLogger(__name__)


class DocumentOrchestrator:
    def __init__(
        self,
        storage: StorageService | None = None,
        render_worker: RenderWorker | None = None,
        document_parser: DocumentParser | None = None,
        pdf_triage: PdfTriageService | None = None,
        pass1_analyzer: Pass1Analyzer | None = None,
        document_synthesizer: DocumentSynthesizer | None = None,
        pass2_refiner: Pass2Refiner | None = None,
    ) -> None:
        self.storage = storage or get_storage_service()
        self.render_worker = render_worker or RenderWorker(storage=self.storage)
        self.document_parser = document_parser or get_default_document_parser(self.storage.settings)
        self.pdf_triage = pdf_triage or get_pdf_triage_service()
        self.pass1_analyzer = pass1_analyzer or Pass1Analyzer(storage=self.storage)
        self.document_synthesizer = document_synthesizer or DocumentSynthesizer(storage=self.storage)
        self.pass2_refiner = pass2_refiner or Pass2Refiner(storage=self.storage)
        self._stage_lock = Lock()
        self._active_stages: dict[str, ProcessingStage] = {}

    def get_stage(self, document_id: str) -> ProcessingStage | None:
        with self._stage_lock:
            return self._active_stages.get(document_id)

    async def run_pipeline_in_background(self, document_id: str) -> None:
        await run_in_threadpool(self.run_pipeline, document_id)

    def run_pipeline(self, document_id: str) -> None:
        pipeline_started_at = perf_counter()
        render_result = None
        parse_context = self._empty_parse_context()
        pass1_summary: dict[str, object] | None = None
        pass2_summary: dict[str, object] | None = None
        synthesis_result: dict[str, object] | None = None

        self._start_processing_benchmark(document_id)
        try:
            self._set_stage(document_id, ProcessingStage.RENDER)
            render_started_at = perf_counter()
            render_result = self.render_worker.render_document(document_id)
            self._record_benchmark_duration(
                document_id,
                "render_time_seconds",
                perf_counter() - render_started_at,
            )
            if render_result.status is DocumentStatus.FAILED or not render_result.rendered_pages:
                self._mark_failed(
                    document_id,
                    "Render produced no usable pages.",
                )
                return

            self.storage.update_document(
                document_id,
                status=DocumentStatus.ANALYZING,
                error_message=None,
            )

            self._clear_stage(document_id)
            if self.storage.settings.pass1_routing_mode == "hybrid":
                parse_context = self._prepare_pass1_inputs_best_effort(document_id)
            else:
                self._start_parse_and_triage_side_step(document_id)
            self._record_parse_context(document_id, parse_context)
            self._set_stage(document_id, ProcessingStage.PASS1)
            pass1_started_at = perf_counter()
            pass1_summary = self.pass1_analyzer.analyze_document(document_id)
            self._record_benchmark_duration(
                document_id,
                "pass1_time_seconds",
                perf_counter() - pass1_started_at,
            )
            self.storage.record_pass1_path_counts(document_id, pass1_summary)
            self.storage.update_document(
                document_id,
                status=DocumentStatus.ANALYZING,
                error_message=None,
            )

            self._set_stage(document_id, ProcessingStage.SYNTHESIS)
            synthesis_started_at = perf_counter()
            synthesis_result = self.document_synthesizer.synthesize_document(document_id)
            self._record_benchmark_duration(
                document_id,
                "synthesis_time_seconds",
                perf_counter() - synthesis_started_at,
            )
            if synthesis_result.get("synthesis_status") != StageStatus.COMPLETED.value:
                self._mark_failed(
                    document_id,
                    self._summarize_error_message(
                        "Document synthesis failed.",
                        synthesis_result.get("error_message"),
                    ),
                )
                return

            self.storage.update_document(
                document_id,
                status=DocumentStatus.ANALYZING,
                error_message=None,
            )

            self._set_stage(document_id, ProcessingStage.PASS2)
            pass2_started_at = perf_counter()
            pass2_summary = self.pass2_refiner.refine_document(document_id)
            self._record_benchmark_duration(
                document_id,
                "pass2_time_seconds",
                perf_counter() - pass2_started_at,
            )
            self.storage.record_pass2_counts(document_id, pass2_summary)

            snapshot = self.storage.get_document_processing_snapshot(
                document_id,
                current_stage=ProcessingStage.PASS2,
            )
            if snapshot is None:
                raise ValueError(f"Document not found during finalization: {document_id}")

            if not snapshot["synthesis_ready"]:
                self._mark_failed(
                    document_id,
                    "Document summary is unavailable.",
                )
                return

            if snapshot["pass2_completed_pages"] <= 0:
                self._mark_failed(
                    document_id,
                    "Pass2 produced no viewer-ready pages.",
                )
                return

            final_error_message = self._build_completion_summary(snapshot)
            self.storage.update_document(
                document_id,
                status=DocumentStatus.COMPLETED,
                error_message=final_error_message,
            )
        except Exception as exc:
            self._mark_failed(
                document_id,
                self._summarize_error_message(
                    f"Pipeline failed during {self._stage_label(self.get_stage(document_id))}.",
                    str(exc),
                ),
            )
        finally:
            self._clear_stage(document_id)
            self._finalize_processing_benchmark(
                document_id=document_id,
                pipeline_started_at=pipeline_started_at,
                render_result=render_result,
                parse_context=parse_context,
                pass1_summary=pass1_summary,
                pass2_summary=pass2_summary,
                synthesis_result=synthesis_result,
            )

    def _build_completion_summary(self, snapshot: dict[str, object]) -> str | None:
        failed_page_count = int(snapshot["failed_page_count"])
        if failed_page_count <= 0:
            return None
        return f"Completed with errors on {failed_page_count} page(s)."

    def _mark_failed(self, document_id: str, message: str) -> None:
        self.storage.update_document(
            document_id,
            status=DocumentStatus.FAILED,
            error_message=message,
        )

    def _start_parse_and_triage_side_step(self, document_id: str) -> None:
        Thread(
            target=self._prepare_pass1_inputs_best_effort,
            args=(document_id,),
            daemon=True,
            name=f"parse-triage-{document_id[:12]}",
        ).start()

    def _prepare_pass1_inputs_best_effort(self, document_id: str) -> dict[str, object]:
        context = self._empty_parse_context()
        document = self.storage.get_document(document_id)
        if document is None:
            logger.warning("Skipping parse/triage for missing document %s.", document_id)
            return context

        pdf_path = self.storage.resolve_relative_path(document.original_path)
        parser_source = self._expected_parser_source()
        parse_artifact = self._load_reusable_parse_artifact(document_id, parser_source)

        if parse_artifact is None:
            parse_started_at = perf_counter()
            try:
                parse_artifact = self.document_parser.parse_document(document_id, pdf_path)
                self.storage.save_parse_artifact(
                    document_id,
                    parse_artifact.model_dump(mode="json"),
                )
                context["parse_available"] = True
            except Exception as exc:
                context["parse_time_seconds"] = round(perf_counter() - parse_started_at, 4)
                logger.warning("Best-effort parse precondition failed for %s: %s", document_id, exc)
                return context
            context["parse_time_seconds"] = round(perf_counter() - parse_started_at, 4)
        else:
            context["parse_available"] = True
            context["parse_artifact_reused"] = True

        if self._has_reusable_page_manifest(document_id, parse_artifact, parser_source):
            context["manifest_available"] = True
            context["page_manifest_reused"] = True
            return context

        triage_started_at = perf_counter()
        try:
            page_manifest = self.pdf_triage.build_page_manifest(
                document_id,
                parse_artifact,
                pdf_path=pdf_path,
            )
            self.storage.save_page_manifest(
                document_id,
                page_manifest.model_dump(mode="json"),
            )
            context["manifest_available"] = True
        except Exception as exc:
            logger.warning("Best-effort page manifest precondition failed for %s: %s", document_id, exc)
        finally:
            context["triage_time_seconds"] = round(perf_counter() - triage_started_at, 4)

        return context

    def _load_reusable_parse_artifact(
        self,
        document_id: str,
        parser_source: str,
    ) -> DocumentParseArtifact | None:
        payload = self.storage.load_parse_artifact(document_id)
        if payload is None:
            return None
        if payload.get("schema_version") != self.storage.settings.parser_schema_version:
            return None
        if payload.get("parser_source") != parser_source:
            return None
        return DocumentParseArtifact.model_validate(payload)

    def _has_reusable_page_manifest(
        self,
        document_id: str,
        parse_artifact: DocumentParseArtifact,
        parser_source: str,
    ) -> bool:
        payload = self.storage.load_page_manifest(document_id)
        if payload is None:
            return False
        if payload.get("schema_version") != self.storage.settings.parser_schema_version:
            return False
        if payload.get("parser_source") != parser_source:
            return False
        manifest_pages = payload.get("pages")
        if not isinstance(manifest_pages, list):
            return False

        manifest_page_numbers = {
            int(page["page_number"])
            for page in manifest_pages
            if isinstance(page, dict) and page.get("page_number") is not None
        }
        parse_page_numbers = {page.page_number for page in parse_artifact.pages}
        return manifest_page_numbers == parse_page_numbers

    def _expected_parser_source(self) -> str:
        parser_source = getattr(self.document_parser, "parser_source", None)
        if parser_source:
            return str(parser_source)
        return self.storage.settings.document_parser_backend

    def _start_processing_benchmark(self, document_id: str) -> None:
        try:
            self.storage.start_processing_benchmark(
                document_id,
                {
                    "analysis_image_long_edge": RENDER_LONG_EDGE_PIXELS,
                },
            )
        except Exception as exc:
            logger.warning("Processing benchmark start failed for %s: %s", document_id, exc)

    def _record_benchmark_duration(
        self,
        document_id: str,
        field_name: str,
        seconds: float,
    ) -> None:
        try:
            self.storage.record_stage_duration(document_id, field_name, seconds)
        except Exception as exc:
            logger.warning(
                "Processing benchmark duration record failed for %s (%s): %s",
                document_id,
                field_name,
                exc,
            )

    def _record_parse_context(
        self,
        document_id: str,
        parse_context: dict[str, object],
    ) -> None:
        try:
            self.storage.record_stage_duration(
                document_id,
                "parse_time_seconds",
                float(parse_context["parse_time_seconds"]),
            )
            self.storage.record_stage_duration(
                document_id,
                "triage_time_seconds",
                float(parse_context["triage_time_seconds"]),
            )
            self.storage.update_processing_benchmark_state(
                document_id,
                {
                    "parse_artifact_reused": bool(parse_context["parse_artifact_reused"]),
                    "page_manifest_reused": bool(parse_context["page_manifest_reused"]),
                },
            )
        except Exception as exc:
            logger.warning("Processing benchmark parse context record failed for %s: %s", document_id, exc)

    def _finalize_processing_benchmark(
        self,
        *,
        document_id: str,
        pipeline_started_at: float,
        render_result: object,
        parse_context: dict[str, object],
        pass1_summary: dict[str, object] | None,
        pass2_summary: dict[str, object] | None,
        synthesis_result: dict[str, object] | None,
    ) -> None:
        try:
            document = self.storage.get_document(document_id)
            snapshot = self.storage.get_document_processing_snapshot(document_id)
            rendered_pages = 0
            if snapshot is not None:
                rendered_pages = int(snapshot["rendered_pages"])
            elif render_result is not None:
                rendered_pages = len(getattr(render_result, "rendered_pages", []))

            if pass1_summary is not None:
                self.storage.record_pass1_path_counts(document_id, pass1_summary)
            if pass2_summary is not None:
                self.storage.record_pass2_counts(document_id, pass2_summary)

            self.storage.record_stage_duration(
                document_id,
                "total_processing_time_seconds",
                perf_counter() - pipeline_started_at,
            )
            self.storage.finalize_processing_benchmark(
                document_id,
                {
                    "analysis_image_long_edge": RENDER_LONG_EDGE_PIXELS,
                    "rendered_pages": rendered_pages,
                    "parse_artifact_reused": bool(parse_context["parse_artifact_reused"]),
                    "page_manifest_reused": bool(parse_context["page_manifest_reused"]),
                    "final_status": (
                        document.status.value if document is not None else DocumentStatus.FAILED.value
                    ),
                    "final_error_message": document.error_message if document is not None else None,
                },
            )
        except Exception as exc:
            logger.warning("Processing benchmark finalize failed for %s: %s", document_id, exc)

    def _empty_parse_context(self) -> dict[str, object]:
        return {
            "parse_available": False,
            "manifest_available": False,
            "parse_artifact_reused": False,
            "page_manifest_reused": False,
            "parse_time_seconds": 0.0,
            "triage_time_seconds": 0.0,
        }

    def _set_stage(self, document_id: str, stage: ProcessingStage) -> None:
        with self._stage_lock:
            self._active_stages[document_id] = stage

    def _clear_stage(self, document_id: str) -> None:
        with self._stage_lock:
            self._active_stages.pop(document_id, None)

    def _stage_label(self, stage: ProcessingStage | None) -> str:
        if stage is None:
            return "pipeline"
        return stage.value

    def _summarize_error_message(self, prefix: str, detail: object | None) -> str:
        normalized_prefix = " ".join(str(prefix).split())
        normalized_detail = " ".join(str(detail or "").split())
        if not normalized_detail:
            return normalized_prefix
        if normalized_detail.lower().startswith("traceback"):
            return normalized_prefix
        max_length = 220
        detail_budget = max_length - len(normalized_prefix) - 1
        if detail_budget <= 0:
            return normalized_prefix
        if len(normalized_detail) > detail_budget:
            normalized_detail = normalized_detail[: max(detail_budget - 3, 0)].rstrip()
            if normalized_detail:
                normalized_detail += "..."
        return f"{normalized_prefix} {normalized_detail}".strip()


_orchestrator_service: DocumentOrchestrator | None = None


def get_document_orchestrator() -> DocumentOrchestrator:
    global _orchestrator_service
    if _orchestrator_service is None:
        _orchestrator_service = DocumentOrchestrator()
    return _orchestrator_service
