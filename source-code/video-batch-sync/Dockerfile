# Video Batch Sync Dockerfile
#
# Build from source-code/ (not this directory):
#   cd source-code
#   docker buildx build -f video-batch-sync/Dockerfile -t your.registry/vss-video-batch-sync:v2 --push .
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY video-batch-sync/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ingest_metadata.py ./ingest_metadata.py
COPY shared/video_chunker.py ./video_chunker.py
COPY video-batch-sync/src/web_service.py ./web_service.py

ENV PYTHONUNBUFFERED=1

CMD ["python", "web_service.py"]
