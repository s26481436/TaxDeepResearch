"""OpenAI-compatible LLM client with structured output support.

Handles the reality that local models (vLLM, Ollama, etc.) have varying
support for json_schema / json_object response formats.
Three-tier fallback: json_schema → json_object + prompt schema → raw + pydantic repair.
"""

from __future__ import annotations

import json
import logging
from enum import StrEnum
from typing import TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from taxwatch.config import get_settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class SchemaSupport(StrEnum):
    JSON_SCHEMA = "json_schema"
    JSON_OBJECT = "json_object"
    RAW = "raw"


class LLMClient:
    def __init__(self):
        settings = get_settings()
        self.client = OpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            timeout=settings.llm_timeout,
        )
        self.model = settings.llm_model
        self.temperature = settings.llm_temperature
        self.max_tokens = settings.llm_max_tokens
        self._schema_support: SchemaSupport | None = None

    def detect_capabilities(self) -> SchemaSupport:
        if self._schema_support is not None:
            return self._schema_support

        for level in [SchemaSupport.JSON_SCHEMA, SchemaSupport.JSON_OBJECT]:
            try:
                test_schema = {
                    "type": "object",
                    "properties": {"test": {"type": "string"}},
                    "required": ["test"],
                }
                kwargs = self._build_format_kwargs(level, test_schema)
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": 'Reply with JSON: {"test": "ok"}'}],
                    max_tokens=50,
                    **kwargs,
                )
                content = resp.choices[0].message.content or ""
                json.loads(content)
                self._schema_support = level
                logger.info("LLM schema support detected: %s", level.value)
                return level
            except Exception:
                continue

        self._schema_support = SchemaSupport.RAW
        logger.info("LLM schema support: raw (no structured output)")
        return SchemaSupport.RAW

    def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        output_model: type[T],
        max_retries: int = 1,
    ) -> T:
        level = self.detect_capabilities()
        schema = output_model.model_json_schema()

        if level == SchemaSupport.RAW:
            schema_str = json.dumps(schema, ensure_ascii=False, indent=2)
            user_prompt += f"\n\nRespond with valid JSON matching this schema:\n{schema_str}"

        kwargs = self._build_format_kwargs(level, schema)

        for attempt in range(max_retries + 1):
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                **kwargs,
            )

            content = resp.choices[0].message.content or "{}"

            try:
                data = json.loads(content)
                return output_model.model_validate(data)
            except (json.JSONDecodeError, ValidationError) as exc:
                if attempt < max_retries:
                    logger.warning(
                        "LLM output validation failed (attempt %d), retrying",
                        attempt + 1,
                    )
                    user_prompt = (
                        f"Your previous response had a validation error:\n{exc}\n\n"
                        f"Original request:\n{user_prompt}\n\n"
                        f"Please fix your JSON response to match the schema."
                    )
                    continue
                msg = f"LLM output failed validation after {max_retries + 1} attempts: {exc}"
                raise ValueError(msg) from exc

        raise RuntimeError("Unreachable")

    def _build_format_kwargs(self, level: SchemaSupport, schema: dict) -> dict:
        if level == SchemaSupport.JSON_SCHEMA:
            return {
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "output", "schema": schema},
                }
            }
        if level == SchemaSupport.JSON_OBJECT:
            return {"response_format": {"type": "json_object"}}
        return {}


_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
