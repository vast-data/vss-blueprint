# VAST DataEngine - Video Search and Summarization (VSS) Foundation Stack


- A Real-Time Video Search and Analysis system powered by [VAST DataEngine](https://www.vastdata.com/platform/dataengine) and [Nvidia NIMs](https://www.nvidia.com/en-eu/ai-data-science/products/nim-microservices/).

- Models: **Cosmos-Reason3** nano (`nvidia/cosmos3-nano-reasoner`) for video reasoning and search synthesis, **Cosmos-Embed1** (`nvidia/cosmos-embed1`, 256-dim) for text and visual embeddings, and **YOLO11** for object detection. GPU install: [vss-blueprint-models](scripts/vss-blueprint-models/README.md).

- This Blueprint is were released to bridge the gap between [NVIDIA’s VSS reference architectures](https://build.nvidia.com/nvidia/video-search-and-summarization/blueprintcard), and showcases the e2e utilization of the full VAST AI OS production-grade capabilities,
including Agentic & Serverless Event-based Compute Framework and VastDB Vector-Store.

---

![Demo11](./docs/0902.gif)

---

## Overview

The system has three main parts:
1. **K8s Application** - Web UI and REST API (Kubernetes)
2. **Ingest Pipeline** - Serverless video processing (VAST DataEngine)
3. **Enrichment Pipeline** - Scheduled prompt-suggester (search chips + key events)

![VSS Blueprint Architecture](source-code/video-demo-diagram.png) — see also the interactive diagram in the app (**Show Blueprint Diagram**) or [`blueprint.html`](source-code/retrieval/video-frontend/src/assets/blueprint.html)

---

## Deployment

Install one site with [vss-deploy](deployments/vss-deploy/README.md). That guide lists what must already exist, which `site.yaml` names must be new, and the `deploy.sh` flags.

### Quick Start

1. **GPU models** (Reason3, Embed1, YOLO on a GPU host): [vss-blueprint-models](scripts/vss-blueprint-models/README.md)

2. **Build and push images** (`REGISTRY` is required; `TAG` defaults to `latest`, the same as an empty tag in `site.yaml`):
   ```bash
   REGISTRY=your.registry/org source-code/scripts/build-retrieval-images.sh
   REGISTRY=your.registry/org source-code/scripts/build-vastde-functions.sh
   ```
   Details: [scripts](source-code/scripts/README.md)

3. **Deploy the site:**
   ```bash
   cd deployments/vss-deploy
   cp site.yaml.example site.yaml
   ./deploy.sh site.yaml --check
   ./deploy.sh site.yaml
   ```

4. **Test:** Upload a video and search at `http://vss-lab.<ingress_domain>` (HTTPS when `site.yaml` has both a cert and a key).

---

## Key Features

| Feature | Description | Documentation |
|---------|-------------|---------------|
| **Video Analysis Prompts** | Configurable AI scenarios (surveillance, traffic, live_driving, etc.) | [video-reasoner](source-code/ingest/video-reasoner/README.md) |
| **Ingest metadata config** | Single definition for upload metadata UI + S3 mapping (`ingest_metadata.py`) | [shared](source-code/shared/README.md) |
| **Custom AI Prompts** | Per-video custom prompts (max length in `ingest_metadata.py`) | [video-reasoner](source-code/ingest/video-reasoner/README.md#custom-prompts) |
| **Metadata Filters** | Filter by camera_id, location, capture_type | [ingest](source-code/ingest/README.md) |
| **Advanced Search & AI Settings** | Max clip cards, synthesis clip count, caption/video weight, similarity | [video-backend](source-code/retrieval/video-backend/README.md#gui-settings) |
| **Explore mode** | Browse uploads by day and location — Complete or Incomplete; summarize or delete a chunk | [video-frontend](source-code/retrieval/video-frontend/README.md#application-modes) |
| **Stream chunks** | Same `stream_id` on upload, streaming, or batch sync; Explore plays chunk N/total with Previous/Next | [video-backend](source-code/retrieval/video-backend/README.md#upload-streams-and-delete) |
| **Delete a video** | Explore delete, or `DELETE /api/v1/videos`, removes that upload’s S3 objects, VastDB rows, and sidecars | [video-backend](source-code/retrieval/video-backend/README.md#upload-streams-and-delete) |
| **Data Dashboard** | VastDB stats, ingest health, S3 pipeline inventory, live key events | [video-frontend](source-code/retrieval/video-frontend/README.md#application-modes) |
| **Search suggestions & key events** | Grounded ≤8-word rephrases of segment `reasoning_content` → `vss-events-collection` | [prompt-suggester](source-code/enrichment/prompt-suggester/README.md) |
| **Object detection counts** | YOLO peak concurrent per class (`object_counts`); UI chips + dashboard heatmap | [video-detector](source-code/ingest/video-detector/README.md) |
| **Agent APIs** | Tool wrappers + grounded Q&A for external agents | [video-backend](source-code/retrieval/video-backend/README.md#agent-apis) |
| **Time Filtering** | Filter by upload time (presets or custom range) | [video-backend](source-code/retrieval/video-backend/README.md#gui-settings) |
| **Video Streaming** | Capture YouTube videos to S3 | [video-streaming](source-code/video-streaming/README.md) |
| **Batch Sync** | Copy MP4 files between S3 buckets | [video-batch-sync](source-code/video-batch-sync/README.md) |
| **Authentication** | VAST username + password | [video-backend](source-code/retrieval/video-backend/README.md) |

---

## Component Documentation

| Component | Description |
|-----------|-------------|
| [shared](source-code/shared/README.md) | Cross-service modules (`ingest_metadata.py` — upload metadata UI + S3) |
| [scripts](source-code/scripts/README.md) | Build scripts for retrieval images and DataEngine functions |
| [video-backend](source-code/retrieval/video-backend/README.md) | REST API, authentication, search |
| [video-frontend](source-code/retrieval/video-frontend/README.md) | Angular web UI (Search, Explore, Dashboard) |
| [prompt-suggester](source-code/enrichment/prompt-suggester/README.md) | Grounded search chips + key events → VastDB |
| [video-streaming](source-code/video-streaming/README.md) | YouTube capture service |
| [video-batch-sync](source-code/video-batch-sync/README.md) | S3 batch copy service |
| [video-segmenter](source-code/ingest/video-segmenter/README.md) | Splits videos into segments |
| [video-detector](source-code/ingest/video-detector/README.md) | YOLO11 object detection + peak counts + bbox sidecars |
| [video-reasoner](source-code/ingest/video-reasoner/README.md) | Plain searchable `reasoning_content` (Cosmos-Reason3) |
| [video-embedder](source-code/ingest/video-embedder/README.md) | Text (`reasoning_content`) + visual embeddings |
| [vastdb-writer](source-code/ingest/vastdb-writer/README.md) | Stores vectors and segment rows in VastDB |

---

## Pipeline Flow

```
Upload Video → vss-chunks bucket
                    ↓
            video-segmenter (~5s clips, trim to capture_interval)
                    ↓
            video-detector (YOLO11 → object_classes + peak object_counts + bbox sidecars)
                    ↓
            video-reasoner (Cosmos-Reason3 → searchable plain reasoning_content ≤1024)
                    ↓
            video-embedder (vectors from reasoning_content + vectors_visual from MP4)
                    ↓
            vastdb-writer (segment rows in VastDB)
                    ↓
              Search Ready
                    ↓
         prompt-suggester (optional enrichment)
                    ↓
         vss-events-collection (grounded search chips + key events)
```

**Search flow:** query embed (Cosmos-Embed1) → hybrid search (`reasoning_content` + visual) → clip cards with upload-time badge and match timeline → Cosmos-Reason3 synthesis → player with bbox overlay and `label N` object chips

**Explore flow:** browse by upload date and **location** (fully indexed chunks only) → clip cards with segment timeline and upload-time badge → full-chunk player with bbox toggle and **Summarize Video**

**Dashboard flow:** VastDB KPIs + ingest quality + object **instance** heatmap (sum of peak counts) + S3 vs index alignment → key events (grounded rephrases) with in-place segment preview

**Enrichment flow:** sample recent `reasoning_content` → one grounded ≤8-word phrase per sample (no inventing) → search chips + key events

**Agent flow:** `GET/POST /api/v1/tools/*` for VastDB/search/explore/synthesize → `POST /api/v1/agent/ask` or `/search-and-answer` for grounded answers

Interactive diagram: open **Show Blueprint Diagram** in the app, or `source-code/retrieval/video-frontend/src/assets/blueprint.html`

---

## Need Help?

- **Deploy**: See [vss-deploy](deployments/vss-deploy/README.md)
- **Community**: [VAST Community Forums](https://community.vastdata.com/)
