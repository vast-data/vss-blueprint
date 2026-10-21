#!/usr/bin/env bash
# Build and push retrieval / edge Docker images (backend, frontend, streaming, batch-sync).
#
# Backend, streaming, and batch-sync use source-code/ as build context so Docker can
# COPY shared/ingest_metadata.py. Frontend builds from retrieval/video-frontend/.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -z "${REGISTRY:-}" ]]; then
  echo "error: set REGISTRY to your registry prefix (e.g. REGISTRY=your.registry/org)" >&2
  echo "  example: REGISTRY=your.registry/org $0" >&2
  exit 1
fi
TAG="${TAG:-latest}"

build_from_source_code() {
  local dockerfile="$1"
  local image="$2"
  echo "==> $image ($dockerfile)"
  docker buildx build \
    -f "$ROOT/$dockerfile" \
    -t "$REGISTRY/$image:$TAG" \
    --push \
    "$ROOT"
}

build_from_service_dir() {
  local dir="$1"
  local image="$2"
  echo "==> $image ($dir)"
  docker buildx build \
    -t "$REGISTRY/$image:$TAG" \
    --push \
    "$ROOT/$dir"
}

build_from_source_code retrieval/video-backend/Dockerfile  vss2-backend
build_from_source_code video-streaming/Dockerfile        vss2-streaming
build_from_source_code video-batch-sync/Dockerfile       vss2-batch
build_from_service_dir retrieval/video-frontend            vss2-frontend

echo "Done. Pushed retrieval images (tag=$TAG) to $REGISTRY"
