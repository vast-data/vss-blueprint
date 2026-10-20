# Manual DataEngine steps

The install that creates storage, the pipeline, and the Kubernetes app is [the site deploy](../README.md). Use this directory only when that stack already exists, or when you need to create or change one function, trigger, secret, or pipeline by hand.

Names in these templates (`video-realtime-processing-pipeline`, `video-chunk-land-trigger`, and so on) are the manual examples. A site created by `deploy.sh` uses the names in `site.yaml` (`vss-pipeline`, `vss-chunk-trigger`, and the rest). Do not apply these manifests on top of a `deploy.sh` pipeline unless you mean to create a second one.

Function images read the DataEngine secret named `vss2-secret`. Keep that name.

Deploy VSS serverless pipelines using **DataEngine UI** or **vastde CLI**:

1. **Ingest** — S3-triggered video processing (`video-realtime-processing-pipeline`)
2. **Enrichment** — scheduled prompt-suggester (`vss-enrichment-pipeline`)

Both pipelines share secret name **`vss2-secret`** (table/bucket values use `vss-*` — see templates).

## Prerequisites

The [site deploy](../README.md) does not attach a Data Engine identity policy. These steps are the hand path.

- A running VAST DataEngine cluster
- User with permissions to setup DataEngine Pipelines
- Pre-created Topic in VAST Event Broker (e.g., `video-topic`)
- A container registry added to your DataEngine tenant in VMS, with images you built and pushed (jump to **Build DataEngine function images** later in this file)
- GPU services (Reason3, Embed1, YOLO) reachable from DataEngine workers — see [vss-blueprint-models](../../../scripts/vss-blueprint-models/README.md)

## Pipeline Overview

**Ingest — `video-realtime-processing-pipeline`**

```
vss-chunks bucket → video-segmenter
                            ↓
vss-chunks-segments bucket → video-detector → video-reasoner → video-embedder → video-vastdb-writer
```

**Enrichment — `vss-enrichment-pipeline` (optional, after ingest is producing rows)**

```
Schedule trigger → prompt-suggester → vss-events-collection (search chips + key events)
```

## Files in This Directory

| File | Used By | Description |
|------|---------|-------------|
| `vss-gui-secret-file-template.yaml` | GUI | Shared secret template (`vss2-secret`) for ingest + enrichment |
| `vss-cli-secret-file-template.yaml` | CLI | Shared secret template (`vss2-secret`) for ingest + enrichment |
| `vss-ingest-pipeline-file.yaml` | CLI | Ingest pipeline manifest for `vastde pipelines create` |
| `vss-enrichment-pipeline-file.yaml` | CLI | Enrichment pipeline manifest (scheduled prompt-suggester) |

---

# Option 1: Deploy with DataEngine UI

Ingest (steps 1–4) is required. Enrichment (step 5) is optional.

## Step 1: Configure Secret

Copy `vss-gui-secret-file-template.yaml` to a local file, fill credentials, and upload in DataEngine UI — **do not commit** files with real keys (templates with empty values are safe in git).

```bash
vim vss-gui-secret-file-template.yaml
```

Secret **name** in DataEngine must be `vss2-secret`. Bucket/table **values** use the `vss-*` namespace (`vss-chunks`, `vss-collection`, etc.). Same secret is reused for enrichment.

| Section | Key Settings |
|---------|--------------|
| **S3** | `s3accesskey`, `s3secretkey`, `s3endpoint` |
| **Reasoning** | Cosmos-Reason3 (`cosmos_host`, `cosmos_port: 8001`, `cosmos_model: nvidia/cosmos3-nano-reasoner`) |
| **Embedding** | Text + visual NIM (`embeddinghost` / `embeddingport: 8002`, `embeddingmodel: nvidia/cosmos-embed1`); recreate VastDB collection after schema changes |
| **VastDB** | `vdbendpoint`, `vdbaccesskey`, `vdbsecretkey`, `vdbbucket`, `vdbschema`, `vdbcollection` |
| **YOLO** | `yolo_infer_host`, `yolo_infer_port` (`8003` with [vss-blueprint-models](../../../scripts/vss-blueprint-models/README.md); templates may still show `8022`), `detection_sidecar_prefix` |
| **Enrichment** | `vdbpromptscollection` (`vss-events-collection`), `suggestions_*` (used by prompt-suggester) |
| **Processing** | `segment_duration`, `scenario` (default prompt key; see [video-reasoner README](../../../source-code/ingest/video-reasoner/README.md). GUI scenario labels: [shared/ingest_metadata.py](../../../source-code/shared/ingest_metadata.py)) |

