# Deploy VSS

One site file creates the storage user, buckets, topic, one DataEngine pipeline (ingest and prompt-suggester), and the Kubernetes app.

```bash
cd deployments/vss-deploy
cp site.yaml.example site.yaml
# fill site.yaml
./deploy.sh site.yaml --check
./deploy.sh site.yaml
```

`site.yaml` is gitignored. A successful deploy prints the storage user's access key and secret key when the key is created, and again after `deploy finished`. The same values are written to `deploy-work/storage-keys.yaml` (mode 600). Keep that file while the user exists. If the user already has a key and the file is missing, the script refuses to mint another one.

## Requirements

These must already be true before `./deploy.sh`. `--check` probes them and does not create anything. A failed check exits without deploying.

### On the machine that runs the script

- `python3`
- `kubectl`, with `KUBECONFIG` set or `site.kubeconfig` filled. The shell value wins.
- `vastde` on `PATH`, already logged in as `vms.user` against `vms.host` for tenant `vms.tenant`. The tenant, username, and VMS host in `vastde config view` must match `site.yaml`.

### Kubernetes and DataEngine

- Namespace `site.namespace` exists and is `Active`.
- That namespace is attached to the DataEngine cluster named `dataengine.cluster`.
- `dataengine.registry` exists in VMS and is linked to that cluster.
- Every image in `site.yaml` is already in that registry. An empty function `tag` means `latest`.

Build and push before the deploy:

```bash
# from the repo root
REGISTRY=your.registry/vss TAG=v1 source-code/scripts/build-retrieval-images.sh
REGISTRY=your.registry/vss TAG=v1 source-code/scripts/build-vastde-functions.sh
```

Image names in `site.yaml` must be the names you pushed. See [scripts](../../source-code/scripts/README.md).

### VAST objects the script does not create

- Broker view `dataengine.broker`, with the Kafka protocol.
- View policy `vms.view_policy`.
- Local group `storage.group`. The new user is attached to this group.

### Models and connectivity

Cosmos-Reason2, Cosmos-Embed1, and YOLO11 must be running. GPU host install: [vss-blueprint-models](../../scripts/vss-blueprint-models/README.md).

VIP pools are probed from `k8s.host`, not from the machine running the script. That host needs SSH as `k8s.user`, and from there:

| Check | Request |
|-------|---------|
| S3 | `storage.s3_endpoint` returns any HTTP response |
| Ingest VastDB | `storage.ingest_vdb_endpoint` returns any HTTP response |
| Backend VastDB | `storage.backend_vdb_endpoint` returns any HTTP response |
| Cosmos-Reason2 | `GET http://<cosmos host>:<port>/v1/models` returns 200 |
| Cosmos-Embed1 | `GET http://<embed host>:<port>/v1/models` returns 200 |
| YOLO11 | `GET http://<yolo host>:<port>/healthz` returns 200 |

Each model accepts an optional `authorization` Bearer token (`runtime.cosmos`, `runtime.embed`, `runtime.yolo`). Leave it empty for a local NIM. When set, ingest, the backend, and the model probe send it as `Authorization`. An empty value is omitted from the DataEngine secret.

`runtime.embed.nvidia_api_key` is required when `local_nim` is false. A local NIM skips it. `storage.output_bucket_suffix` is what the segmenter appends to the chunks bucket; `{chunks}{suffix}` must equal `storage.buckets.segments`.

A missing response fails the check. A non-200 on a model URL is a warning.

### HTTPS

Leave `https.cert` and `https.key` empty for HTTP. Set both to serve TLS. The secret name defaults to `vss-tls`. One of the two paths is a failure.

## Flags

| Command | What it does |
|---------|----------------|
| `./deploy.sh site.yaml` | Validate, then create the stack. On failure, remove what this deploy created. |
| `./deploy.sh site.yaml --check` | Validate only. |
| `./deploy.sh site.yaml --cleanup` | Remove the stack named in `site.yaml`. The namespace stays, because it is attached to DataEngine. |
| `./deploy.sh site.yaml --no-cleanup` | Deploy, and leave objects in place if a step fails. |

`--check` and `--cleanup` are not combined. A validator failure does not deploy and does not clean up.

## What the script creates

- Local user `storage.user`, member of `storage.group`, and one access key.
- Buckets `storage.buckets.chunks`, `segments`, and `database`.
- Topic `dataengine.topic` on the broker.
- Triggers, functions, and one pipeline from the `pipeline` section, including the scheduled prompt-suggester.
- DataEngine secret `dataengine.secret`. Function images read `vss2-secret`, so that is the name to use.
- Kubernetes deployments, services, and ingresses for backend, frontend, streaming, and batch sync.
- A TLS secret when `https` is set.

Ingress hosts are `vss-lab.<ingress_domain>`, `vss-backend.<ingress_domain>`, `vss-streamer.<ingress_domain>`, and `vss-batch-sync.<ingress_domain>`. The UI is `vss-lab`. The frontend proxies `/api/` to the backend, so the browser uses the lab host.
