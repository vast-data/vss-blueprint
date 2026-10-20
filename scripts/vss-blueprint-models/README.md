# VSS GPU models (Reason3, Embed1, YOLO)

Deploy **Cosmos-Reason3**, **Cosmos-Embed1**, and **YOLO11** with Docker on a bare-metal GPU host. Used by the VSS ingest pipeline and K8s backend. Run `deploy.sh` from this directory (`scripts/vss-blueprint-models/`).

The VSS pipeline can run against Cosmos-Reason3 or Cosmos-Reason2. This script starts Cosmos-Reason3 nano, Cosmos-Embed1, and YOLO11.

The [site deploy](../../deployments/vss-deploy/README.md) puts these hosts in `site.yaml`. Vector dims and local NIM notes: [COSMOS_LOCAL_STACK.md](../../docs/COSMOS_LOCAL_STACK.md).

## What it deploys

| Service | Container | Default port | GPU | Image |
| ------- | --------- | ------------ | --- | ----- |
| Reason3 nano | `cosmos3-nano-reasoner` | **8001** | `REASONER_GPU` | `nvcr.io/nim/nvidia/cosmos3-reasoner:1.7.0` (`NIM_MODEL_SIZE=nano`, served as `nvidia/cosmos3-nano-reasoner`) |
| Embed1 | `cosmos-embed1` | **8002** | `EMBED_YOLO_GPU` | `nvcr.io/nim/nvidia/cosmos-embed1:1.1.0` |
| YOLO | `yolo-infer` | **8003** | `EMBED_YOLO_GPU` | `yolo-infer:latest` (built locally from `yolo-infer/`) |

**GPU layout:** With no index, every GPU is treated as free. Embed1 and YOLO use the first GPU. Reason3 nano uses the second when the host has two or more; with one GPU, all selected services share it. Pass indexes to pin devices instead.

**VSS secrets:** set `cosmos_host` / `cosmos_port` (8001), `cosmos_model` to `nvidia/cosmos3-nano-reasoner`, `embeddinghost` / `embeddingport` (8002), and `yolo_infer_host` / `yolo_infer_port` (8003) to this host. If a template still shows YOLO `8022`, override `--yolo-port` or change the secret to **8003**.

## Prerequisites

