# syntax=docker/dockerfile:1

FROM bluenviron/mediamtx:1.17.0-ffmpeg

USER root

RUN --mount=type=cache,target=/var/cache/apk \
    apk add --no-cache wget
