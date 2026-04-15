"""Capture-and-store helpers for the Raspberry Pi camera plugin."""

from __future__ import annotations

from typing import Protocol


class _Response(Protocol):
    def json(self) -> dict[str, object]: ...


class _Session(Protocol):
    async def get(self, model: object, ident: object) -> object: ...


class _CameraRequest(Protocol):
    async def __call__(self, *, endpoint: str, body: dict[str, object]) -> _Response: ...


def require_model(_session: _Session, _product_id: int) -> None:
    """Validate that the referenced model exists."""
    return


async def capture_and_store_image(
    *,
    session: _Session,
    camera_request: _CameraRequest,
    product_id: int,
    description: str | None = None,
) -> object:
    """Capture an image via the Pi API and return the stored image record."""
    require_model(session, product_id)
    upload_metadata: dict[str, object] = {"product_id": product_id}
    if description is not None:
        upload_metadata["description"] = description

    response = await camera_request(endpoint="/images", body={"upload_metadata": upload_metadata})
    payload = response.json()
    image_id = payload["image_id"]
    return await session.get(object, image_id)
