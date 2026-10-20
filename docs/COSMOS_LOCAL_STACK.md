# Local Cosmos stack (Reason3 + Embed1 + YOLO)

Deploy the GPU services with Docker from **[scripts/vss-blueprint-models](../scripts/vss-blueprint-models/README.md)** (`./deploy.sh` on a host with NVIDIA GPUs).

## Services

| Service | Container / image | Host port | Used for |
|---------|-------------------|-----------|----------|
| **Cosmos-Reason3 nano** | `cosmos3-nano-reasoner` (`nvcr.io/nim/nvidia/cosmos3-reasoner:1.7.0`, `NIM_MODEL_SIZE=nano`, model `nvidia/cosmos3-nano-reasoner`) | **8001** | `video-reasoner` (plain `reasoning_content`) + `prompt-suggester` |
| **Cosmos-Embed1** | `cosmos-embed1` (`nvcr.io/nim/nvidia/cosmos-embed1:1.1.0`) | **8002** | `video-embedder` + search backend |
| **YOLO11** | `yolo-infer` (built from `yolo-infer/`) | **8003** | `video-detector` |

## Secret alignment (`vss-gui-secret-file-template.yaml` / CLI / `backend-secret.yaml`)

- `cosmos_host` / `cosmos_port: 8001` / `cosmos_model: nvidia/cosmos3-nano-reasoner`
- `embeddinghost` / `embeddingport: 8002` / `embeddingmodel: nvidia/cosmos-embed1`
- `embeddingdimensions: 256` (required — Embed1 is **256-dim**, not 2048)
- `yolo_infer_host` / `yolo_infer_port: 8003` (script default; templates may still show `8022`)

Backend `backend-secret.yaml` must use the same embed host/port/model/dimensions for search.

## How embeddings work in code

| Column | Ingest | API |
|--------|--------|-----|
| `vectors` | `reasoning_content` → Cosmos-Embed1 text (`request_type: query` / `bulk_text`) | Text query embed |
| `vectors_visual` | Segment **MP4** → Cosmos-Embed1 video (`data:video/mp4;base64,...`) | Text query embed (same model space) |

Client: `source-code/ingest/video-embedder/common/cosmos_embed_client.py`

## DataEngine networking

Use the IP/hostname that **DataEngine workers** can reach (not always `127.0.0.1` if functions run on another node). Same for the K8s backend calling embed for search.

## VastDB

Recreate the collection when switching to 256-dim vectors (was 2048 for Llama embed models).
