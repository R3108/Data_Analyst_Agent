"""Typed application errors. Every error maps to a stable `code` the UI can act on."""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    status_code: int = 400
    code: str = "bad_request"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return payload


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class UnauthorizedError(AppError):
    """Not signed in, or the session presented is no longer valid."""

    status_code = 401
    code = "unauthorized"


class ForbiddenError(AppError):
    """Signed in, but not allowed to do this. Distinct from 401: signing in again won't help."""

    status_code = 403
    code = "forbidden"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class RateLimitedError(AppError):
    """Too many attempts. Carries `retry_after_s` so the UI can say when to try again."""

    status_code = 429
    code = "rate_limited"

    def __init__(self, message: str, *, retry_after_s: int = 60, **kwargs: Any) -> None:
        details = {**kwargs.pop("details", {}), "retry_after_s": retry_after_s}
        super().__init__(message, details=details, **kwargs)
        self.retry_after_s = retry_after_s


class InvalidInputError(AppError):
    status_code = 422
    code = "invalid_input"


class UnsupportedFileError(AppError):
    status_code = 415
    code = "unsupported_file"


class PayloadTooLargeError(AppError):
    status_code = 413
    code = "payload_too_large"


class LLMError(AppError):
    status_code = 502
    code = "llm_error"


class LLMNotConfiguredError(LLMError):
    status_code = 503
    code = "llm_not_configured"


class BudgetExceededError(AppError):
    status_code = 402
    code = "llm_budget_exceeded"
