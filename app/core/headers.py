"""Shared HTTP response headers for cache and data-protection policies."""

NO_STORE_HEADERS: dict[str, str] = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Expires": "0",
}
