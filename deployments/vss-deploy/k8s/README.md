# Kubernetes app manifests

The install that creates storage, the pipeline, and this app is [the site deploy](../README.md). `deploy.sh` copies these files, writes `backend-secret.yaml` from `site.yaml`, and runs `QUICK_DEPLOY.sh`.

Use this directory only when the storage user, buckets, topic, and pipeline already exist and you need to apply or update the web app by itself.

`QUICK_DEPLOY.sh` expects `backend-secret.yaml` beside the script. The full deploy writes that file. A manual run copies the example and fills it in. Do not commit the filled file.

## Prerequisites

- **Kubernetes access:**
  - `kubectl` installed and configured for your cluster
  - Ability to create namespaces and deploy resources

- **VAST cluster access:**
  - Cluster name (e.g., `v1234`) - used as part of the URL
  - VMS hostname and tenant name for user authentication (see [User Authentication](../../../source-code/retrieval/video-backend/README.md))

- **Storage resources:**
  - S3 buckets: `vss-chunks` and `vss-chunks-segments` (segmenter writes `{upload_bucket}-segments` by default)
  - VastDB bucket: `vss-vastdb-bucket`, schema `vss-schema`, tables `vss-collection` and `vss-events-collection`

- **AI/ML services:**
  - Cosmos-Embed1 NIM (hybrid search embeddings)
  - Cosmos-Reason3 NIM (search/explore synthesis; same host as ingest reasoner)
  - YOLO11 infer service (ingest `video-detector`)
  - Optional NVIDIA Cloud API key when `embedding_local_nim: false`
  - GPU host deploy: [vss-blueprint-models](../../../scripts/vss-blueprint-models/README.md)

- **Network access:**
  - Ability to modify `/etc/hosts` on your local machine

---

## Step 1: Configure Backend Secret

Copy the example and fill in credentials locally (**do not commit** `backend-secret.yaml`):

```bash
cp backend-secret.yaml.example backend-secret.yaml
vim backend-secret.yaml
```

| Section | Key Settings |
|---------|--------------|
| **VastDB** | `vdb_endpoint`, `vdb_bucket` (`vss-vastdb-bucket`), `vdb_schema` (`vss-schema`), `vdb_collection` (`vss-collection`), `vdb_prompts_collection` (`vss-events-collection`), credentials |
| **S3** | `s3_endpoint` (must match tenant), `s3_upload_bucket` (`vss-chunks`), `s3_segments_bucket` (`vss-chunks-segments`), credentials |
| **Cosmos-Embed1** | `embedding_host`, `embedding_port`, `embedding_model`, `embedding_local_nim`, `nvidia_api_key` |
| **Cosmos-Reason3** | `cosmos_host`, `cosmos_port`, `cosmoshttpscheme`, `cosmos_model`, `synthesis_*` |
| **UI** | `display_timezone` (IANA, e.g. `Asia/Jerusalem`) |
| **Auth** | `vast_host`, `tenant_name`, `jwt_secret` (see [setup](../../../source-code/retrieval/video-backend/README.md#user-authentication)) |

---

## Step 2: Docker Images

Build images from the repo root, then set those image strings in each `*-deployment.yaml`.

```bash
REGISTRY=your.registry/vss TAG=v1 source-code/scripts/build-retrieval-images.sh
```

Manual builds from `source-code/` as context (see [shared README](../../../source-code/shared/README.md#docker-builds)):

```bash
cd source-code

docker buildx build -f retrieval/video-backend/Dockerfile -t your.registry/vss-video-backend:v2 --push .

docker buildx build -f retrieval/video-frontend/Dockerfile -t your.registry/vss-video-frontend:v2 --push retrieval/video-frontend

docker buildx build -f video-streaming/Dockerfile -t your.registry/vss-video-streaming:v2 --push .

docker buildx build -f video-batch-sync/Dockerfile -t your.registry/vss-video-batch-sync:v2 --push .
```

If your cluster requires a specific architecture (for example `linux/amd64`), add `--platform linux/amd64` to each `docker build`. Ensure your registry is reachable from the cluster (image pull secrets if the registry is private).

---

## Step 3: Deploy

```bash
./QUICK_DEPLOY.sh <namespace> <ingress_domain>

# Example:
./QUICK_DEPLOY.sh vss vastdata.com
```

Ingress hosts are `vss-lab`, `vss-backend`, `vss-streamer`, and `vss-batch-sync` under that domain. Image references in the deployment YAML are placeholders. `deploy.sh` replaces them from `site.yaml`. A manual run must set those image strings before applying.

**What gets deployed:**
- Backend Service (FastAPI) - REST API
- Frontend Service (Angular) - Web UI
- Video Streaming Service - YouTube capture
- Video Batch Sync Service - S3 copy
- Ingress Resources - External access

---

## Step 4: Wait for Pods

```bash
kubectl get pods -n <namespace> -w
```

---

## Step 5: Configure DNS

Get ingress IP:
```bash
kubectl get ingress -n <namespace>
```

Add to `/etc/hosts` (on your local machine) when you are not using the ingress domain's DNS:
```
<INGRESS_IP> vss-lab.<ingress_domain>
```

---

## Step 6: Access UI

```
http://vss-lab.<ingress_domain>
```

With `https.cert` and `https.key` set in `site.yaml`, the full deploy serves this host over HTTPS.

---

## Troubleshooting

### Cannot access web UI
- Check pods: `kubectl get pods -n <namespace>`
- Check ingress: `kubectl get ingress -n <namespace>`
- Verify `/etc/hosts` entry

### Authentication fails
- Verify `s3_endpoint` matches tenant
- See [User Authentication](../../../source-code/retrieval/video-backend/README.md#user-authentication)

### View Logs

```bash
# Backend
kubectl logs -f -n <namespace> -l app=video-backend

# Frontend  
kubectl logs -f -n <namespace> -l app=video-frontend

# Streaming
kubectl logs -f -n <namespace> -l app=video-stream-capture

# Batch Sync
kubectl logs -f -n <namespace> -l app=video-batch-sync
```