- NVIDIA driver + `nvidia-smi`
- Docker + [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)
- NGC API key ([generate here](https://org.ngc.nvidia.com/setup/api-key))
- NGC licenses accepted for [cosmos3-reasoner](https://catalog.ngc.nvidia.com/orgs/nim/nvidia/containers/cosmos3-reasoner) and [Cosmos-Embed1](https://catalog.ngc.nvidia.com/orgs/nim/nvidia/containers/cosmos-embed1)
- YOLO sources in `yolo-infer/` next to `deploy.sh` (or set `YOLO_INFER_DIR`)

## Quick start

From the blueprint repo:

```bash
cd scripts/vss-blueprint-models
export NGC_API_KEY='<your-ngc-api-key>'
./deploy.sh
```

- No args — GPUs are free: one GPU runs everything, two or more put embed+yolo on the first and Reason3 nano on the second
- One arg — all selected services on that GPU
- Two args — embed+yolo on the first, Reason3 nano on the second

```bash
./deploy.sh 2                          # all three on GPU 2
./deploy.sh --embedder --yolo 2        # embedder + YOLO on GPU 2
./deploy.sh 2 3 --reasoner-port 8001 --embed-port 8002 --yolo-port 8003
```

Defaults are **8001 / 8002 / 8003** (VSS convention). Point `cosmos_*`, `embedding*`, and `yolo_infer_*` in `vss2-secret` / `backend-secret.yaml` at a host DataEngine and K8s can reach (not always `127.0.0.1`).

The script logs into `nvcr.io`, starts containers, then polls health (`loading` while a NIM downloads, `ok` when ready). First NIM start can take several minutes.

| Variable | Default | Purpose |
|----------|---------|---------|
| `HEALTH_WAIT_SEC` | `600` | Max seconds to wait per service (Reason3 uses this; embed 180s, yolo 120s) |

## Arguments

| Positional | Meaning |
| ---------- | ------- |
| `GPU` | Optional pin for embed+yolo (and Reason3 nano if `REASONER_GPU` omitted). Omitted GPUs are treated as free. |
| `REASONER_GPU` | Optional pin for Reason3 nano. Omitted when `GPU` is set: Reason3 nano uses that same GPU. |

## Options

| Flag | Description |
| ---- | ----------- |
| `--all` | Deploy all three (default if no service flag is given) |
| `--reasoner` | Deploy Reason3 only |
| `--embedder` | Deploy Embed1 only |
| `--yolo` | Deploy YOLO only |
| `--reasoner-port P` | Host port for Reason3 (default `8001`) |
| `--embed-port P` | Host port for Embed1 (default `8002`) |
| `--yolo-port P` | Host port for YOLO (default `8003`) |
| `--ngc-api-key K` | NGC API key (alternative to `NGC_API_KEY` env) |
| `--build-yolo` | Force `docker build` even if `yolo-infer:latest` exists |
| `-h`, `--help` | Show usage |

```bash
./deploy.sh --embedder --yolo 2   # embedder + yolo on GPU 2
./deploy.sh --reasoner 3          # reasoner only on GPU 3
./deploy.sh --yolo --build-yolo 2
./deploy.sh --embedder --yolo 2 3 # second GPU unused if reasoner is not deployed
```

## Environment variables

| Variable | Default | Purpose |
| -------- | ------- | ------- |
| `NGC_API_KEY` | (prompt if TTY) | Pull NIM weights from NGC |
| `REASONER_PORT` | `8001` | Reason3 host port |
| `EMBED_PORT` | `8002` | Embed1 host port |
| `YOLO_PORT` | `8003` | YOLO host port |
| `YOLO_INFER_DIR` | `yolo-infer` (sibling of `deploy.sh`) | YOLO source for build and bind mount |
| `YOLO_IMAGE` | `yolo-infer:latest` | Docker image tag for YOLO |

If `YOLO_IMAGE` is missing locally, the script builds it once automatically. Use `--build-yolo` to rebuild. On hosts with an old `yolo:latest` image only, either build or set `YOLO_IMAGE=yolo:latest`.

## Verify

```bash
curl -s "http://127.0.0.1:${REASONER_PORT:-8001}/v1/health/ready"
curl -s "http://127.0.0.1:${EMBED_PORT:-8002}/v1/health/ready"
curl -s "http://127.0.0.1:${YOLO_PORT:-8003}/healthz"
```

```bash
docker ps --format 'table {{.Names}}\t{{.Ports}}\t{{.Status}}' \
  | grep -E 'cosmos3-nano|cosmos-embed1|yolo-infer|NAMES'
nvidia-smi
```

## Tear down

```bash
docker rm -f cosmos3-nano-reasoner cosmos-embed1 yolo-infer
```

NIM caches stay in `~/.cache/nim/` for faster redeploys.

## Troubleshooting

| Issue | Fix |
| ----- | --- |
| `NGC_API_KEY is empty` | `export NGC_API_KEY=...` or `--ngc-api-key` |
| Reason3 health not 200 yet | Wait for model download; `docker logs -f cosmos3-nano-reasoner` |
| Reason3 OOM | Pick a freer `REASONER_GPU`; or add `-e 'NIM_PASSTHROUGH_ARGS=--gpu-memory-utilization 0.85'` to the Reason3 `docker run` |
| YOLO `cuda_available: false` | Ensure `--runtime=nvidia` (script does this); check `nvidia-smi` |
| `yolo-infer/main.py not found` | Run from `scripts/vss-blueprint-models`, or set `YOLO_INFER_DIR` to a directory that contains `main.py` |
| Port in use | `docker ps` / stop old `yolo-object` or another container on 8001/8002/8003 |
