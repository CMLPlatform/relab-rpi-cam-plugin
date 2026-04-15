"""Context-backed access to the process-active app runtime."""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.runtime import AppRuntime


_active_runtime_var: ContextVar[AppRuntime | None] = ContextVar("active_runtime", default=None)


def set_active_runtime(runtime: AppRuntime | None) -> None:
    """Bind the active runtime for the current context."""
    _active_runtime_var.set(runtime)


def get_active_runtime() -> AppRuntime:
    """Return the active runtime for background helpers and non-request code."""
    runtime = _active_runtime_var.get()
    if runtime is None:
        msg = "Application runtime has not been initialized"
        raise RuntimeError(msg)
    return runtime