## Step 2: Create Triggers

Navigate to **DataEngine UI → Triggers** and create:

| Trigger Name | Type | Bucket |
|--------------|------|--------|
| `video-chunk-land-trigger` | S3 Bucket | `vss-chunks` |
| `video-segment-land-trigger` | S3 Bucket | `vss-chunks-segments` |

## Step 3: Create Functions

Navigate to **DataEngine UI → Functions** and create:

| Function | Image (names from `build-vastde-functions.sh`) |
|----------|-------|
| `video-segmenter` | `<registry-host>/<path>/vss2-segmenter:<image-tag>` |
| `video-detector` | `<registry-host>/<path>/vss2-detector:<image-tag>` |
| `video-reasoner` | `<registry-host>/<path>/vss2-reasoner:<image-tag>` |
| `video-embedder` | `<registry-host>/<path>/vss2-embedder:<image-tag>` |
| `video-vastdb-writer` | `<registry-host>/<path>/vss2-vastdb:<image-tag>` |

## Step 4: Create Pipeline

Navigate to **DataEngine UI → Pipelines → Create New Pipeline**

1. **Name:** `video-realtime-processing-pipeline`

2. **Upload secret:** `vss-gui-secret-file-template.yaml`

3. **Create connections:**
   - `video-chunk-land-trigger` → `video-segmenter`
   - `video-segment-land-trigger` → `video-detector` → `video-reasoner` → `video-embedder` → `video-vastdb-writer`

4. **Set resources (all functions):**
   - CPU: `1000m - 5000m`
   - Memory: `1280Mi - 2560Mi`

5. **Save and activate the pipeline**

## Step 5: Enrichment pipeline (optional)

Skip unless you want search suggestion chips and dashboard key events. Deploy after ingest is writing rows to `vss-collection`. Reuse **`vss2-secret`** — do not create a second secret. Details: [prompt-suggester](../../../source-code/enrichment/prompt-suggester/README.md).

1. **Trigger:** DataEngine UI → Triggers → create `vss-prompt-suggester-scheduled-trigger` (type **Schedule**, e.g. every 5–15 minutes).
2. **Function:** create `prompt-suggester` with image `<registry-host>/<path>/vss2-events:<image-tag>`.
3. **Pipeline:** name `vss-enrichment-pipeline`; attach existing `vss2-secret`; connect `vss-prompt-suggester-scheduled-trigger` → `prompt-suggester`.
4. **Resources:** CPU `200m - 1000m`, memory `256Mi - 512Mi`. Activate.

---

# Option 2: Deploy with vastde CLI

Ingest (steps 1–5) is required. Enrichment (step 6) is optional.

## Step 1: Configure Secret

Edit `vss-cli-secret-file-template.yaml`:

```bash
vim vss-cli-secret-file-template.yaml
```

| Section | Key Settings |
|---------|--------------|
| **S3** | `s3accesskey`, `s3secretkey`, `s3endpoint` |
| **Reasoning** | Cosmos-Reason3 (`cosmos_host`, `cosmos_port: 8001`, `cosmos_model: nvidia/cosmos3-nano-reasoner`) |
| **Embedding** | Text + visual NIM (`embeddinghost` / `embeddingport: 8002`, `embeddingmodel: nvidia/cosmos-embed1`); recreate VastDB collection after schema changes |
| **VastDB** | `vdbendpoint`, `vdbaccesskey`, `vdbsecretkey`, `vdbbucket`, `vdbschema`, `vdbcollection` |
| **YOLO** | `yolo_infer_host`, `yolo_infer_port` (`8003` with [vss-blueprint-models](../../../scripts/vss-blueprint-models/README.md); templates may still show `8022`), `detection_sidecar_prefix` |
| **Enrichment** | `vdbpromptscollection` (`vss-events-collection`), `suggestions_*` (used by prompt-suggester) |
| **Processing** | `segment_duration`, `scenario` (default prompt key; see [video-reasoner README](../../../source-code/ingest/video-reasoner/README.md). GUI scenario labels: [shared/ingest_metadata.py](../../../source-code/shared/ingest_metadata.py)) |

## Step 2: Create Triggers

```bash
vastde triggers create \
  --name video-chunk-land-trigger \
  --type Element \
  --source-bucket vss-chunks \
  --events "ObjectCreated:*" \
  --broker-name <your-broker-name> \
  --broker-type Internal \
  --topic <your-topic>

vastde triggers create \
  --name video-segment-land-trigger \
  --type Element \
  --source-bucket vss-chunks-segments \
  --events "ObjectCreated:*" \
  --broker-name <your-broker-name> \
  --broker-type Internal \
  --topic <your-topic>
```

