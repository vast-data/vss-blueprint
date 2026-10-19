# Vast VSS Blueprint Backend Dockerfile
#
# Build from source-code/ (not this directory):
#   cd source-code
#   docker buildx build -f retrieval/video-backend/Dockerfile -t your.registry/vss-video-backend:v2 --push .
FROM python:3.10-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY retrieval/video-backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY retrieval/video-backend/src/ ./src/
COPY shared/ingest_metadata.py ./src/ingest_metadata.py
COPY shared/video_chunker.py ./src/video_chunker.py

RUN mkdir -p /opt/adbc-driver
COPY retrieval/video-backend/libadbc_driver_vastdb.so /opt/adbc-driver/libadbc_driver_vastdb.so
RUN chmod 755 /opt/adbc-driver/libadbc_driver_vastdb.so

EXPOSE 8000

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]