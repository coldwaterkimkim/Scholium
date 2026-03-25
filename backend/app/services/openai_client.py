from __future__ import annotations

import base64
import json
import mimetypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openai import OpenAI

from app.core.config import AppSettings, StageConfig, StageName, get_settings
from app.utils.validation import get_json_schema, validate_payload


class OpenAIClientError(RuntimeError):
    """Base OpenAI client error."""


class OpenAIResponseParseError(OpenAIClientError):
    """Raised when the response cannot be parsed as JSON."""


class OpenAIResponseValidationError(OpenAIClientError):
    """Raised when the response fails local schema validation."""


class OpenAIResponsesClient:
    def __init__(self, settings: AppSettings | None = None, client: OpenAI | None = None) -> None:
        self.settings = settings or get_settings()
        if not self.settings.has_openai_api_key:
            raise OpenAIClientError("OPENAI_API_KEY is missing. Prepare the root .env file first.")

        self._client = client or OpenAI(
            api_key=self.settings.openai_api_key,
            max_retries=self.settings.openai_max_retries,
            timeout=self.settings.openai_timeout_seconds,
        )

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

    def _run_stage(
        self,
        stage: StageName,
        stage_payload: dict[str, Any],
        page_image_path: str | Path | None = None,
        extra_user_messages: list[str] | None = None,
    ) -> dict[str, Any]:
        response = self._call_responses_api(
            stage,
            stage_payload,
            page_image_path=page_image_path,
            extra_user_messages=extra_user_messages,
        )

        try:
            return self._validate_and_wrap(stage, stage_payload, response)
        except (OpenAIResponseParseError, OpenAIResponseValidationError) as exc:
            repair_message = (
                "Your previous response failed local parsing or schema validation. "
                "Return only a valid JSON object that matches the schema exactly. "
                f"Validation details: {exc}"
            )
            retry_response = self._call_responses_api(
                stage,
                stage_payload,
                page_image_path=page_image_path,
                repair_message=repair_message,
                extra_user_messages=extra_user_messages,
            )
            return self._validate_and_wrap(stage, stage_payload, retry_response)

    def _call_responses_api(
        self,
        stage: StageName,
        stage_payload: dict[str, Any],
        page_image_path: str | Path | None = None,
        repair_message: str | None = None,
        extra_user_messages: list[str] | None = None,
    ) -> Any:
        stage_config = self.settings.stage_config(stage)
        prompt_text = self._load_prompt_text(stage_config)
        message_input = self._build_input_messages(
            stage_payload,
            page_image_path,
            repair_message,
            extra_user_messages=extra_user_messages,
        )

        try:
            return self._client.responses.create(
                model=stage_config.model_name,
                reasoning={"effort": stage_config.reasoning_effort},
                instructions=prompt_text,
                input=message_input,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": stage_config.schema_name,
                        "schema": get_json_schema(stage),
                        "strict": True,
                    }
                },
                timeout=self.settings.openai_timeout_seconds,
            )
        except Exception as exc:  # pragma: no cover - depends on live API/network
            raise OpenAIClientError(f"Responses API call failed for stage '{stage}': {exc}") from exc

    def _validate_and_wrap(
        self,
        stage: StageName,
        stage_payload: dict[str, Any],
        response: Any,
    ) -> dict[str, Any]:
        parsed_result = self._apply_system_fields(
            stage_payload,
            self._parse_response_payload(response),
        )

        try:
            validated_result = validate_payload(stage, parsed_result)
        except Exception as exc:
            raise OpenAIResponseValidationError(str(exc)) from exc

        return {
            "meta": self._build_meta(stage, response),
            "result": validated_result,
        }

    def _parse_response_payload(self, response: Any) -> dict[str, Any]:
        raw_text = getattr(response, "output_text", None)

        if not raw_text:
            response_dict = response.model_dump(mode="python") if hasattr(response, "model_dump") else {}
            raw_text = self._extract_text_from_output(response_dict.get("output", []))

        if not raw_text:
            raise OpenAIResponseParseError("No JSON text was found in the Responses API output.")

        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise OpenAIResponseParseError(f"Failed to decode JSON response: {exc}") from exc

        if not isinstance(payload, dict):
            raise OpenAIResponseParseError("The Responses API payload must decode to a JSON object.")

        return payload

    def _build_meta(self, stage: StageName, response: Any) -> dict[str, Any]:
        stage_config = self.settings.stage_config(stage)
        return {
            "schema_version": self.settings.schema_version,
            "prompt_version": stage_config.prompt_version,
            "model_name": getattr(response, "model", stage_config.model_name),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def _apply_system_fields(
        self,
        stage_payload: dict[str, Any],
        parsed_result: dict[str, Any],
    ) -> dict[str, Any]:
        normalized_result = dict(parsed_result)

        if "document_id" in stage_payload:
            normalized_result["document_id"] = stage_payload["document_id"]
        if "page_number" in stage_payload:
            normalized_result["page_number"] = stage_payload["page_number"]

        return normalized_result

    def _build_input_messages(
        self,
        stage_payload: dict[str, Any],
        page_image_path: str | Path | None,
        repair_message: str | None,
        extra_user_messages: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        user_content: list[dict[str, Any]] = [
            {"type": "input_text", "text": json.dumps(stage_payload, ensure_ascii=False, indent=2)}
        ]

        if page_image_path is not None:
            user_content.append(
                {
                    "type": "input_image",
                    "image_url": self._image_path_to_data_url(page_image_path),
                }
            )

        messages: list[dict[str, Any]] = [
            {"role": "user", "content": user_content},
        ]

        if extra_user_messages:
            for message in extra_user_messages:
                if not message:
                    continue
                messages.append(
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": message}],
                    }
                )

        if repair_message:
            messages.append(
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": repair_message}],
                }
            )

        return messages

    def _load_prompt_text(self, stage_config: StageConfig) -> str:
        if not stage_config.prompt_path.exists():
            raise OpenAIClientError(
                f"Prompt file is missing for stage '{stage_config.stage_name}': {stage_config.prompt_path}"
            )
        return stage_config.prompt_path.read_text(encoding="utf-8")

    def _image_path_to_data_url(self, page_image_path: str | Path) -> str:
        path = Path(page_image_path)
        if not path.exists():
            raise OpenAIClientError(f"Page image file does not exist: {path}")

        mime_type, _ = mimetypes.guess_type(path.name)
        encoded_bytes = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime_type or 'application/octet-stream'};base64,{encoded_bytes}"

    def _extract_text_from_output(self, output_items: list[dict[str, Any]]) -> str | None:
        for item in output_items:
            for content in item.get("content", []):
                if content.get("type") == "output_text" and content.get("text"):
                    return content["text"]
        return None
