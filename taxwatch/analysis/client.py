"""OpenAI-compatible LLM client with structured output support.

Handles the reality that local models (vLLM, Ollama, etc.) have varying
support for json_schema / json_object response formats.
Three-tier fallback: json_schema → json_object + prompt schema → raw + pydantic repair.
"""

from __future__ import annotations

import json
import logging
import random
import time
from enum import StrEnum
from typing import Any, TypeVar

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    OpenAI,
)
from pydantic import BaseModel, ValidationError

from taxwatch.config import get_settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)



# WSO2 APIM announces a tripped circuit breaker in the response body rather than
# through the status code — the call comes back as a plain 400, indistinguishable
# from a malformed request until you read the payload.
_SUSPENSION_MARKERS = (
    "suspended",
    "303001",
    "address endpoint",
    "currently , address endpoint",
)


def _error_body(exc: Exception) -> str:
    for attr in ("message", "body"):
        value = getattr(exc, attr, None)
        if isinstance(value, str) and value:
            return value.lower()
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=False).lower()
    response = getattr(exc, "response", None)
    text = getattr(response, "text", None)
    return text.lower() if isinstance(text, str) else ""


def _is_suspension(exc: Exception) -> bool:
    body = _error_body(exc)
    return any(marker in body for marker in _SUSPENSION_MARKERS)


def _retry_after_seconds(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    raw = headers.get("retry-after") or headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None


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
            # The SDK retries twice on its own by default. Stacked under our
            # own retry loop that is up to 15 HTTP calls per logical request,
            # with two independent backoffs — which reads as a hang. Retry
            # policy lives in _create_with_retry alone.
            max_retries=0,
        )
        self.model = settings.llm_model
        self.temperature = settings.llm_temperature
        self.max_tokens = settings.llm_max_tokens
        self.retry_attempts = settings.llm_retry_attempts
        self.retry_base_delay = settings.llm_retry_base_delay
        self.retry_max_delay = settings.llm_retry_max_delay
        self.retry_on_bad_request = settings.llm_retry_on_bad_request
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

        if level in (SchemaSupport.RAW, SchemaSupport.JSON_OBJECT):
            schema_str = json.dumps(schema, ensure_ascii=False, indent=2)
            user_prompt += f"\n\nRespond with valid JSON matching this schema:\n{schema_str}"

        kwargs = self._build_format_kwargs(level, schema)

        budget = self.max_tokens

        for attempt in range(max_retries + 1):
            resp = self._create_with_retry(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=budget,
                **kwargs,
            )

            choice = resp.choices[0]
            content = choice.message.content or "{}"

            # Truncated output parses as "malformed JSON", which sends the retry
            # off fixing syntax it never got wrong. Give it more room instead.
            if choice.finish_reason == "length":
                if attempt < max_retries:
                    logger.warning(
                        "LLM output hit the %d-token limit; retrying with %d",
                        budget,
                        budget * 2,
                    )
                    budget *= 2
                    continue
                msg = (
                    f"LLM output was truncated at the {budget}-token limit "
                    f"(LLM_MAX_TOKENS). The response is incomplete JSON, not invalid "
                    f"JSON — raise LLM_MAX_TOKENS or extract from fewer provisions."
                )
                raise ValueError(msg)

            try:
                data = json.loads(content)
                return output_model.model_validate(data)
            except (json.JSONDecodeError, ValidationError) as exc:
                preview = content[:500] if content else "(empty)"
                logger.debug("LLM raw response (first 500 chars): %s", preview)
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
                msg = (
                    f"LLM output failed validation after {max_retries + 1} attempts: {exc}\n"
                    f"Response preview: {preview}"
                )
                raise ValueError(msg) from exc

        raise RuntimeError("Unreachable")


    # Status codes the gateway uses for "busy, try again". 400 is in the list
    # because this deployment's gateway answers overload with BadRequest rather
    # than 429 — see _is_retryable for why that is gated behind a setting.
    _TRANSIENT_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

    def _is_retryable(self, exc: Exception) -> bool:
        # A dropped connection or a timeout says nothing about the request
        # itself, so it is always worth another go.
        if isinstance(exc, (APIConnectionError, APITimeoutError)):
            return True
        if not isinstance(exc, APIStatusError):
            return False
        if exc.status_code in self._TRANSIENT_STATUS:
            return True
        # A 400 normally means the request is wrong and will stay wrong, so
        # retrying it just burns time. Some gateways nonetheless return it under
        # load, which is why this is opt-out rather than unconditional: a real
        # malformed request still fails, only later and more loudly.
        return exc.status_code == 400 and self.retry_on_bad_request

    def _backoff_delay(self, attempt: int, exc: Exception) -> float:
        """How long to wait before retry `attempt`.

        Equal jitter, not full jitter. Full jitter can draw a near-zero wait,
        which is fine for a busy server but wrong for a circuit breaker: WSO2
        APIM suspends the endpoint outright, and every call inside that window
        is refused no matter how it is spaced. A retry that fires immediately
        just burns an attempt against a door that is still shut, so half of each
        delay is fixed and only the other half is randomised.
        """
        retry_after = _retry_after_seconds(exc)
        if retry_after is not None:
            # The gateway said when to come back; arguing with it is pointless.
            return min(retry_after, self.retry_max_delay)

        capped = min(self.retry_base_delay * (2 ** (attempt - 1)), self.retry_max_delay)
        return capped / 2 + random.uniform(0, capped / 2)

    def _create_with_retry(self, **create_kwargs: Any):
        last: Exception | None = None

        for attempt in range(1, self.retry_attempts + 1):
            try:
                return self.client.chat.completions.create(
                    model=self.model,
                    temperature=self.temperature,
                    **create_kwargs,
                )
            except Exception as exc:  # noqa: BLE001 — re-raised below if fatal
                if not self._is_retryable(exc) or attempt >= self.retry_attempts:
                    raise
                last = exc
                delay = self._backoff_delay(attempt, exc)
                status = getattr(exc, "status_code", "-")
                logger.warning(
                    "LLM call failed (%s status=%s%s), attempt %d/%d; retrying in %.1fs",
                    type(exc).__name__,
                    status,
                    ", endpoint suspended" if _is_suspension(exc) else "",
                    attempt,
                    self.retry_attempts,
                    delay,
                )
                time.sleep(delay)

        raise last if last else RuntimeError("Unreachable")

    def _build_format_kwargs(self, level: SchemaSupport, schema: dict) -> dict:
        if level == SchemaSupport.JSON_SCHEMA:
            return {
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "output", "schema": schema, "strict": True},
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
