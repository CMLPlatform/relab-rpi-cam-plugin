"""Shared literals for the test suite."""

HTML_CONTENT_TYPE = "text/html"
JPEG_CONTENT_TYPE = "image/jpeg"
PNG_CONTENT_TYPE = "image/png"

# Shared literals used across multiple test modules
SDP_MID = "a=mid:0"
SDP_ICE_UFRAG = "a=ice-ufrag:"
SDP_ICE_LABEL = "a=ice-" + "pwd:"
SDP_FINGERPRINT = "a=fingerprint:sha-256"
SDP_RTPMAP = "a=rtpmap:96 H264/90000"

BASE_HOST = "http://host.docker.internal:8889"
LOCATION_REL = "/cam-preview/whep/abc"
LOCATION_FULL = BASE_HOST + LOCATION_REL
EXTERNAL_LOCATION = "http://external.example/cam-preview/whep/xyz"

LOCATION_HEADER_TEXT = "Location header"
HTTP_400_TEXT = "HTTP 400"
ANSWER_SDP = "v=0\nanswer"
MEDIA_UNREACHABLE = "MediaMTX unreachable"

# Shared constants for FFmpeg command and audio checks used in unit tests
FFMPEG_FLAG_FLV = "-f flv"
FFMPEG_FLAG_SHORTEST = "-shortest"
ANULLSRC_STEREO = "anullsrc=channel_layout=stereo:sample_rate=44100"
NULLAUDIO_MONITOR = "nullaudio.monitor"
HLS = "hls"
MASTER_M3U8 = "master.m3u8"
HTTP_UPLOAD_HLS = "http_upload_hls"
RTMPS_BASE = "rtmps://a.rtmps.youtube.com:443/live2"

# Sample image upload constants used in integration tests
UPLOADED_STATUS = "uploaded"
QUEUED_STATUS = "queued"
SAMPLE_IMAGE_ID = "a1b2c3d4e5f6a7b8a1b2c3d4e5f6a7b8"
SAMPLE_IMAGE_URL = "https://backend.example/images/a1b2c3d4.jpg"

# Sample server-side upload values used by unit tests
SAMPLE_SERVER_IMAGE_ID = "server-abc"
SAMPLE_SERVER_IMAGE_URL = "https://backend.example/images/abc.jpg"

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
PAIRING_REGISTER_TIMEOUT_LOG = "PAIRING REGISTER TIMEOUT | code=CODE1 retry_in_s=1"
PAIRING_POLL_TIMEOUT_LOG = "PAIRING POLL TIMEOUT | code=CODE1 retry_in_s=3"
TRACEBACK_TEXT = "Traceback"

# Compose override template samples
COMPOSE_OVERRIDE_APP_ONE_DEVICE = 'services:\n  app:\n    devices:\n      - "/dev/video0:/dev/video0"\n'
COMPOSE_OVERRIDE_CUSTOM_NO_DEVICES = "services:\n  rpi-cam-plugin:\n    devices: []\n"
