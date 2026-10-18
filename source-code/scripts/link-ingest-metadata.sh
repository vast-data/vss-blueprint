#!/usr/bin/env bash
# Symlink shared modules for local Python runs (optional — Docker COPY handles images).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

link_one() {
  local src="$1"
  local dest="$2"
  if [[ ! -f "$src" ]]; then
    echo "Missing canonical file: $src" >&2
    exit 1
  fi
  mkdir -p "$(dirname "$dest")"
  ln -sf "$src" "$dest"
}

link_one "$ROOT/shared/ingest_metadata.py" "$ROOT/retrieval/video-backend/src/ingest_metadata.py"
link_one "$ROOT/shared/ingest_metadata.py" "$ROOT/video-streaming/src/ingest_metadata.py"
link_one "$ROOT/shared/ingest_metadata.py" "$ROOT/video-batch-sync/src/ingest_metadata.py"
link_one "$ROOT/shared/video_chunker.py" "$ROOT/retrieval/video-backend/src/video_chunker.py"
link_one "$ROOT/shared/video_chunker.py" "$ROOT/video-streaming/src/video_chunker.py"
link_one "$ROOT/shared/video_chunker.py" "$ROOT/video-batch-sync/src/video_chunker.py"

echo "Linked ingest_metadata.py and video_chunker.py from shared/ (backend, streaming, batch-sync)."
