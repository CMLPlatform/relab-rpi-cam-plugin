"""Tests for security middleware helpers."""

from __future__ import annotations

import app.core.middleware as middleware_mod

_BLOB_WORKER_CSP_DIRECTIVE = "worker-src 'self' blob:"
_WORKER_SRC_DIRECTIVE = "worker-src"


class TestContentSecurityPolicy:
    """Tests for route-specific CSP generation."""

    def test_setup_page_allows_blob_workers_for_hls(self) -> None:
        """Setup routes should allow blob workers used by hls.js."""
        csp = middleware_mod._content_security_policy_for_path("/")

        assert _BLOB_WORKER_CSP_DIRECTIVE in csp

    def test_default_policy_does_not_add_blob_worker_support(self) -> None:
        """Non-setup routes should keep the stricter baseline policy."""
        csp = middleware_mod._content_security_policy_for_path("/api/status")

        assert _WORKER_SRC_DIRECTIVE not in csp
