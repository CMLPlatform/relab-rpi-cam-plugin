"""Tests for security middleware helpers."""

from __future__ import annotations

import app.core.middleware as middleware_mod

_BLOB_WORKER_CSP_DIRECTIVE = "worker-src 'self' blob:"
_WORKER_SRC_DIRECTIVE = "worker-src"
_DEFAULT_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
_OBJECT_SRC_NONE_DIRECTIVE = "object-src 'none'"
_SETUP_CONNECT_SELF_DIRECTIVE = "connect-src 'self'"
_HTTP_SCHEME_SOURCE = "http:"
_WS_SCHEME_SOURCE = "ws:"
_WSS_SCHEME_SOURCE = "wss:"


class TestContentSecurityPolicy:
    """Tests for route-specific CSP generation."""

    def test_setup_page_allows_blob_workers_for_hls(self) -> None:
        """Setup routes should allow blob workers used by hls.js."""
        csp = middleware_mod._content_security_policy_for_path("/")

        assert _BLOB_WORKER_CSP_DIRECTIVE in csp
        assert _OBJECT_SRC_NONE_DIRECTIVE in csp
        assert _SETUP_CONNECT_SELF_DIRECTIVE in csp
        assert _HTTP_SCHEME_SOURCE not in csp
        assert _WS_SCHEME_SOURCE not in csp
        assert _WSS_SCHEME_SOURCE not in csp

    def test_default_policy_does_not_add_blob_worker_support(self) -> None:
        """Non-setup routes should keep the stricter baseline policy."""
        csp = middleware_mod._content_security_policy_for_path("/api/status")

        assert csp == _DEFAULT_CSP
        assert _WORKER_SRC_DIRECTIVE not in csp
