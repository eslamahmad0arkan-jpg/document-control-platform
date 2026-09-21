"""Structured API errors. Any Google API failure maps to a friendly message + code,
never an uncaught exception to the client."""
from __future__ import annotations


class AppError(Exception):
    status_code = 400
    code = "BAD_REQUEST"
    message = "Invalid request."

    def __init__(self, message: str | None = None, details: dict | None = None):
        if message:
            self.message = message
        self.details = details or {}
        super().__init__(self.message)

    def to_dict(self) -> dict:
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


class AuthError(AppError):
    status_code = 401
    code = "UNAUTHORIZED"
    message = "Authentication required."


class ForbiddenError(AppError):
    status_code = 403
    code = "FORBIDDEN"
    message = "You do not have permission to perform this action."


class NotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"
    message = "Resource not found."


class ConflictError(AppError):
    status_code = 409
    code = "CONFLICT"
    message = "The resource already exists or state conflicts."


class ValidationError_(AppError):
    status_code = 400
    code = "VALIDATION_ERROR"
    message = "Invalid request payload."


# --- Google API mapped errors -------------------------------------------------

class GoogleApiError(AppError):
    """Base error raised by the Drive client; carries the original API error."""

    def __init__(self, message: str = "Google API error.", status: int = 500,
                 google_code: str | None = None, details: dict | None = None):
        self.google_code = google_code or google_code or "API_ERROR"
        super().__init__(message, details or {"googleCode": self.google_code})
        self.status = status
        self.code = "GOOGLE_API_ERROR"


class GooglePermissionError(GoogleApiError):
    """403 / permission denied — the user lacks real Drive permissions."""

    def __init__(self, details: dict | None = None):
        super().__init__(
            "You do not have permission to access this item in Google Drive.",
            status=403, google_code="PERMISSION_DENIED", details=details,
        )


class GoogleAuthExpiredError(GoogleApiError):
    """401 / expired or revoked token."""

    def __init__(self, details: dict | None = None):
        super().__init__(
            "Google Drive connection expired. Please reconnect your Google account.",
            status=401, google_code="INVALID_TOKEN", details=details,
        )


class GoogleRateLimitError(GoogleApiError):
    """429 — should be retried with exponential backoff."""

    def __init__(self, details: dict | None = None):
        super().__init__(
            "Google Drive is rate-limiting requests. Retrying shortly.",
            status=429, google_code="RATE_LIMIT", details=details,
        )


class GoogleNotFoundError(GoogleApiError):
    def __init__(self, details: dict | None = None):
        super().__init__(
            "The requested Google Drive item no longer exists.",
            status=404, google_code="NOT_FOUND", details=details,
        )


class GoogleConnectionError(GoogleApiError):
    """Network / connectivity failure talking to Google."""

    def __init__(self, message: str = "Could not reach Google services.", details: dict | None = None):
        super().__init__(message, status=502, google_code="NETWORK", details=details)