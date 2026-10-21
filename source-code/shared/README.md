# Shared cross-service modules

## `video_chunker.py`

Splits a finished local file into fixed-duration pieces (`DEFAULT_CHUNK_DURATION_SEC` is 30). Manual upload and batch sync both call `iter_video_chunks`. `chunk_s3_metadata` fills `stream_id`, `chunk_index`, `chunk_start_sec`, `chunk_duration_sec`, and `capture_interval`. Live streaming does not split a file here; it records the next window, then calls `chunk_s3_metadata` for the same fields.

| Consumer | How it gets the module |
|----------|-------------------------|
| **video-backend** | Docker: `COPY shared/video_chunker.py` into `src/`. Local: imported from this directory when `src/video_chunker.py` is absent. |
| **video-batch-sync** | Docker: `COPY shared/video_chunker.py`. Local: `shared/` is put on `sys.path` when that directory exists. |
| **video-streaming** | Same as batch sync. Uses `chunk_s3_metadata` only. |

The backend image installs `ffmpeg` (includes `ffprobe`) for the upload split.

## `ingest_metadata.py`

Single source of truth for ingest/upload metadata (field labels, capture types, scenario labels, S3 mapping, custom-prompt limit).

| Consumer | How it gets the module |
|----------|-------------------------|
| **video-backend** | Docker: `COPY shared/ingest_metadata.py` into image. Local: optional symlink via `link-ingest-metadata.sh` |
| **video-streaming** | Same |
| **video-batch-sync** | Same |
| **video-frontend** | Runtime: `GET /api/v1/metadata/ingest-config` |

Scenario **prompt text** stays in `ingest/video-reasoner/common/prompts.py`.

There are **no committed copies** under service `src/` directories.

---

## Editing metadata

1. Change **`source-code/shared/ingest_metadata.py`**
2. For a new analysis scenario, also add prompt text to `prompts.py`
3. Rebuild affected Docker images (see below)

---

## Docker builds

Build from **`source-code/`** as context (canonical `shared/` is copied into each image), or use the helper script:

```bash
source-code/scripts/build-retrieval-images.sh   # backend, frontend, streaming, batch-sync
```

Manual builds:

```bash
cd source-code

docker buildx build -f retrieval/video-backend/Dockerfile -t your.registry/org/vss2-backend:latest --push .

docker buildx build -f retrieval/video-frontend/Dockerfile -t your.registry/org/vss2-frontend:latest --push retrieval/video-frontend

docker buildx build -f video-streaming/Dockerfile -t your.registry/org/vss2-streaming:latest --push .

docker buildx build -f video-batch-sync/Dockerfile -t your.registry/org/vss2-batch:latest --push .
```

Frontend still uses `retrieval/video-frontend/` as context (no shared Python module).

See [scripts README](../scripts/README.md) for `REGISTRY` / `TAG` overrides.

---

## Local Python dev (without Docker)

One-time symlink (optional):

```bash
source-code/scripts/link-ingest-metadata.sh
```

This creates `src/ingest_metadata.py` → `shared/ingest_metadata.py` in backend, streaming, and batch-sync. Symlinks are gitignored.

---
