"""Helpers for client-safe HTTP error responses."""

from app.observability.logging import get_request_id


def client_error_detail(message: str) -> str | dict[str, str]:
    """Return a stable error detail, adding request id when one is bound."""
    request_id = get_request_id()
    if request_id:
        return {"message": message, "request_id": request_id}
    return message
