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

- Python 3.10 or newer. `./deploy.sh` creates `deploy-work/venv` and installs PyYAML there.
- `kubectl`, with `KUBECONFIG` set or `site.kubeconfig` filled. The shell value wins.
- `vastde` v5.5.0-sp2 on `PATH`, already logged in as `vms.user` against `vms.host` for tenant `vms.tenant`. The tenant, username, and VMS host in `vastde config view` must match `site.yaml`. This deploy path is aligned with DataEngine 5.5.0-sp2. Install and builder selection: [Get vastde and roll function images](#get-vastde-and-roll-function-images).

### Kubernetes and DataEngine

- Namespace `site.namespace` exists and is `Active`.
- That namespace is attached to the DataEngine cluster named `dataengine.cluster`.
- `dataengine.registry` exists in VMS and is linked to that cluster.
- Every image in `site.yaml` is already in that registry. An empty function `tag` means `latest`.

Build and push before the deploy:

```bash
# from the repo root
REGISTRY=your.registry/vss TAG=v1 source-code/scripts/build-retrieval-images.sh
REGISTRY=<registry-host>/<path> TAG=<image-tag> source-code/scripts/build-vastde-functions.sh
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

## Get vastde and roll function images

Aligned with DataEngine **5.5.0-sp2**: CLI `vastde` v5.5.0-sp2 and builder `vastdataorg/vast-builder:v5.5.0-sp2`. Run this on the Linux host that has Docker and the function source (`linux/amd64`). Pulling `vastdataorg/vast-builder` leaves the CLI on whatever builder it already had; `vastde builders set` is what selects it. `<image-tag>` below is the tag you push for the function images. It is separate from the 5.5.0-sp2 CLI and builder versions.

### 1. Put the CLI on `PATH`

```bash
mkdir -p "$HOME/bin"
curl -fsSL -o "$HOME/bin/vastde" \
  https://github.com/vast-data/dataengine-cli/releases/download/v5.5.0-sp2/vastde_linux_amd64
chmod +x "$HOME/bin/vastde"
case ":$PATH:" in
  *":$HOME/bin:"*) ;;
  *) echo 'export PATH="$HOME/bin:$PATH"' >> "$HOME/.bashrc" ;;
esac
export PATH="$HOME/bin:$PATH"
hash -r
vastde version
```

`vastde version` prints `v5.5.0-sp2`. The darwin binary is a different build; function images are built on this Linux host.

### 2. Log in and select the builder

Tenant, user, and VMS host come from `site.yaml` (`vms.tenant`, `vms.user`, `vms.host`). Type the password when asked. Leave it out of the repo.

```bash
vastde config init \
  --tenant <vms.tenant> \
  --username <vms.user> \
  --vms-url https://<vms.host> \
  --builder-image-url vastdataorg/vast-builder:v5.5.0-sp2
vastde config set --password
vastde builders set v5.5.0-sp2
vastde builders inspect
vastde config view
```

`builders inspect` shows `vastdataorg/vast-builder:v5.5.0-sp2`. If `~/.vast/config.toml` already exists, `config init` stops until you pass `--force`, or you set the same fields with `vastde config set`. `vastde config view` must show the same tenant, user, and VMS host as `site.yaml`.

### 3. Build and push a new tag

From the `source-code` directory. `vastde functions build <name>` writes a local image `<name>:latest`. Push that to the artifact in `site.yaml` (`dev/solutions/vss2-*`). In zsh, write `"${name}:latest"`. `"$name:latest"` is parsed as a modifier and the tag disappears.

Use a new tag for every rebuild. Pushing a tag the pipeline already runs does not change the pods.

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

Log in to that registry before `docker push`. A lifecycle exit 51 is the constraints buildpack rejecting `requirements.txt`. Rerun one function with `vastde functions build <name> -v 5` (this failure does not write `build.log`). Pins that resolve on this builder: `cloudevents==1.11.0`, `pydantic==2.10.6`, `pydantic-settings==2.7.1`. Leave `opentelemetry-*` out of `requirements.txt`. The segmenter also needs `setuptools==75.8.2` next to `imageio-ffmpeg==0.4.9`.

Confirm the image you are about to roll:

```bash
docker run --rm --entrypoint python "${REG}/vss2-detector:${TAG}" \
  -c "import pydantic; print(pydantic.VERSION)"
```

That prints `2.10.6`.

`source-code/scripts/build-vastde-functions.sh` runs the same six builds. It reads `REGISTRY` and `TAG` instead of `REG`.

In `site.yaml`, write the tag with a space after the colon (`tag: <image-tag>`). `tag:<image-tag>` with no space is a different key, and the deploy then looks for `:latest`.

### 4. Publish the new revision

`./deploy.sh` skips a function that already exists and redeploys the pipeline on the revision it already has. A new `tag` in `site.yaml` does not roll those pods. Publish a revision, then set the pipeline to that revision and deploy it.

```bash
for name in vss-segmenter vss-detector vss-reasoner vss-embedder vss-vastdb vss-suggester; do
  vastde functions update "$name" --image-tag "$TAG" --publish
done
```

In the pipeline builder, set every function to the revision just published and deploy `vss-pipeline`.

## Flags

| Command | What it does |
|---------|----------------|
| `./deploy.sh site.yaml` | Validate, then create the stack. On failure, remove what this deploy created. |
| `./deploy.sh site.yaml --check` | Validate only. |
| `./deploy.sh site.yaml --cleanup` | Remove the stack named in `site.yaml`. The namespace stays, because it is attached to DataEngine. |
| `./deploy.sh site.yaml --no-cleanup` | Deploy, and leave objects in place if a step fails. |

`--check` and `--cleanup` are not combined. A validator failure does not deploy and does not clean up.

Cleanup empties the chunks and segments buckets from `k8s.host`, which can reach the S3 VIP, using `deploy-work/storage-keys.yaml`. It then deletes every VastDB table and schema in the database bucket through VMS, and only then deletes that view. Keep the keys file until cleanup finishes.

## What the script creates

- Local user `storage.user`, member of `storage.group`, and one access key.
- Buckets `storage.buckets.chunks`, `segments`, and `database`.
- Topic `dataengine.topic` on the broker.
- Triggers, functions, and one pipeline from the `pipeline` section, including the scheduled prompt-suggester.
- DataEngine secret `dataengine.secret`. Function images read `vss2-secret`, so that is the name to use.
- Kubernetes deployments, services, and ingresses for backend, frontend, streaming, and batch sync.
- A TLS secret when `https` is set.

Ingress hosts are `vss-lab.<ingress_domain>`, `vss-backend.<ingress_domain>`, `vss-streamer.<ingress_domain>`, and `vss-batch-sync.<ingress_domain>`. The UI is `vss-lab`. The frontend proxies `/api/` to the backend, so the browser uses the lab host.
