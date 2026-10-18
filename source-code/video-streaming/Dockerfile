# Video Stream Capture Dockerfile
#
# Build from source-code/ (not this directory):
#   cd source-code
#   docker buildx build -f video-streaming/Dockerfile -t your.registry/vss-video-streaming:v2 --push .
#
# Python 3.11+: current yt-dlp rejects 3.10.
# Node 20: YouTube extraction requires a JS runtime. Debian's `nodejs` package
# often has no `node` binary, which makes yt-dlp fail with
# "No supported JavaScript runtime could be found".
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    wget \
    curl \
    ca-certificates \
    xz-utils \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# yt-dlp enables Deno by default and also accepts Node. Both must be real binaries on PATH.
ARG NODE_VERSION=20.19.0
ARG DENO_VERSION=2.1.4
RUN curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" \
    | tar -xJ -C /usr/local --strip-components=1 \
    && node --version \
    && curl -fsSL -o /tmp/deno.zip \
        "https://github.com/denoland/deno/releases/download/v${DENO_VERSION}/deno-x86_64-unknown-linux-gnu.zip" \
    && unzip -o /tmp/deno.zip -d /usr/local/bin \
    && chmod 755 /usr/local/bin/deno \
    && rm /tmp/deno.zip \
    && deno --version

WORKDIR /app

COPY video-streaming/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir yt-dlp \
    && yt-dlp --remote-components ejs:github \
        --js-runtimes "deno:$(command -v deno)" \
        --js-runtimes "node:$(command -v node)" \
        --version

COPY shared/ingest_metadata.py ./ingest_metadata.py
COPY shared/video_chunker.py ./video_chunker.py
COPY video-streaming/src/web_service.py ./web_service.py

RUN mkdir -p recordings
RUN chmod +x web_service.py

ENV PYTHONUNBUFFERED=1

CMD ["python", "web_service.py"]
