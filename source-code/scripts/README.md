# Build & dev scripts

Runnable from any working directory (scripts resolve `source-code/` automatically).

| Script | Purpose |
|--------|---------|
| [`build-vastde-functions.sh`](build-vastde-functions.sh) | Build + push DataEngine images `vss2-segmenter`, `vss2-detector`, `vss2-reasoner`, `vss2-embedder`, `vss2-vastdb`, `vss2-events`. Aligned with DataEngine 5.5.0-sp2 (`vastde` v5.5.0-sp2, builder `vastdataorg/vast-builder:v5.5.0-sp2`): [Get vastde and roll function images](../../deployments/vss-deploy/README.md#get-vastde-and-roll-function-images). |
| [`build-retrieval-images.sh`](build-retrieval-images.sh) | Build + push K8s images `vss2-backend`, `vss2-frontend`, `vss2-streaming`, `vss2-batch` |
| [`link-ingest-metadata.sh`](link-ingest-metadata.sh) | Local dev: symlink `shared/ingest_metadata.py` and `shared/video_chunker.py` into backend / streaming / batch-sync `src/` |

## Environment overrides

Both build scripts require `REGISTRY` (no default). `TAG` defaults to `latest`, the same as an empty tag in `site.yaml`.

| Variable | Default |
|----------|---------|
| `REGISTRY` | **required** — registry prefix (e.g. `your.registry/org`) |
| `TAG` | `latest` |

Example:

```bash
REGISTRY=your.registry/org source-code/scripts/build-retrieval-images.sh
```

## Retrieval image context

- **backend, streaming, batch-sync** — Docker build context is `source-code/` (copies `shared/ingest_metadata.py`)
- **frontend** — context is `retrieval/video-frontend/`

See [shared README](../shared/README.md) for details.
