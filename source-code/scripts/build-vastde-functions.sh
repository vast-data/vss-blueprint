#!/usr/bin/env bash
# Build and push DataEngine function images.
# Aligned with DataEngine 5.5.0-sp2: vastde v5.5.0-sp2 and
# builder vastdataorg/vast-builder:v5.5.0-sp2. TAG is the function image tag.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -z "${REGISTRY:-}" ]]; then
  echo "error: set REGISTRY to your registry prefix (e.g. REGISTRY=your.registry/vss)" >&2
  echo "  example: REGISTRY=your.registry/vss TAG=v1 $0" >&2
  exit 1
fi
TAG="${TAG:-v1}"

build_and_push() {
  local dir="$1"
  local name="$2"

  echo "==> ${name}"
  (cd "$ROOT/$dir" && vastde functions build "${name}") || return 1
  docker tag "${name}:latest" "${REGISTRY}/${name}:${TAG}" || return 1
  docker push "${REGISTRY}/${name}:${TAG}"
}

build_and_push ingest/video-segmenter vss2-segmenter
build_and_push ingest/video-detector vss2-detector
build_and_push ingest/video-reasoner vss2-reasoner
build_and_push ingest/video-embedder vss2-embedder
build_and_push ingest/vastdb-writer vss2-vastdb
build_and_push enrichment/prompt-suggester vss2-events

echo "Done. Pushed vastde functions (tag=$TAG) to $REGISTRY"
