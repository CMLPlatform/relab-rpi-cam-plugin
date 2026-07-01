"""Context-backed access to the process-active app runtime."""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.runtime import AppRuntime
    from app.core.runtime_state import RuntimeState


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


def get_active_runtime_state() -> RuntimeState:
    """Return the active runtime's mutable ``RuntimeState`` (credentials/keys).

    Last-resort accessor for context-free code (background helpers, log filters)
    that holds no request and no injected handle. Prefer passing ``RuntimeState``
    explicitly; reach for this only when there is genuinely nothing to pass.
    """
    return get_active_runtime().runtime_state
