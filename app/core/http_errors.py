"""Helpers for client-safe HTTP error responses."""

from app.observability.logging import get_request_id


def client_error_detail(message: str, *, request_id: str | None = None) -> dict[str, str]:
    """Return a stable error detail, adding request id when one is bound."""
    detail: dict[str, str] = {"message": message}
    if rid := request_id or get_request_id():
        detail["request_id"] = rid
    return detail
