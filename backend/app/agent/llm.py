"""LLM access layer. Nodes depend on the `StructuredLLM` protocol, not on a vendor SDK,
which keeps the graph testable with a scripted fake."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Generic, Protocol, TypeVar

import openai
import pydantic

from app.core.config import Settings
from app.core.errors import LLMError, LLMNotConfiguredError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=pydantic.BaseModel)


@dataclass
class LLMResult(Generic[T]):
    output: T
    usage: dict[str, Any] = field(default_factory=dict)


class StructuredLLM(Protocol):
    def generate(
        self,
        *,
        system: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        schema: type[T],
        purpose: str,
    ) -> LLMResult[T]: ...


class OpenAILLM:
    """OpenAI Responses API with Pydantic Structured Outputs and reasoning control."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: openai.OpenAI | None = None
        self._lock = threading.Lock()

    def _get_client(self) -> openai.OpenAI:
        if self._client is None:
            with self._lock:
                if self._client is None:
                    kwargs: dict[str, Any] = {"max_retries": 3, "timeout": self.settings.llm_timeout_s}
                    if self.settings.openai_api_key:
                        kwargs["api_key"] = self.settings.openai_api_key
                    self._client = openai.OpenAI(**kwargs)
        return self._client

    def generate(
        self,
        *,
        system: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        schema: type[T],
        purpose: str,
    ) -> LLMResult[T]:
        model = self.settings.openai_model
        system_text = "\n\n".join(
            str(block.get("text", "")) for block in system if block.get("type") == "text"
        ).strip()
        params: dict[str, Any] = {
            "model": model,
            "max_output_tokens": self.settings.llm_max_tokens,
            "input": [{"role": "developer", "content": system_text}, *messages],
            "text_format": schema,
            "store": False,
        }
        if self.settings.analyst_effort:
            params["reasoning"] = {"effort": self.settings.analyst_effort}

        try:
            response = self._get_client().responses.parse(**params)
        except openai.AuthenticationError as exc:
            raise LLMNotConfiguredError(
                "The OpenAI API rejected the credentials. Check OPENAI_API_KEY."
            ) from exc
        except openai.PermissionDeniedError as exc:
            raise LLMError(f"This API key does not have access to '{model}'.",
                           code="llm_permission_denied") from exc
        except openai.NotFoundError as exc:
            raise LLMError(f"Model '{model}' was not found. Check OPENAI_MODEL.",
                           code="llm_model_not_found") from exc
        except openai.RateLimitError as exc:
            raise LLMError("The model is rate limited right now. Please retry in a moment.",
                           code="llm_rate_limited", status_code=429) from exc
        except openai.BadRequestError as exc:
            raise LLMError(f"The model request was rejected: {exc.message}", code="llm_bad_request") from exc
        except openai.APIStatusError as exc:
            raise LLMError(f"The model service returned an error ({exc.status_code}). Please retry.",
                           code="llm_unavailable") from exc
        except openai.APIConnectionError as exc:
            raise LLMError("Could not reach the OpenAI API. Check your network connection.",
                           code="llm_unreachable") from exc
        except pydantic.ValidationError as exc:
            raise LLMError("The model returned output that did not match the expected structure.",
                           code="llm_invalid_output") from exc
        except (openai.OpenAIError, TypeError) as exc:
            if "auth" in str(exc).lower() or "api_key" in str(exc).lower():
                raise LLMNotConfiguredError(
                    "No OpenAI credentials found. Set OPENAI_API_KEY in your .env file."
                ) from exc
            raise LLMError(f"Unexpected model client error: {exc}") from exc

        raw_usage = response.usage
        input_details = getattr(raw_usage, "input_tokens_details", None) if raw_usage else None
        usage = {
            "purpose": purpose,
            "model": response.model,
            "input_tokens": getattr(raw_usage, "input_tokens", 0) or 0,
            "output_tokens": getattr(raw_usage, "output_tokens", 0) or 0,
            "cache_read_tokens": getattr(input_details, "cached_tokens", 0) or 0,
            "cache_write_tokens": getattr(input_details, "cache_write_tokens", 0) or 0,
        }
        logger.info("llm[%s] model=%s status=%s in=%s out=%s cache_read=%s", purpose, response.model,
                    response.status, usage["input_tokens"], usage["output_tokens"], usage["cache_read_tokens"])

        refusal = _refusal_text(response)
        if refusal:
            raise LLMError("The model declined to help with this request. Try rephrasing the question.",
                           code="llm_refusal")
        incomplete_reason = getattr(getattr(response, "incomplete_details", None), "reason", None)
        if response.status == "incomplete" and incomplete_reason == "max_output_tokens":
            raise LLMError("The model response was cut off before it finished. Try a narrower question.",
                           code="llm_truncated")
        if response.status == "failed":
            raise LLMError("The model could not complete the request. Please retry.", code="llm_unavailable")
        parsed = response.output_parsed
        if parsed is None:
            raise LLMError("The model did not return a structured response.", code="llm_invalid_output")
        return LLMResult(output=parsed, usage=usage)


def _refusal_text(response: Any) -> str | None:
    """Return a refusal from a Responses API message, if one is present."""
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            if getattr(content, "type", None) == "refusal":
                return getattr(content, "refusal", None) or "refused"
    return None
