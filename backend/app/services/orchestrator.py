from __future__ import annotations

from threading import Lock

from starlette.concurrency import run_in_threadpool

from app.models.document import DocumentStatus, ProcessingStage, StageStatus
from app.services.document_synthesizer import DocumentSynthesizer
from app.services.pass1_analyzer import Pass1Analyzer
from app.services.pass2_refiner import Pass2Refiner
from app.services.storage import StorageService, get_storage_service
from app.workers.render_worker import RenderWorker


class DocumentOrchestrator:
    def __init__(
        self,
        storage: StorageService | None = None,
        render_worker: RenderWorker | None = None,
        pass1_analyzer: Pass1Analyzer | None = None,
        document_synthesizer: DocumentSynthesizer | None = None,
        pass2_refiner: Pass2Refiner | None = None,
    ) -> None:
        self.storage = storage or get_storage_service()
        self.render_worker = render_worker or RenderWorker(storage=self.storage)
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
        try:
            self._set_stage(document_id, ProcessingStage.RENDER)
            render_result = self.render_worker.render_document(document_id)
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

            self._set_stage(document_id, ProcessingStage.PASS1)
            self.pass1_analyzer.analyze_document(document_id)
            self.storage.update_document(
                document_id,
                status=DocumentStatus.ANALYZING,
                error_message=None,
            )

            self._set_stage(document_id, ProcessingStage.SYNTHESIS)
            synthesis_result = self.document_synthesizer.synthesize_document(document_id)
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
            self.pass2_refiner.refine_document(document_id)

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
