from __future__ import annotations

import json
import shlex
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import AppSettings, PROJECT_ROOT, StageConfig, StageName, get_settings
from app.services.analysis_client import (
    AnalysisClientError,
    AnalysisResponseParseError,
    AnalysisResponseValidationError,
)
from app.utils.validation import get_json_schema, validate_payload


class CodexCLIClientError(AnalysisClientError):
    """Raised when Codex CLI cannot complete a local analysis call."""


class CodexCLIClient:
    def __init__(self, settings: AppSettings | None = None) -> None:
        self.settings = settings or get_settings()

    def run_pass1(
        self,
        page_image_path: str | Path,
        document_id: str,
        page_number: int,
        optional_extracted_text: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "document_id": document_id,
            "page_number": page_number,
            "schema_version": self.settings.schema_version,
            "prompt_version": self.settings.stage_config("pass1").prompt_version,
            "optional_extracted_text": optional_extracted_text,
        }
        return self._run_stage("pass1", payload, page_image_path=page_image_path)

    def run_pass1_text_first(
        self,
        *,
        document_id: str,
        page_number: int,
        route_label: str,
        route_reason: str,
        parser_source: str,
        text_length: int,
        non_empty_text_block_count: int,
        page_text: str,
        parsed_blocks: list[dict[str, Any]],
        allowed_anchor_regions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload = {
            "document_id": document_id,
            "page_number": page_number,
            "schema_version": self.settings.schema_version,
            "prompt_version": self.settings.stage_config("pass1").prompt_version,
            "route_label": route_label,
            "route_reason": route_reason,
            "parser_source": parser_source,
            "text_length": text_length,
            "non_empty_text_block_count": non_empty_text_block_count,
            "optional_extracted_text": page_text,
            "page_text": page_text,
            "parsed_blocks": parsed_blocks,
            "allowed_anchor_regions": allowed_anchor_regions,
        }
        return self._run_stage(
            "pass1",
            payload,
            extra_user_messages=[self._build_pass1_text_first_guidance()],
        )

    def run_document_synthesis(
        self,
        document_id: str,
        total_pages: int,
        page_analysis_summaries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload = {
            "document_id": document_id,
            "total_pages": total_pages,
            "schema_version": self.settings.schema_version,
            "prompt_version": self.settings.stage_config("document_synthesis").prompt_version,
            "page_analysis_summaries": page_analysis_summaries,
        }
        return self._run_stage("document_synthesis", payload)

    def run_pass2(
        self,
        page_image_path: str | Path,
        document_id: str,
        page_number: int,
        pass1_result: dict[str, Any],
        document_summary: dict[str, Any],
        extra_guidance: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "document_id": document_id,
            "page_number": page_number,
            "schema_version": self.settings.schema_version,
            "prompt_version": self.settings.stage_config("pass2").prompt_version,
            "pass1_result": pass1_result,
            "document_summary": document_summary,
        }
        extra_user_messages = [extra_guidance] if extra_guidance else None
        return self._run_stage(
            "pass2",
            payload,
            page_image_path=page_image_path,
            extra_user_messages=extra_user_messages,
        )

    def run_selection_explanation(
        self,
        page_image_path: str | Path,
        document_id: str,
        page_number: int,
        selection_id: str,
        selected_bbox: list[float],
        pass1_result: dict[str, Any],
        document_summary: dict[str, Any],
        matched_preprocessed_elements: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload = {
            "document_id": document_id,
            "page_number": page_number,
            "selection_id": selection_id,
            "anchor_id": selection_id,
            "selected_bbox": selected_bbox,
            "bbox": selected_bbox,
            "schema_version": self.settings.schema_version,
            "prompt_version": self.settings.stage_config("selection_explanation").prompt_version,
            "pass1_result": pass1_result,
            "document_summary": document_summary,
            "matched_preprocessed_elements": matched_preprocessed_elements,
        }
        return self._run_stage("selection_explanation", payload, page_image_path=page_image_path)

    def run_selection_follow_up(
        self,
        page_image_path: str | Path,
        document_id: str,
        page_number: int,
        selection_id: str,
        question: str,
        selection_explanation: dict[str, Any],
        pass1_result: dict[str, Any],
        document_summary: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "document_id": document_id,
            "page_number": page_number,
            "selection_id": selection_id,
            "question": question,
            "schema_version": self.settings.schema_version,
            "prompt_version": self.settings.stage_config("selection_follow_up").prompt_version,
            "selection_explanation": selection_explanation,
            "pass1_result": pass1_result,
            "document_summary": document_summary,
        }
        return self._run_stage("selection_follow_up", payload, page_image_path=page_image_path)

    def _run_stage(
        self,
        stage: StageName,
        stage_payload: dict[str, Any],
        page_image_path: str | Path | None = None,
        extra_user_messages: list[str] | None = None,
    ) -> dict[str, Any]:
        try:
            raw_payload = self._call_codex_cli(
                stage,
                stage_payload,
                page_image_path=page_image_path,
                extra_user_messages=extra_user_messages,
            )
            return self._validate_and_wrap(stage, stage_payload, raw_payload)
        except (AnalysisResponseParseError, AnalysisResponseValidationError) as exc:
            repair_message = (
                "Your previous response failed local JSON parsing or schema validation. "
                "Return exactly one valid JSON object matching the provided schema. "
                f"Validation details: {exc}"
            )
            raw_payload = self._call_codex_cli(
                stage,
                stage_payload,
                page_image_path=page_image_path,
                repair_message=repair_message,
                extra_user_messages=extra_user_messages,
            )
            return self._validate_and_wrap(stage, stage_payload, raw_payload)

    def _call_codex_cli(
        self,
        stage: StageName,
        stage_payload: dict[str, Any],
        page_image_path: str | Path | None = None,
        repair_message: str | None = None,
        extra_user_messages: list[str] | None = None,
    ) -> dict[str, Any]:
        stage_config = self.settings.stage_config(stage)
        prompt_text = self._load_prompt_text(stage_config)

        with tempfile.TemporaryDirectory(prefix=f"scholium_codex_{stage}_") as tmp_dir:
            tmp_path = Path(tmp_dir)
            schema_path = tmp_path / "output_schema.json"
            output_path = tmp_path / "last_message.json"
            schema_path.write_text(
                json.dumps(get_json_schema(stage), ensure_ascii=False),
                encoding="utf-8",
            )

            command = self._build_command(
                schema_path=schema_path,
                output_path=output_path,
                page_image_path=page_image_path,
            )
            prompt = self._build_prompt(
                stage=stage,
                prompt_text=prompt_text,
                stage_payload=stage_payload,
                repair_message=repair_message,
                extra_user_messages=extra_user_messages,
                has_image=page_image_path is not None,
            )

            try:
                completed = subprocess.run(
                    command,
                    input=prompt,
                    text=True,
                    capture_output=True,
                    timeout=self.settings.codex_cli_timeout_seconds,
                    cwd=PROJECT_ROOT,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise CodexCLIClientError(
                    f"Codex CLI timed out for stage '{stage}' after "
                    f"{self.settings.codex_cli_timeout_seconds}s."
                ) from exc
            except OSError as exc:
                raise CodexCLIClientError(
                    f"Codex CLI failed to start for stage '{stage}'. "
                    f"Check CODEX_CLI_BIN={self.settings.codex_cli_bin!r}. Detail: {exc}"
                ) from exc

            if completed.returncode != 0:
                raise CodexCLIClientError(
                    f"Codex CLI exited with code {completed.returncode} for stage '{stage}'. "
                    f"stdout={self._summarize_process_text(completed.stdout)} "
                    f"stderr={self._summarize_process_text(completed.stderr)}"
                )

            if not output_path.exists():
                raise AnalysisResponseParseError(
                    f"Codex CLI did not write a final JSON message for stage '{stage}'."
                )

            raw_text = output_path.read_text(encoding="utf-8").strip()
            if not raw_text:
                raise AnalysisResponseParseError(
                    f"Codex CLI wrote an empty final message for stage '{stage}'."
                )

            try:
                parsed = json.loads(raw_text)
            except json.JSONDecodeError as exc:
                raise AnalysisResponseParseError(
                    f"Codex CLI final message is not valid JSON for stage '{stage}': {exc}"
                ) from exc

            if not isinstance(parsed, dict):
                raise AnalysisResponseParseError(
                    f"Codex CLI final message must be a JSON object for stage '{stage}'."
                )
            return parsed

    def _build_command(
        self,
        *,
        schema_path: Path,
        output_path: Path,
        page_image_path: str | Path | None,
    ) -> list[str]:
        command = shlex.split(self.settings.codex_cli_bin)
        if not command:
            raise CodexCLIClientError("CODEX_CLI_BIN cannot be empty.")

        command.extend(["--sandbox", "read-only", "-a", "never"])
        if self.settings.codex_cli_model:
            command.extend(["-m", self.settings.codex_cli_model])
        command.extend(
            [
                "-c",
                f'model_reasoning_effort="{self.settings.codex_cli_reasoning_effort}"',
            ]
        )
        command.extend(
            [
                "exec",
                "--ephemeral",
                "--ignore-user-config",
                "--ignore-rules",
                "--skip-git-repo-check",
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
            ]
        )

        if page_image_path is not None:
            path = Path(page_image_path)
            if not path.exists():
                raise CodexCLIClientError(f"Page image file does not exist: {path}")
            command.extend(["--image", str(path)])

        command.append("-")
        return command

    def _build_prompt(
        self,
        *,
        stage: StageName,
        prompt_text: str,
        stage_payload: dict[str, Any],
        repair_message: str | None,
        extra_user_messages: list[str] | None,
        has_image: bool,
    ) -> str:
        payload_text = json.dumps(stage_payload, ensure_ascii=False, separators=(",", ":"))
        parts = [
            "You are the local Scholium MVP analysis provider running inside Codex CLI.",
            "Act as a pure JSON generation engine for this single analysis call.",
            "Do not edit files, do not run shell commands, do not browse, and do not inspect the repository.",
            "Use only the stage instructions, the structured input JSON, and the attached image if present.",
            "Return exactly one JSON object. No Markdown, no code fences, no prose outside JSON.",
            "The returned object is the validated result body only; the backend adds meta separately.",
            f"Stage: {stage}",
            "An image is attached for this stage." if has_image else "No image is attached for this stage.",
            "\n--- Stage Instructions ---\n",
            prompt_text,
            "\n--- Structured Input JSON ---\n",
            payload_text,
        ]
        if extra_user_messages:
            parts.extend(["\n--- Extra Stage Guidance ---\n", "\n\n".join(extra_user_messages)])
        if repair_message:
            parts.extend(["\n--- Repair Instruction ---\n", repair_message])
        return "\n".join(parts)

    def _validate_and_wrap(
        self,
        stage: StageName,
        stage_payload: dict[str, Any],
        parsed_result: dict[str, Any],
    ) -> dict[str, Any]:
        normalized_result = dict(parsed_result)
        if stage == "selection_follow_up":
            try:
                validated_result = validate_payload(stage, normalized_result)
            except Exception as exc:
                raise AnalysisResponseValidationError(str(exc)) from exc

            return {
                "meta": self._build_meta(stage),
                "result": validated_result,
            }

        if "document_id" in stage_payload:
            normalized_result["document_id"] = stage_payload["document_id"]
        if "page_number" in stage_payload:
            normalized_result["page_number"] = stage_payload["page_number"]
        if stage == "selection_explanation":
            selection_id = str(stage_payload["selection_id"])
            selected_bbox = list(stage_payload["selected_bbox"])
            normalized_result["selection_id"] = selection_id
            normalized_result["anchor_id"] = selection_id
            if not normalized_result.get("concept_title") and normalized_result.get("label"):
                normalized_result["concept_title"] = normalized_result["label"]
            if not normalized_result.get("label") and normalized_result.get("concept_title"):
                normalized_result["label"] = normalized_result["concept_title"]
            normalized_result["bbox"] = selected_bbox
            normalized_result["selected_bbox"] = selected_bbox
            normalized_result["explanation_mode"] = "selection"
        try:
            validated_result = validate_payload(stage, normalized_result)
        except Exception as exc:
            raise AnalysisResponseValidationError(str(exc)) from exc

        return {
            "meta": self._build_meta(stage),
            "result": validated_result,
        }

    def _build_meta(self, stage: StageName) -> dict[str, Any]:
        stage_config = self.settings.stage_config(stage)
        model_name = self.settings.codex_cli_model or "default"
        return {
            "schema_version": self.settings.schema_version,
            "prompt_version": stage_config.prompt_version,
            "model_name": f"codex-cli:{model_name}",
            "reasoning_effort": self.settings.codex_cli_reasoning_effort,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def _load_prompt_text(self, stage_config: StageConfig) -> str:
        if not stage_config.prompt_path.exists():
            raise CodexCLIClientError(
                f"Prompt file is missing for stage '{stage_config.stage_name}': {stage_config.prompt_path}"
            )
        return stage_config.prompt_path.read_text(encoding="utf-8")

    def _build_pass1_text_first_guidance(self) -> str:
        return (
            "Text-first mode is active for this pass1 call. No page image is provided. "
            "Ground the output only in the supplied page_text, parsed_blocks, and allowed_anchor_regions. "
            "Treat allowed_anchor_regions as the authoritative bbox source. "
            "Every candidate_anchors[].bbox must exactly match one allowed_anchor_regions[].bbox. "
            "Prefer anchors whose bbox exactly matches a single parsed block. "
            "If an anchor must span adjacent blocks, use only a provided two-block union bbox. "
            "Reusing the same allowed bbox for multiple grounded anchors is acceptable when several questions attach to the same text region. "
            "Do not invent visual regions or decorative elements that are not supported by page_text, parsed_blocks, or allowed_anchor_regions. "
            "Return the same pass1 JSON schema as usual."
        )

    def _summarize_process_text(self, value: str | None, *, max_length: int = 700) -> str:
        text = " ".join(str(value or "").split())
        if len(text) <= max_length:
            return text
        head_length = max(1, max_length // 2)
        tail_length = max(1, max_length - head_length - 15)
        return f"{text[:head_length].rstrip()} ...[truncated]... {text[-tail_length:].lstrip()}"
