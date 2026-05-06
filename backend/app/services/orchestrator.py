from __future__ import annotations

import logging
from threading import Lock, Thread
from time import perf_counter

from starlette.concurrency import run_in_threadpool

from app.models.document import DocumentStatus, ProcessingStage, RenderStatus, StageStatus
from app.models.parser import DocumentPageManifest, DocumentParseArtifact
from app.models.pipeline_v2 import RecommendedExecution
from app.services.document_parser import DocumentParser, get_default_document_parser
from app.services.document_spine_builder import DocumentSpineBuilder
from app.services.document_synthesizer import DocumentSynthesizer
from app.services.pass1_analyzer import Pass1Analyzer
from app.services.pass2_compat_builder import Pass2CompatBuilder
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
        document_spine_builder: DocumentSpineBuilder | None = None,
        pass1_analyzer: Pass1Analyzer | None = None,
        document_synthesizer: DocumentSynthesizer | None = None,
        pass2_refiner: Pass2Refiner | None = None,
        pass2_compat_builder: Pass2CompatBuilder | None = None,
    ) -> None:
        self.storage = storage or get_storage_service()
        self.render_worker = render_worker or RenderWorker(storage=self.storage)
        self.document_parser = document_parser or get_default_document_parser(self.storage.settings)
        self.pdf_triage = pdf_triage or get_pdf_triage_service()
        self.document_spine_builder = document_spine_builder or DocumentSpineBuilder()
        self.pass1_analyzer = pass1_analyzer or Pass1Analyzer(storage=self.storage)
        self.document_synthesizer = document_synthesizer or DocumentSynthesizer(storage=self.storage)
        self.pass2_refiner = pass2_refiner or Pass2Refiner(storage=self.storage)
        self.pass2_compat_builder = pass2_compat_builder or Pass2CompatBuilder(storage=self.storage)
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
        spine_context = self._empty_spine_context()
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
            if self._should_prepare_parse_precondition():
                parse_context = self._prepare_pass1_inputs_best_effort(document_id)
            else:
                self._start_parse_and_triage_side_step(document_id)
            self._record_parse_context(document_id, parse_context)
            if self._should_run_spine_shadow():
                spine_context = self._build_spine_shadow_best_effort(document_id, parse_context)
            self._record_spine_context(document_id, spine_context)
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

            if not self.storage.settings.precompute_anchored_explanations:
                snapshot = self.storage.get_document_processing_snapshot(
                    document_id,
                    current_stage=ProcessingStage.SYNTHESIS,
                )
                if snapshot is None:
                    raise ValueError(f"Document not found during on-demand finalization: {document_id}")
                if not snapshot["synthesis_ready"]:
                    self._mark_failed(
                        document_id,
                        "Document summary is unavailable.",
                    )
                    return
                if int(snapshot["pass1_completed_pages"]) <= 0:
                    self._mark_failed(
                        document_id,
                        "Pass1 preprocessing produced no viewer-ready pages.",
                    )
                    return

                self.storage.update_document(
                    document_id,
                    status=DocumentStatus.COMPLETED,
                    error_message=self._build_completion_summary(snapshot),
                )
                return

            self._set_stage(document_id, ProcessingStage.PASS2)
            pass2_started_at = perf_counter()
            pass2_summary = self._run_pass2_stage(document_id)
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
                spine_context=spine_context,
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

    def _should_run_spine_shadow(self) -> bool:
        return (
            self.storage.settings.pipeline_mode == "v2_spine"
            and self.storage.settings.v2_spine_mode in {"shadow", "active"}
        )

    def _should_prepare_parse_precondition(self) -> bool:
        return self.storage.settings.pass1_routing_mode == "hybrid" or self._should_run_spine_shadow()

    def _start_processing_benchmark(self, document_id: str) -> None:
        try:
            self.storage.start_processing_benchmark(
                document_id,
                {
                    "analysis_image_long_edge": RENDER_LONG_EDGE_PIXELS,
                    "pipeline_mode": self.storage.settings.pipeline_mode,
                    "spine_mode": self.storage.settings.v2_spine_mode,
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

    def _record_spine_context(
        self,
        document_id: str,
        spine_context: dict[str, object],
    ) -> None:
        try:
            self.storage.record_stage_duration(
                document_id,
                "spine_time_seconds",
                float(spine_context["spine_time_seconds"]),
            )
            self.storage.update_processing_benchmark_state(
                document_id,
                {
                    "document_spine_generated": bool(spine_context["document_spine_generated"]),
                    "page_routing_generated": bool(spine_context["page_routing_generated"]),
                    "hard_page_count": int(spine_context["hard_page_count"]),
                    "routing_counts_by_label": dict(spine_context["routing_counts_by_label"]),
                    "spine_shadow_status": str(spine_context["spine_shadow_status"]),
                    "spine_shadow_reason": spine_context["spine_shadow_reason"],
                },
            )
        except Exception as exc:
            logger.warning("Processing benchmark spine context record failed for %s: %s", document_id, exc)

    def _finalize_processing_benchmark(
        self,
        *,
        document_id: str,
        pipeline_started_at: float,
        render_result: object,
        parse_context: dict[str, object],
        spine_context: dict[str, object],
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
                    "pipeline_mode": self.storage.settings.pipeline_mode,
                    "spine_mode": self.storage.settings.v2_spine_mode,
                    "rendered_pages": rendered_pages,
                    "parse_artifact_reused": bool(parse_context["parse_artifact_reused"]),
                    "page_manifest_reused": bool(parse_context["page_manifest_reused"]),
                    "document_spine_generated": bool(spine_context["document_spine_generated"]),
                    "page_routing_generated": bool(spine_context["page_routing_generated"]),
                    "hard_page_count": int(spine_context["hard_page_count"]),
                    "routing_counts_by_label": dict(spine_context["routing_counts_by_label"]),
                    "spine_shadow_status": str(spine_context["spine_shadow_status"]),
                    "spine_shadow_reason": spine_context["spine_shadow_reason"],
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

    def _empty_spine_context(self) -> dict[str, object]:
        if self.storage.settings.v2_spine_mode == "off":
            status = "disabled"
            reason: str | None = "disabled"
        else:
            status = "disabled"
            reason = "not_requested"
        return {
            "document_spine_generated": False,
            "page_routing_generated": False,
            "hard_page_count": 0,
            "routing_counts_by_label": {
                "text-rich": 0,
                "visual-rich": 0,
                "scan-like": 0,
            },
            "spine_time_seconds": 0.0,
            "spine_shadow_status": status,
            "spine_shadow_reason": reason,
        }

    def _build_spine_shadow_best_effort(
        self,
        document_id: str,
        parse_context: dict[str, object],
    ) -> dict[str, object]:
        context = self._empty_spine_context()
        if not self._should_run_spine_shadow():
            return context

        context["spine_shadow_status"] = "skipped"
        context["spine_shadow_reason"] = None

        if not bool(parse_context["parse_available"]):
            context["spine_shadow_reason"] = "parse_unavailable"
            return context
        if not bool(parse_context["manifest_available"]):
            context["spine_shadow_reason"] = "manifest_unavailable"
            return context

        started_at = perf_counter()
        try:
            parse_payload = self.storage.load_parse_artifact(document_id)
            if parse_payload is None:
                context["spine_shadow_reason"] = "parse_unavailable"
                return context

            manifest_payload = self.storage.load_page_manifest(document_id)
            if manifest_payload is None:
                context["spine_shadow_reason"] = "manifest_unavailable"
                return context

            parse_artifact = DocumentParseArtifact.model_validate(parse_payload)
            page_manifest = DocumentPageManifest.model_validate(manifest_payload)
            document_spine, page_routing = self.document_spine_builder.build(
                document_id,
                parse_artifact,
                page_manifest,
                pipeline_mode=self.storage.settings.pipeline_mode,
                # active behaves like shadow in this slice.
                spine_mode=self.storage.settings.v2_spine_mode,
                schema_version=self.storage.settings.schema_version,
            )
            self.storage.save_document_spine(document_id, document_spine.model_dump(mode="json"))
            self.storage.save_page_routing(document_id, page_routing.model_dump(mode="json"))

            routing_summary = document_spine.result.routing_summary
            context.update(
                {
                    "document_spine_generated": True,
                    "page_routing_generated": True,
                    "hard_page_count": int(routing_summary.hard_page_count),
                    "routing_counts_by_label": {
                        "text-rich": int(routing_summary.text_rich_pages),
                        "visual-rich": int(routing_summary.visual_rich_pages),
                        "scan-like": int(routing_summary.scan_like_pages),
                    },
                    "spine_shadow_status": "completed",
                    "spine_shadow_reason": None,
                }
            )
            return context
        except Exception as exc:
            context["spine_shadow_status"] = "failed"
            context["spine_shadow_reason"] = "builder_failed"
            logger.warning("Best-effort spine shadow build failed for %s: %s", document_id, exc)
            return context
        finally:
            context["spine_time_seconds"] = round(perf_counter() - started_at, 4)

    def _run_pass2_stage(self, document_id: str) -> dict[str, object]:
        execution_plan = self._build_pass2_execution_plan(document_id)
        if execution_plan["pass2_execution_mode"] != "hard_pages_only":
            all_pages_summary = self.pass2_refiner.refine_document(document_id)
            return {
                **all_pages_summary,
                "pass2_execution_mode": "all_pages",
                "llm_pages": list(all_pages_summary["requested_pages"]),
                "compat_pages": [],
                "planner_reason_by_page": {},
                "selected_pages": list(all_pages_summary["requested_pages"]),
                "skipped_llm_pages": [],
                "pass2_planner_status": execution_plan["pass2_planner_status"],
                "pass2_planner_reason": execution_plan["pass2_planner_reason"],
                "compat_promoted_to_llm_pages": [],
            }

        llm_pages = list(execution_plan["llm_pages"])
        compat_pages = list(execution_plan["compat_pages"])
        planner_reason_by_page = dict(execution_plan["planner_reason_by_page"])
        page_routing_by_page = dict(execution_plan["page_routing_by_page"])

        llm_summaries: list[dict[str, object]] = []
        if llm_pages:
            llm_summaries.append(self.pass2_refiner.refine_document(document_id, page_numbers=llm_pages))

        compat_summary = self._empty_pass2_summary(document_id)
        compat_promoted_to_llm_pages: list[int] = []
        compat_completed_pages: list[int] = []
        compat_saved_paths: list[str] = []
        compat_qa_warnings: list[dict[str, object]] = []

        if compat_pages:
            compat_summary = self.pass2_compat_builder.build_document(
                document_id,
                compat_pages,
                planner_reason_by_page=planner_reason_by_page,
                page_routing_by_page=page_routing_by_page,
            )
            compat_promoted_to_llm_pages = sorted(
                {
                    int(page_result["page_number"])
                    for page_result in compat_summary["failed_pages"]
                }
            )
            compat_completed_pages = sorted(
                {
                    int(page_number)
                    for page_number in compat_summary["completed_pages"]
                    if int(page_number) not in compat_promoted_to_llm_pages
                }
            )
            compat_saved_paths = [
                str(saved_path)
                for saved_path in compat_summary["saved_paths"]
            ]
            compat_qa_warnings = list(compat_summary["qa_warnings"])

            if compat_promoted_to_llm_pages:
                planner_reason_by_page.update(
                    {
                        page_number: "compat_builder_failed_promoted_to_llm"
                        for page_number in compat_promoted_to_llm_pages
                    }
                )
                llm_summaries.append(
                    self.pass2_refiner.refine_document(
                        document_id,
                        page_numbers=compat_promoted_to_llm_pages,
                    )
                )

        llm_summary = self._merge_pass2_summaries(document_id, llm_summaries)
        actual_llm_pages = sorted(set(llm_pages) | set(compat_promoted_to_llm_pages))

        return {
            "document_id": document_id,
            "requested_pages": list(execution_plan["selected_pages"]),
            "completed_pages": sorted(
                set(llm_summary["completed_pages"]) | set(compat_completed_pages)
            ),
            "failed_pages": list(llm_summary["failed_pages"]),
            "saved_paths": list(llm_summary["saved_paths"]) + compat_saved_paths,
            "qa_warnings": list(llm_summary["qa_warnings"]) + compat_qa_warnings,
            "llm_pages": actual_llm_pages,
            "compat_pages": compat_completed_pages,
            "planner_reason_by_page": planner_reason_by_page,
            "pass2_execution_mode": "hard_pages_only",
            "selected_pages": list(execution_plan["selected_pages"]),
            "skipped_llm_pages": compat_completed_pages,
            "pass2_planner_status": "active",
            "pass2_planner_reason": (
                "compat_builder_failed_promoted"
                if compat_promoted_to_llm_pages
                else None
            ),
            "compat_promoted_to_llm_pages": compat_promoted_to_llm_pages,
        }

    def _build_pass2_execution_plan(self, document_id: str) -> dict[str, object]:
        if not self._should_use_active_pass2_planner():
            return {
                "pass2_execution_mode": "all_pages",
                "llm_pages": [],
                "compat_pages": [],
                "planner_reason_by_page": {},
                "selected_pages": [],
                "skipped_llm_pages": [],
                "page_routing_by_page": {},
                "pass2_planner_status": "disabled",
                "pass2_planner_reason": "not_requested",
            }

        try:
            page_routing = self.storage.load_page_routing(document_id)
        except Exception as exc:
            logger.warning("Pass2 active planner routing load failed for %s: %s", document_id, exc)
            return self._fallback_pass2_execution_plan("routing_invalid")

        if page_routing is None:
            return self._fallback_pass2_execution_plan("routing_missing")

        target_pages = sorted(
            page.page_number
            for page in self.storage.get_pages(document_id)
            if page.render_status is RenderStatus.RENDERED
            and page.pass1_status is StageStatus.COMPLETED
        )
        routing_pages = list(page_routing["result"]["pages"])
        routing_page_numbers = [int(page["page_number"]) for page in routing_pages]

        if routing_page_numbers != target_pages:
            return self._fallback_pass2_execution_plan("routing_coverage_mismatch")

        llm_pages: list[int] = []
        compat_pages: list[int] = []
        planner_reason_by_page: dict[int, str] = {}
        page_routing_by_page: dict[int, dict[str, object]] = {}

        for page_entry in routing_pages:
            page_number = int(page_entry["page_number"])
            recommended_execution = str(page_entry["recommended_execution"])
            if recommended_execution not in {member.value for member in RecommendedExecution}:
                return self._fallback_pass2_execution_plan("routing_invalid")

            page_routing_by_page[page_number] = dict(page_entry)
            if recommended_execution == RecommendedExecution.TEXT_FIRST.value:
                try:
                    pass1_artifact = self.storage.load_pass1_result(document_id, page_number)
                except Exception:
                    pass1_artifact = None
                candidate_count = 0
                if pass1_artifact is not None:
                    candidate_count = len(pass1_artifact["result"]["candidate_anchors"])
                if candidate_count < 3:
                    llm_pages.append(page_number)
                    planner_reason_by_page[page_number] = (
                        "compat_candidate_pool_too_small_promoted_to_llm"
                    )
                else:
                    compat_pages.append(page_number)
                    planner_reason_by_page[page_number] = "recommended_execution=text_first"
                continue

            llm_pages.append(page_number)
            planner_reason_by_page[page_number] = (
                f"recommended_execution={recommended_execution}"
            )

        return {
            "pass2_execution_mode": "hard_pages_only",
            "llm_pages": sorted(set(llm_pages)),
            "compat_pages": sorted(set(compat_pages)),
            "planner_reason_by_page": planner_reason_by_page,
            "selected_pages": target_pages,
            "skipped_llm_pages": sorted(set(compat_pages)),
            "page_routing_by_page": page_routing_by_page,
            "pass2_planner_status": "active",
            "pass2_planner_reason": None,
        }

    def _fallback_pass2_execution_plan(self, reason: str) -> dict[str, object]:
        return {
            "pass2_execution_mode": "all_pages",
            "llm_pages": [],
            "compat_pages": [],
            "planner_reason_by_page": {},
            "selected_pages": [],
            "skipped_llm_pages": [],
            "page_routing_by_page": {},
            "pass2_planner_status": "fallback",
            "pass2_planner_reason": reason,
        }

    def _should_use_active_pass2_planner(self) -> bool:
        return (
            self.storage.settings.pipeline_mode == "v2_spine"
            and self.storage.settings.v2_spine_mode == "active"
            and self.storage.settings.pass2_execution_mode == "hard_pages_only"
        )

    def _empty_pass2_summary(self, document_id: str) -> dict[str, object]:
        return {
            "document_id": document_id,
            "requested_pages": [],
            "completed_pages": [],
            "failed_pages": [],
            "saved_paths": [],
            "qa_warnings": [],
        }

    def _merge_pass2_summaries(
        self,
        document_id: str,
        summaries: list[dict[str, object]],
    ) -> dict[str, object]:
        if not summaries:
            return self._empty_pass2_summary(document_id)

        requested_pages: list[int] = []
        completed_pages: set[int] = set()
        failed_pages_by_number: dict[int, dict[str, object]] = {}
        saved_paths: list[str] = []
        qa_warnings: list[dict[str, object]] = []

        for summary in summaries:
            requested_pages.extend(int(page_number) for page_number in summary["requested_pages"])
            completed_pages.update(int(page_number) for page_number in summary["completed_pages"])
            for failed_page in summary["failed_pages"]:
                failed_pages_by_number[int(failed_page["page_number"])] = {
                    "page_number": int(failed_page["page_number"]),
                    "error_message": failed_page["error_message"],
                }
            saved_paths.extend(str(saved_path) for saved_path in summary["saved_paths"])
            qa_warnings.extend(list(summary["qa_warnings"]))

        return {
            "document_id": document_id,
            "requested_pages": sorted(set(requested_pages)),
            "completed_pages": sorted(completed_pages),
            "failed_pages": [
                failed_pages_by_number[page_number]
                for page_number in sorted(failed_pages_by_number)
            ],
            "saved_paths": saved_paths,
            "qa_warnings": qa_warnings,
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
