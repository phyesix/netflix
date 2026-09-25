FROM python:3.12-alpine

ARG VERSION=dev
LABEL org.opencontainers.image.title="netflix-top10arr" \
      org.opencontainers.image.description="Netflix Türkiye Top 10 -> Sonarr / Radarr" \
      org.opencontainers.image.source="https://github.com/phyesix/netflix" \
      org.opencontainers.image.version="${VERSION}"

RUN apk add --no-cache tzdata \
    && mkdir -p /data && chown 1000:1000 /data

WORKDIR /app
COPY top10arr.py .

ENV APP_VERSION=${VERSION} \
    STATE_FILE=/data/state.json \
    CRON_SCHEDULE="17 */6 * * *" \
    TZ=Europe/Istanbul \
    RUN_ON_START=true \
    PYTHONUNBUFFERED=1

VOLUME /data
USER 1000:1000
CMD ["python", "/app/top10arr.py"]