## Step 3: Create Functions

Set `--container-registry` to the registry name as configured in VMS. Replace `YOUR_ORG` in `--artifact-source` with the repository namespace/path that registry uses for the image you pushed (for Docker Hub this is typically `username` or org name, so the source looks like `<path>/vss2-segmenter`).

```bash
vastde functions create \
  --name video-segmenter \
  --container-registry dockerio \
  --artifact-source YOUR_ORG/vss2-segmenter \
  --artifact-type image \
  --image-tag v1

vastde functions create \
  --name video-detector \
  --container-registry dockerio \
  --artifact-source YOUR_ORG/vss2-detector \
  --artifact-type image \
  --image-tag v1

vastde functions create \
  --name video-reasoner \
  --container-registry dockerio \
  --artifact-source YOUR_ORG/vss2-reasoner \
  --artifact-type image \
  --image-tag v1

vastde functions create \
  --name video-embedder \
  --container-registry dockerio \
  --artifact-source YOUR_ORG/vss2-embedder \
  --artifact-type image \
  --image-tag v1

vastde functions create \
  --name video-vastdb-writer \
  --container-registry dockerio \
  --artifact-source YOUR_ORG/vss2-vastdb \
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

## Step 6: Enrichment pipeline (optional)

Skip unless you want search suggestion chips and dashboard key events. Deploy after ingest is writing rows to `vss-collection`. Reuse **`vss2-secret`** — do not create a second secret. Details: [prompt-suggester](../../../source-code/enrichment/prompt-suggester/README.md).

Create the schedule trigger in DataEngine (name must match the YAML VRN: `vss-prompt-suggester-scheduled-trigger`). Then:

```bash
vastde functions create \
  --name prompt-suggester \
  --container-registry dockerio \
  --artifact-source YOUR_ORG/vss2-events \
  --artifact-type image \
  --image-tag v1
```

Edit `vss-enrichment-pipeline-file.yaml` (`kubernetes_cluster_vrn`, `namespace`, `topic`), then:

```bash
vastde pipelines create \
  --name vss-enrichment-pipeline \
  --config @vss-enrichment-pipeline-file.yaml \
  --secret-file vss-cli-secret-file-template.yaml \
  --deploy
```

---

## Function Documentation

| Function | Description | Details |
|----------|-------------|---------|
| video-segmenter | Splits videos into segments | [README](../../../source-code/ingest/video-segmenter/README.md) |
| video-detector | YOLO11 object detection + sidecar | [README](../../../source-code/ingest/video-detector/README.md) |
| video-reasoner | AI video analysis (Cosmos-Reason3) | [README](../../../source-code/ingest/video-reasoner/README.md) |
| video-embedder | Vector embeddings | [README](../../../source-code/ingest/video-embedder/README.md) |
| video-vastdb-writer | Stores vectors in VastDB | [README](../../../source-code/ingest/vastdb-writer/README.md) |
| prompt-suggester | Scheduled enrichment → search chips + key events | [README](../../../source-code/enrichment/prompt-suggester/README.md) |

## Build DataEngine function images

Aligned with DataEngine **5.5.0-sp2** (`vastde` v5.5.0-sp2, builder `vastdataorg/vast-builder:v5.5.0-sp2`). Install the CLI and select that builder from [Get vastde and roll function images](../README.md#get-vastde-and-roll-function-images). From `source-code`, `TAG` and `REG` are the function image tag and the registry path. They are separate from the 5.5.0-sp2 CLI and builder versions. Paths below are relative to that directory. `"${name}:latest"` keeps the tag in zsh.

```bash
cd <checkout>/source-code
TAG=<image-tag>
REG=<registry-host>/<path>

build() {
  local dir="$1" name="$2"
  echo "==> ${name}"
  (cd "${dir}" && vastde functions build "${name}") || return 1
  docker tag "${name}:latest" "${REG}/${name}:${TAG}" || return 1
  docker push "${REG}/${name}:${TAG}"
}

build ingest/video-segmenter vss2-segmenter
build ingest/video-detector vss2-detector
build ingest/video-reasoner vss2-reasoner
build ingest/video-embedder vss2-embedder
build ingest/vastdb-writer vss2-vastdb
build enrichment/prompt-suggester vss2-events
```

The same six builds are `REGISTRY=<registry-host>/<path> TAG=<image-tag> source-code/scripts/build-vastde-functions.sh`. That script reads `REGISTRY`, not `REG`. Use these image names in the DataEngine UI and in `vastde functions create`.
