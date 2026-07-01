"""Shared HTTP response headers and client-safe error helpers."""

from __future__ import annotations

from app.observability.logging import get_request_id

NO_STORE_HEADERS: dict[str, str] = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Expires": "0",
}


def client_error_detail(message: str, *, request_id: str | None = None) -> dict[str, str]:
    """Return a stable error detail, adding request id when one is bound."""
    detail: dict[str, str] = {"message": message}
    if rid := request_id or get_request_id():
        detail["request_id"] = rid
    return detail
