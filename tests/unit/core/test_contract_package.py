"""Tests for the shared contract package surface."""

import relab_rpi_cam_models as contracts


def test_shared_package_exports_only_contract_types() -> None:
    """The package should not expose plugin runtime or workflow internals."""
    assert hasattr(contracts, "StreamView")
    assert not hasattr(contracts, "Stream")
    assert not hasattr(contracts, "YoutubeStreamConfig")
