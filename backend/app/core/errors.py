class PrismError(Exception):
    """Base class for expected, user-facing errors."""

    status_code = 400
    code = "prism_error"

    def __init__(self, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class IngestionError(PrismError):
    code = "ingestion_error"
    status_code = 422


class NotFound(PrismError):
    code = "not_found"
    status_code = 404


class Forbidden(PrismError):
    code = "forbidden"
    status_code = 403


class Unauthorized(PrismError):
    code = "unauthorized"
    status_code = 401


class Conflict(PrismError):
    code = "conflict"
    status_code = 409
