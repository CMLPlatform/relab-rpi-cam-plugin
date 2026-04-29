"""Shared literals for the test suite."""

HTML_CONTENT_TYPE = "text/html"
JPEG_CONTENT_TYPE = "image/jpeg"
PNG_CONTENT_TYPE = "image/png"
NO_STORE_CACHE_CONTROL = "no-store"
HLS_M3U8_CONTENT_TYPE = "application/vnd.apple.mpegurl"
HLS_MP4_CONTENT_TYPE = "video/mp4"
HLS_PREVIEW_ENCODER_FRAGMENT = "preview encoder"

# Shared URL bases and prefixes
BACKEND_EXAMPLE_BASE_URL = "https://backend.example"
EXAMPLE_BACKEND_URL = "https://example.com"
EXAMPLE_RELAY_BACKEND_URL = "wss://example.com/v1/plugins/rpi-cam/ws/connect"
EXAMPLE_RELAY_BACKEND_URL_UNSECURE = "ws://example.com/v1/plugins/rpi-cam/ws/connect"
EXAMPLE_RELAY_HTTP_URL = "http://example.com"
EXAMPLE_RELAY_HTTPS_URL = "https://example.com"
EXAMPLE_RELAY_BACKEND_URL_WITH_CAMERA_ID = f"{EXAMPLE_RELAY_BACKEND_URL}?camera_id=cam-42"
EXAMPLE_IMAGE_URL = f"{EXAMPLE_BACKEND_URL}/img.jpg"
YOUTUBE_WATCH_URL_PREFIX = "https://youtube.com/watch?v="
YOUTUBE_EMBED_URL_PREFIX = "https://www.youtube.com/embed/"

# Sample image upload constants used in integration tests
UPLOADED_STATUS = "uploaded"
QUEUED_STATUS = "queued"
SAMPLE_IMAGE_ID = "a1b2c3d4e5f6a7b8a1b2c3d4e5f6a7b8"
SAMPLE_IMAGE_URL = f"{BACKEND_EXAMPLE_BASE_URL}/images/a1b2c3d4.jpg"

# Shared backend upload values used by unit tests
BACKEND_IMAGE_URL = f"{BACKEND_EXAMPLE_BASE_URL}/images/abc.jpg"
SAMPLE_SERVER_IMAGE_ID = "server-abc"
BACKEND_PUSH_IMAGE_ID = "srv-abc"
BACKEND_PUSH_IMAGE_BYTES = b"jpeg-body"
BACKEND_PUSH_FILENAME = "local-1.jpg"

# Prometheus metric snapshot lines used across tests
MET_CPU_18_5 = "rpi_cam_cpu_percent 18.5"
MET_MEM_44_0 = "rpi_cam_mem_percent 44.0"
MET_DISK_12_0 = "rpi_cam_disk_percent 12.0"
MET_CPU_TEMP_62_0 = "rpi_cam_cpu_temp_celsius 62.0"
MET_THERMAL_WARM = 'rpi_cam_thermal_state{state="warm"} 1'
MET_CPU = "rpi_cam_cpu_percent"

# Unit metrics snapshot constants
MET_CPU_10_0 = "rpi_cam_cpu_percent 10.0"
MET_MEM_20_0 = "rpi_cam_mem_percent 20.0"
MET_DISK_30_0 = "rpi_cam_disk_percent 30.0"
MET_PREVIEW_SESSIONS_0_0 = "rpi_cam_preview_sessions 0.0"
MET_CPU_TEMP_55_5 = "rpi_cam_cpu_temp_celsius 55.5"
MET_PREVIEW_FPS_24_5 = "rpi_cam_preview_fps 24.5"

# Metric name tokens
MET_CPU_NAME = "rpi_cam_cpu_percent"
MET_MEM_NAME = "rpi_cam_mem_percent"
MET_DISK_NAME = "rpi_cam_disk_percent"
MET_PREVIEW_SESSIONS_NAME = "rpi_cam_preview_sessions"
MET_CPU_TEMP_NAME = "rpi_cam_cpu_temp_celsius"
MET_PREVIEW_FPS_NAME = "rpi_cam_preview_fps"

