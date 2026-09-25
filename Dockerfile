FROM python:3.12-alpine
WORKDIR /app
COPY top10arr.py .
ENV STATE_FILE=/data/state.json \
    INTERVAL_HOURS=12 \
    PYTHONUNBUFFERED=1
VOLUME /data
USER 1000:1000
CMD ["python", "/app/top10arr.py"]
