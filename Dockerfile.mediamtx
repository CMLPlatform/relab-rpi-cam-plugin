# syntax=docker/dockerfile:1

FROM bluenviron/mediamtx:1.17.1-ffmpeg@sha256:f648b6c98abbc02917d5598479e647b1b451dd13f8df265ab2afde060fd50f7f

USER root

RUN --mount=type=cache,target=/var/cache/apk \
    apk add --no-cache wget

USER 1000:1000
