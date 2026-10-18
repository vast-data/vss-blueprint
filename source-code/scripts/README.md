# Build & dev scripts

Runnable from any working directory (scripts resolve `source-code/` automatically).

| Script | Purpose |
|--------|---------|
| [`build-vastde-functions.sh`](build-vastde-functions.sh) | Build + push DataEngine pipeline images (segmenter, reasoner, embedder, vastdb-writer, prompt-suggester) |
| [`build-retrieval-images.sh`](build-retrieval-images.sh) | Build + push K8s app images (backend, frontend, streaming, batch-sync) |
| [`link-ingest-metadata.sh`](link-ingest-metadata.sh) | Local dev: symlink `shared/ingest_metadata.py` and `shared/video_chunker.py` into backend / streaming / batch-sync `src/` |

## Environment overrides

Both build scripts require `REGISTRY` (no default). `TAG` defaults to `v1`.

| Variable | Default |
|----------|---------|
| `REGISTRY` | **required** — your registry prefix (e.g. `your.registry/vss`) |
| `TAG` | `v1` |

Example:

```bash
REGISTRY=your.registry/vss TAG=v3 source-code/scripts/build-retrieval-images.sh
```

## Retrieval image context

- **backend, streaming, batch-sync** — Docker build context is `source-code/` (copies `shared/ingest_metadata.py`)
- **frontend** — context is `retrieval/video-frontend/`

See [shared README](../shared/README.md) for details.
