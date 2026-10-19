# VSS GPU models (Reason2, Embed1, YOLO)

Deploy **Cosmos-Reason2**, **Cosmos-Embed1**, and **YOLO11** on a bare-metal GPU host with Docker. Used by the VSS ingest pipeline and K8s backend.

This directory lives at `scripts/vss-blueprint-models/` in [vss-blueprint](../../README.md).

## Quick start

From the **blueprint repo root**:

```bash
cd scripts/vss-blueprint-models
export NGC_API_KEY='<your-ngc-api-key>'
./deploy.sh EMBED_YOLO_GPU REASONER_GPU
```

Example — embed + YOLO on GPU 2, reasoner on GPU 3:

```bash
./deploy.sh 2              # all services on GPU 2
./deploy.sh --embedder --yolo 2
./deploy.sh 2 3 --reasoner-port 8001 --embed-port 8002 --yolo-port 8003
```

Default ports (**8001 / 8002 / 8003**) match VSS convention. Point `cosmos_*`, `embedding*`, and `yolo_infer_*` in `vss2-secret` / `backend-secret.yaml` at a host DataEngine and K8s can reach (not always `127.0.0.1`).

## Contents

| Path | Purpose |
|------|---------|
| `deploy.sh` | Deploy script (Reason2, Embed1, YOLO) |
| `DEPLOY.md` | Full usage, options, troubleshooting |
| `yolo-infer/` | YOLO FastAPI service (`main.py`, `Dockerfile`) |

## Documentation

See **[DEPLOY.md](DEPLOY.md)** for prerequisites, GPU layout, environment variables, health checks, and tear down.

The [site deploy](../../deployments/vss-deploy/README.md) puts these hosts in `site.yaml`. Vector dims / local NIM notes: [COSMOS_LOCAL_STACK.md](../../docs/COSMOS_LOCAL_STACK.md).
