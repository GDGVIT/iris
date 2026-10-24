"""Application errors.

Every error carries the machine-readable ``code`` and the HTTP ``status`` the
API should answer with, so the Flask error handler is a single generic function
rather than a per-exception dispatch table. The previous hierarchy had eleven
classes, half of them abstract bases that were never raised.
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    INVALID_REQUEST = "INVALID_REQUEST"
    INVALID_TASK_ID = "INVALID_TASK_ID"
    NOT_FOUND = "NOT_FOUND"
    TASK_ALREADY_COMPLETE = "TASK_ALREADY_COMPLETE"
    PAGE_NOT_FOUND = "PAGE_NOT_FOUND"
    DISAMBIGUATION_PAGE = "DISAMBIGUATION_PAGE"
    PATH_NOT_FOUND = "PATH_NOT_FOUND"
    RATE_LIMITED = "RATE_LIMITED"
    WIKIPEDIA_API_ERROR = "WIKIPEDIA_API_ERROR"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class IrisError(Exception):
    """Base for everything this application raises deliberately."""

    code: ErrorCode = ErrorCode.INTERNAL_ERROR
    status: int = 500

    def __init__(self, message: str, code: ErrorCode | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code

    @property
    def headers(self) -> dict[str, str]:
        """Extra response headers the error needs, such as ``Retry-After``."""
        return {}


class InvalidRequestError(IrisError):
    code = ErrorCode.INVALID_REQUEST
    status = 400


class NotFoundError(IrisError):
    code = ErrorCode.NOT_FOUND
    status = 404


class ConflictError(IrisError):
    code = ErrorCode.TASK_ALREADY_COMPLETE
    status = 409


class RateLimitedError(IrisError):
    code = ErrorCode.RATE_LIMITED
    status = 429

    def __init__(self, retry_after: int) -> None:
        super().__init__(f"Too many requests. Try again in {retry_after} seconds.")
        self.retry_after = retry_after

    @property
    def headers(self) -> dict[str, str]:
        return {"Retry-After": str(self.retry_after)}


class PageNotFoundError(IrisError):
    code = ErrorCode.PAGE_NOT_FOUND
    status = 404

    def __init__(self, title: str) -> None:
        super().__init__(f"'{title}' does not exist on Wikipedia")
        self.title = title


class DisambiguationError(IrisError):
    code = ErrorCode.DISAMBIGUATION_PAGE
    status = 400

    def __init__(self, title: str, resolved: str | None = None) -> None:
        if resolved and resolved != title:
            message = f"'{title}' redirects to the disambiguation page '{resolved}'"
        else:
            message = f"'{title}' is a disambiguation page"
        super().__init__(f"{message}. Please choose a more specific page.")
        self.title = title


class PathNotFoundError(IrisError):
    code = ErrorCode.PATH_NOT_FOUND
    status = 404

    def __init__(self, start: str, end: str, max_depth: int) -> None:
        super().__init__(
            f"No path found from '{start}' to '{end}' within {max_depth} steps"
        )
        self.start = start
        self.end = end


class WikipediaAPIError(IrisError):
    """Raised when Wikipedia is unreachable or keeps failing. Retryable."""

    code = ErrorCode.WIKIPEDIA_API_ERROR
    status = 503


class ConfigError(IrisError):
    code = ErrorCode.CONFIGURATION_ERROR
    status = 500
