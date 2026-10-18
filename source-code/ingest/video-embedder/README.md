# Video Embedder

DataEngine function that converts video reasoning text into vector embeddings for semantic search.

## What It Does

- Receives reasoning text from the `video-reasoner` function
- Embeds `reasoning_content` into **text** vectors (`vectors`) via Cosmos-Embed1
- Downloads the segment from S3 and embeds the full segment MP4 into **visual** vectors (`vectors_visual`) when enabled
- Passes both embeddings and metadata to the next function in the pipeline
- Preserves all metadata (camera_id, capture_type, location, etc.)

## Easy to Adjust

The [site deploy](../../../deployments/vss-deploy/README.md) writes these keys into the DataEngine secret from `site.yaml`:

| Setting | Description |
|---------|-------------|
| **`embedding_local_nim`** | `true` = local NIM (no API key), `false` = NVIDIA Cloud (sends API key) |
| **`embeddinghost`** / **`embeddingport`** / **`embeddinghttpscheme`** | Cosmos-Embed1 endpoint |
| **`embeddingmodel`** | `nvidia/cosmos-embed1` |
| **`embeddingdimensions`** | `256` |
| **`visual_embedding_enabled`** | When `true`, download segment from S3 and compute `vectors_visual` |
| **`visual_embedding_model`** | `nvidia/cosmos-embed1` (same NIM as text) |
| **`visual_embedding_dimensions`** | `256` |
| **`nvidia_api_key`** | Required when `embedding_local_nim: false` (NVIDIA Cloud) |

Local stack guide: `docs/COSMOS_LOCAL_STACK.md`

## About the Function

- **Trigger**: Receives events from `video-reasoner` function
- **Input**: Reasoning text and metadata from video analysis
- **Output**: Vector embeddings and metadata
- **Processing**: Calls Cosmos-Embed1 NIM (`request_type` query / bulk_text / video)
- **Validation**: Skips if reasoning content is empty or invalid

## What Runs It

- **Runtime**: VAST DataEngine serverless runtime
- **Image**: `your.registry/vss-video-embedder:v1` (placeholder — build with `vastde build` and push; see [the site deploy](../../../deployments/vss-deploy/README.md))
- **Resources**: Configure CPU/Memory in DataEngine UI pipeline settings
- **Dependencies**: Python 3.11, Cosmos-Embed1 NIM access
