# relab-rpi-cam-models

Shared transport contracts for the [RELab Raspberry Pi Camera plugin](https://github.com/CMLPlatform/relab-rpi-cam-plugin) and the [RELab platform](https://github.com/CMLPlatform/relab), part of the [CML RELab project](https://cml-relab.org).

## Overview

This package provides stable, hardware-independent DTOs for camera, image, and stream payloads exchanged between the plugin and backend.

## Usage

Install from PyPI (or your internal index):

```bash
pip install relab-rpi-cam-models
```

Import models in your code:

```python
from relab_rpi_cam_models.camera import CameraMode, CameraStatusView
from relab_rpi_cam_models.images import ImageMetadata
from relab_rpi_cam_models.stream import StreamView, StreamMode
```

## Public API

The supported public API is intentionally small:

- `CameraMode`
- `CameraStatusView`
- `StreamMode`
- `StreamView`
- `StreamMetadata`
- `ImageCaptureResponse`
- image metadata DTOs used inside those responses

This package does not own:

- runtime stream state
- provider-specific request models such as YouTube stream config
- Pillow or EXIF helper behavior
- implementation-specific exceptions

Treat anything outside the documented DTOs as private and subject to change.

## License

AGPL-3.0-or-later