# Telemetry JSON expected values
TELEMETRY_CPU_TEMP = 48.2
TELEMETRY_CPU_PERCENT = 7.5
TELEMETRY_MEM_PERCENT = 31.0
TELEMETRY_DISK_PERCENT = 25.0
TELEMETRY_THERMAL_NORMAL = "normal"
TIMESTAMP_KEY = "timestamp"

# Pairing flow log fragments
PAIRING_REGISTER_TIMEOUT_LOG = "PAIRING REGISTER TIMEOUT | code=ABC123 retry_in_s=1"
PAIRING_POLL_TIMEOUT_LOG = "PAIRING POLL TIMEOUT | code=ABC123 retry_in_s=3"
TRACEBACK_TEXT = "Traceback"

# Image sink / MediaMTX test literals
IMAGE_SINK_BACKEND = "backend"
IMAGE_SINK_S3 = "s3"
IMAGE_SINK_AUTO = "auto"
DEFAULT_S3_REGION = "us-east-1"
S3_BUCKET_NAME = "rpi-cam"
S3_OBJECT_KEY = "rpi-cam/42/abc123.jpg"
S3_OBJECT_KEY_UNSORTED = "rpi-cam/unsorted/img-no-product.jpg"
S3_PUBLIC_URL = "http://minio.local:9000/rpi-cam/rpi-cam/42/abc123.jpg"
S3_CDN_URL = "https://cdn.example.com/rpi-cam/9/xyz.jpg"
S3_IMAGE_BYTES = b"jpeg-body"
S3_IMAGE_ID = "abc123"
S3_UNSORTED_IMAGE_ID = "img-no-product"
S3_CDN_IMAGE_ID = "xyz"
S3_MEDIA_TYPE = "image/jpeg"
S3_BUCKET_ALREADY_OWNED_BY_YOU = "BucketAlreadyOwnedByYou"
S3_BUCKET_ALREADY_EXISTS = "BucketAlreadyExists"

# MediaMTX test literals
MEDIAMTX_PATCH_URL = "http://mediamtx:9997/v3/config/paths/patch/cam-hires"
MEDIAMTX_RTMPS_URL = "rtmps://a.rtmps.youtube.com:443/live2/abcd-efgh-ijkl"
MEDIAMTX_FFMPEG = "ffmpeg"
MEDIAMTX_FFMPEG_COPY = "-c:v copy"
MEDIAMTX_FFMPEG_ANULLSRC = "anullsrc"
MEDIAMTX_MISSING_PATH_LOG = "missing path"
MEDIAMTX_HTTP_500 = "HTTP 500"
MEDIAMTX_UNREACHABLE = "unreachable"
PICAMERA2_MAIN_STREAM_NAME = "main"
PICAMERA2_LORES_STREAM_NAME = "lores"
PICAMERA2_CAM_HIRES_PATH = "cam-hires"
PICAMERA2_STARTUP_TIMEOUT = "startup timeout"
PICAMERA2_CAMERA_NOT_INITIALIZED = "Camera backend has not been initialized"
YOUTUBE_TEST_BROADCAST_URL = f"{YOUTUBE_WATCH_URL_PREFIX}TEST_BROADCAST_KEY_123"
YOUTUBE_WATCH_URL = f"{YOUTUBE_WATCH_URL_PREFIX}broadcast-key"
YOUTUBE_EMBED_URL = f"{YOUTUBE_EMBED_URL_PREFIX}broadcast-key"
YOUTUBE_PUBLIC_URL = f"{YOUTUBE_WATCH_URL_PREFIX}public-id"
CAMERA_DEVICE_NOT_FOUND = "Camera device not found"

# Compose override template samples
COMPOSE_OVERRIDE_APP_ONE_DEVICE = 'services:\n  app:\n    devices:\n      - "/dev/video0:/dev/video0"\n'
COMPOSE_OVERRIDE_CUSTOM_NO_DEVICES = "services:\n  rpi-cam-plugin:\n    devices: []\n"
