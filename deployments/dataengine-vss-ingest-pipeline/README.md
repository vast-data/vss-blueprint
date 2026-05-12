# Deploy Ingest Pipeline (VAST DataEngine)

Deploy the serverless video processing pipeline using **DataEngine UI** or **vastde CLI**.

## Prerequisites

- A running VAST DataEngine cluster
- User with permissions to setup DataEngine Pipelines (including Vector QueryEngine Identity-Policy)
- Pre-created Topic in VAST Event Broker (e.g., `video-topic`)
- A container registry added to your DataEngine tenant in VMS, with images you built and pushed (see [Build ingest function images](#build-ingest-function-images) below)

## Pipeline Overview

**Pipeline Name:** `video-realtime-processing-pipeline`

```
video-chunks bucket → video-segmenter
                            ↓
video-chunks-segments bucket → video-reasoner → video-embedder → video-vastdb-writer
```

## Files in This Directory

| File | Used By | Description |
|------|---------|-------------|
| `vss-gui-secret-file-template.yaml` | GUI | Secret template for DataEngine UI deployment |
| `vss-cli-secret-file-template.yaml` | CLI | Secret template for vastde CLI deployment |
| `vss-ingest-pipeline-file.yaml` | CLI | Pipeline manifest for `vastde pipelines create` |

---

# Option 1: Deploy with DataEngine UI

## Step 1: Configure Secret

Edit `vss-gui-secret-file-template.yaml`:

```bash
vim vss-gui-secret-file-template.yaml
```

| Section | Key Settings |
|---------|--------------|
| **S3** | `s3accesskey`, `s3secretkey`, `s3endpoint` |
| **Reasoning** | `reasoning_provider` (cosmos/nemotron), endpoint settings |
| **Embedding** | `embedding_local_nim`, `embeddinghost`/`embeddingport`, `nvidia_api_key` |
| **VastDB** | `vdbendpoint`, `vdbaccesskey`, `vdbsecretkey`, `vdbbucket`, `vdbschema`, `vdbcollection` |
| **Processing** | `segment_duration`, `scenario` |

## Step 2: Create Triggers

Navigate to **DataEngine UI → Triggers** and create:

| Trigger Name | Type | Bucket |
|--------------|------|--------|
| `video-chunk-land-trigger` | S3 Bucket | `video-chunks` |
| `video-segment-land-trigger` | S3 Bucket | `video-chunks-segments` |

## Step 3: Create Functions

Navigate to **DataEngine UI → Functions** and create:

| Function | Image (placeholder — use the image you built and pushed) |
|----------|-------|
| `video-segmenter` | `your.registry/vss-video-segmenter:v1` |
| `video-reasoner` | `your.registry/vss-video-reasoner:v1` |
| `video-embedder` | `your.registry/vss-video-embedder:v1` |
| `video-vastdb-writer` | `your.registry/vss-vastdb-writer:v1` |

## Step 4: Create Pipeline

Navigate to **DataEngine UI → Pipelines → Create New Pipeline**

1. **Name:** `video-realtime-processing-pipeline`

2. **Upload secret:** `vss-gui-secret-file-template.yaml`

3. **Create connections:**
   - `video-chunk-land-trigger` → `video-segmenter`
   - `video-segment-land-trigger` → `video-reasoner` → `video-embedder` → `video-vastdb-writer`

4. **Set resources (all functions):**
   - CPU: `1000m - 5000m`
   - Memory: `1280Mi - 2560Mi`

5. **Save and activate the pipeline**

---

# Option 2: Deploy with vastde CLI

## Step 1: Configure Secret

Edit `vss-cli-secret-file-template.yaml`:

```bash
vim vss-cli-secret-file-template.yaml
```

| Section | Key Settings |
|---------|--------------|
| **S3** | `s3accesskey`, `s3secretkey`, `s3endpoint` |
| **Reasoning** | `reasoning_provider` (cosmos/nemotron), endpoint settings |
| **Embedding** | `embedding_local_nim`, `embeddinghost`/`embeddingport`, `nvidia_api_key` |
| **VastDB** | `vdbendpoint`, `vdbaccesskey`, `vdbsecretkey`, `vdbbucket`, `vdbschema`, `vdbcollection` |
| **Processing** | `segment_duration`, `scenario` |

## Step 2: Create Triggers

```bash
vastde triggers create \
  --name video-chunk-land-trigger \
  --type Element \
  --source-bucket video-chunks \
  --events "ObjectCreated:*" \
  --broker-name <your-broker-name> \
  --broker-type Internal \
  --topic <your-topic>

vastde triggers create \
  --name video-segment-land-trigger \
  --type Element \
  --source-bucket video-chunks-segments \
  --events "ObjectCreated:*" \
  --broker-name <your-broker-name> \
  --broker-type Internal \
  --topic <your-topic>
```

## Step 3: Create Functions

Set `--container-registry` to the registry name as configured in VMS. Replace `YOUR_ORG` in `--artifact-source` with the repository namespace/path that registry uses for the image you pushed (for Docker Hub this is typically `username` or org name, so the source looks like `YOUR_ORG/vss-video-segmenter`).

```bash
vastde functions create \
  --name video-segmenter \
  --container-registry dockerio \
  --artifact-source YOUR_ORG/vss-video-segmenter \
  --artifact-type image \
  --image-tag v1

vastde functions create \
  --name video-reasoner \
  --container-registry dockerio \
  --artifact-source YOUR_ORG/vss-video-reasoner \
  --artifact-type image \
  --image-tag v1

vastde functions create \
  --name video-embedder \
  --container-registry dockerio \
  --artifact-source YOUR_ORG/vss-video-embedder \
  --artifact-type image \
  --image-tag v1

vastde functions create \
  --name video-vastdb-writer \
  --container-registry dockerio \
  --artifact-source YOUR_ORG/vss-vastdb-writer \
  --artifact-type image \
  --image-tag v1
```

## Step 4: Configure Pipeline Manifest

Edit `vss-ingest-pipeline-file.yaml` and fill in:
- `kubernetes_cluster_vrn` - Your Kubernetes cluster VRN (run `vastde compute-clusters list`)
- `namespace` - Target Kubernetes namespace
- `topic` fields in link entries (e.g., `vast:dataengine:topics:<broker-name>/<topic>`)

## Step 5: Create and Deploy Pipeline

```bash
vastde pipelines create \
  --name video-realtime-processing-pipeline \
  --config @vss-ingest-pipeline-file.yaml \
  --secret-file vss-cli-secret-file-template.yaml \
  --deploy
```

---

## Function Documentation

| Function | Description | Details |
|----------|-------------|---------|
| video-segmenter | Splits videos into segments | [README](../../source-code/ingest/video-segmenter/README.md) |
| video-reasoner | AI video analysis | [README](../../source-code/ingest/video-reasoner/README.md) |
| video-embedder | Vector embeddings | [README](../../source-code/ingest/video-embedder/README.md) |
| video-vastdb-writer | Stores vectors in VastDB | [README](../../source-code/ingest/vastdb-writer/README.md) |

## Build ingest function images

Build each DataEngine function image with the [VAST DataEngine CLI](https://github.com/vast-data/dataengine-cli) (`vastde`) from the blueprint source directories, then push to your registry. DataEngine workloads typically target `linux/amd64`.

From `vss-blueprint/`:

```bash
# video-segmenter
cd source-code/ingest/video-segmenter
vastde build -t your.registry/vss-video-segmenter:v1 . --platform linux/amd64
docker push your.registry/vss-video-segmenter:v1

# video-reasoner
cd ../video-reasoner
vastde build -t your.registry/vss-video-reasoner:v1 . --platform linux/amd64
docker push your.registry/vss-video-reasoner:v1

# video-embedder
cd ../video-embedder
vastde build -t your.registry/vss-video-embedder:v1 . --platform linux/amd64
docker push your.registry/vss-video-embedder:v1

# vastdb-writer (image name vss-vastdb-writer)
cd ../vastdb-writer
vastde build -t your.registry/vss-vastdb-writer:v1 . --platform linux/amd64
docker push your.registry/vss-vastdb-writer:v1
```

Replace `your.registry` with your real registry. Use the same names and tags in the DataEngine UI, in `vastde functions create` (see Step 3), and in your VMS registry configuration.
