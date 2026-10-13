"""Backend plugin API path prefix with a one-release fallback to the legacy prefix."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import httpx

PLUGIN_API_PREFIX = "/v1/plugins/r9cam"
# NOTE: drop the legacy prefix one release after every backend serves PLUGIN_API_PREFIX.
LEGACY_PLUGIN_API_PREFIX = "/v1/plugins/rpi-cam"


async def send_with_legacy_fallback(send: Callable[[str], Awaitable[httpx.Response]], suffix: str) -> httpx.Response:
    """Call ``send`` with the new path, and retry once on the legacy path when the backend answers 404.

    A backend that predates the rename answers 404 for the new prefix.
    """
    response = await send(f"{PLUGIN_API_PREFIX}{suffix}")
    if response.status_code == 404:
        response = await send(f"{LEGACY_PLUGIN_API_PREFIX}{suffix}")
    return response
