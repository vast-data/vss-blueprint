#!/usr/bin/env bash
# Deploy VSS GPU services on bare-metal RTX (Docker).
# Location: scripts/vss-blueprint-models/  Docs: README.md
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
YOLO_SRC="${YOLO_INFER_DIR:-$SCRIPT_DIR/yolo-infer}"
YOLO_IMAGE="${YOLO_IMAGE:-yolo-infer:latest}"

REASONER_IMAGE="nvcr.io/nim/nvidia/cosmos3-reasoner:1.7.0"
REASONER_CONTAINER="cosmos3-nano-reasoner"
EMBED_IMAGE="nvcr.io/nim/nvidia/cosmos-embed1:1.1.0"

REASONER_PORT="${REASONER_PORT:-8001}"
EMBED_PORT="${EMBED_PORT:-8002}"
YOLO_PORT="${YOLO_PORT:-8003}"

DEPLOY_ALL=false
DEPLOY_REASONER=false
DEPLOY_EMBEDDER=false
DEPLOY_YOLO=false
BUILD_YOLO=false
EMBED_YOLO_GPU=""
REASONER_GPU=""

usage() {
  cat <<'EOF'
Deploy VSS GPU services on bare-metal RTX (Docker).
Docs: README.md

Usage:
  ./deploy.sh [GPU] [REASONER_GPU] [options]

  No GPU index treats every GPU as free:
    one GPU      → all selected services on that GPU
    two or more  → Embed1+YOLO on the first, Reason3 nano on the second

  GPU           Pin Embed1 + YOLO (and Reason3 nano if REASONER_GPU omitted)
  REASONER_GPU  Pin Cosmos-Reason3 nano

Options:
  --all                 Deploy all (default if no --reasoner/--embedder/--yolo)
  --reasoner            Deploy Reason3 nano only
  --embedder            Deploy Embed1 only
  --yolo                Deploy YOLO only
  --reasoner-port P     Host port for Reason3 nano (default: 8001)
  --embed-port P        Host port for Embed1 (default: 8002)
  --yolo-port P         Host port for YOLO (default: 8003)
  --ngc-api-key K       NGC API key (or set NGC_API_KEY)
  --build-yolo          docker build yolo-infer image before YOLO deploy
  -h, --help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --all) DEPLOY_ALL=true ;;
    --reasoner) DEPLOY_REASONER=true ;;
    --embedder) DEPLOY_EMBEDDER=true ;;
    --yolo) DEPLOY_YOLO=true ;;
    --build-yolo) BUILD_YOLO=true ;;
    --reasoner-port)
      [[ $# -ge 2 ]] || { echo "missing value for --reasoner-port"; exit 1; }
      REASONER_PORT="$2"
      shift
      ;;
    --embed-port)
      [[ $# -ge 2 ]] || { echo "missing value for --embed-port"; exit 1; }
      EMBED_PORT="$2"
      shift
      ;;
    --yolo-port)
      [[ $# -ge 2 ]] || { echo "missing value for --yolo-port"; exit 1; }
      YOLO_PORT="$2"
      shift
      ;;
    --ngc-api-key)
      [[ $# -ge 2 ]] || { echo "missing value for --ngc-api-key"; exit 1; }
      NGC_API_KEY="$2"
      shift
      ;;
    -h|--help) usage; exit 0 ;;
    -*)
      echo "unknown option: $1" >&2
      exit 1
      ;;
    *)
      if [[ "$1" =~ ^[0-9]+$ ]]; then
        if [[ -z "$EMBED_YOLO_GPU" ]]; then EMBED_YOLO_GPU="$1"
        elif [[ -z "$REASONER_GPU" ]]; then REASONER_GPU="$1"
        else echo "extra argument: $1" >&2; exit 1
        fi
      else
        echo "invalid argument: $1" >&2
        exit 1
      fi
      ;;
  esac
  shift
done

if [[ "$DEPLOY_ALL" == true || "$DEPLOY_REASONER$DEPLOY_EMBEDDER$DEPLOY_YOLO" == "falsefalsefalse" ]]; then
  DEPLOY_ALL=true
fi
if [[ "$DEPLOY_ALL" == true ]]; then
  DEPLOY_REASONER=true
  DEPLOY_EMBEDDER=true
  DEPLOY_YOLO=true
fi

if [[ -z "$EMBED_YOLO_GPU" ]]; then
  mapfile -t _gpus < <(nvidia-smi --query-gpu=index --format=csv,noheader | tr -d ' ')
  [[ ${#_gpus[@]} -ge 1 ]] || { echo "error: nvidia-smi reported no GPUs" >&2; exit 1; }
  EMBED_YOLO_GPU="${_gpus[0]}"
  if [[ ${#_gpus[@]} -ge 2 ]]; then
    REASONER_GPU="${_gpus[1]}"
  else
    REASONER_GPU="${_gpus[0]}"
  fi
  echo "Treating ${#_gpus[@]} GPU(s) as free: embed+yolo=$EMBED_YOLO_GPU reasoner=$REASONER_GPU"
elif [[ -z "$REASONER_GPU" ]]; then
  REASONER_GPU="$EMBED_YOLO_GPU"
fi

for p in "$REASONER_PORT" "$EMBED_PORT" "$YOLO_PORT"; do
  [[ "$p" =~ ^[0-9]+$ ]] && (( p >= 1 && p <= 65535 )) || {
    echo "error: invalid port: $p" >&2
    exit 1
  }
done
if [[ "$DEPLOY_EMBEDDER" == true && "$DEPLOY_YOLO" == true && "$EMBED_PORT" == "$YOLO_PORT" ]]; then
  echo "error: embedder and yolo ports must differ" >&2
  exit 1
fi

if [[ -z "${NGC_API_KEY:-}" ]]; then
  if [[ -t 0 ]]; then
    read -rsp "NGC_API_KEY: " NGC_API_KEY
    echo
  else
    echo "error: set NGC_API_KEY or pass --ngc-api-key" >&2
    exit 1
  fi
fi
[[ -n "$NGC_API_KEY" ]] || { echo "error: NGC_API_KEY is empty" >&2; exit 1; }

echo "NGC login (nvcr.io)..."
echo "$NGC_API_KEY" | docker login nvcr.io -u '$oauthtoken' --password-stdin

mkdir -p "$HOME/.cache/nim/cosmos3-nano" "$HOME/.cache/nim/cosmos-embed1"

gpu_run() {
  local gpu="$1"
  shift
  docker run -d --runtime=nvidia --gpus "\"device=${gpu}\"" "$@"
}

deploy_reasoner() {
  echo "==> Reason3 nano on GPU $REASONER_GPU (:$REASONER_PORT)"
  docker rm -f "$REASONER_CONTAINER" 2>/dev/null || true
  gpu_run "$REASONER_GPU" \
    --name "$REASONER_CONTAINER" \
    --restart unless-stopped \
    --ipc=host \
    --shm-size=32g \
    -p "${REASONER_PORT}:8000" \
    -e "NGC_API_KEY=${NGC_API_KEY}" \
    -e NIM_MODEL_SIZE=nano \
    -e NIM_CACHE_PATH=/opt/nim/.cache \
    -v "$HOME/.cache/nim/cosmos3-nano:/opt/nim/.cache" \
    "$REASONER_IMAGE"
}

deploy_embed() {
  echo "==> Embed1 on GPU $EMBED_YOLO_GPU (:$EMBED_PORT)"
  docker rm -f cosmos-embed1 2>/dev/null || true
  gpu_run "$EMBED_YOLO_GPU" \
    --name cosmos-embed1 \
    --restart unless-stopped \
    --shm-size=16g \
    -p "${EMBED_PORT}:8000" \
    -e "NGC_API_KEY=${NGC_API_KEY}" \
    -e NIM_CACHE_PATH=/opt/nim/.cache \
    -v "$HOME/.cache/nim/cosmos-embed1:/opt/nim/.cache" \
    "$EMBED_IMAGE"
}

deploy_yolo() {
  echo "==> YOLO on GPU $EMBED_YOLO_GPU (:$YOLO_PORT)"
  if [[ "$BUILD_YOLO" == true ]]; then
    echo "building $YOLO_IMAGE from $YOLO_SRC (--build-yolo)"
    [[ -f "$YOLO_SRC/main.py" ]] || { echo "error: $YOLO_SRC/main.py not found" >&2; exit 1; }
    docker build -t "$YOLO_IMAGE" "$YOLO_SRC"
  elif ! docker image inspect "$YOLO_IMAGE" &>/dev/null; then
    echo "building $YOLO_IMAGE from $YOLO_SRC (image not found locally)"
    [[ -f "$YOLO_SRC/main.py" ]] || { echo "error: $YOLO_SRC/main.py not found" >&2; exit 1; }
    docker build -t "$YOLO_IMAGE" "$YOLO_SRC"
  fi
  docker rm -f yolo-infer yolo-object 2>/dev/null || true
  gpu_run "$EMBED_YOLO_GPU" \
    --name yolo-infer \
    --restart unless-stopped \
    -p "${YOLO_PORT}:8000" \
    -v "$YOLO_SRC:/app" \
    -w /app \
    -e NVIDIA_VISIBLE_DEVICES="$EMBED_YOLO_GPU" \
    -e NVIDIA_DRIVER_CAPABILITIES=compute,utility,video \
    -e YOLO_MODEL=yolo11s.pt \
    -e YOLO_DEVICE=0 \
    -e YOLO_CONF=0.4 \
    -e YOLO_CONFIG_DIR=/tmp/Ultralytics \
    "$YOLO_IMAGE" \
    uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1 --timeout-keep-alive 120
}

HEALTH_WAIT_SEC="${HEALTH_WAIT_SEC:-600}"

wait_health() {
  local container="$1"
  local label="$2"
  local port="$3"
  local path="$4"
  local max="${5:-$HEALTH_WAIT_SEC}"
  local url="http://127.0.0.1:${port}${path}"
  local elapsed=0
  local interval=10

  while (( elapsed < max )); do
    local st code
    st=$(docker inspect "$container" --format '{{if .State.Restarting}}restarting{{else}}{{ .State.Status }}{{end}}' 2>/dev/null || echo missing)
    case "$st" in
      exited|missing)
        echo "  $label :$port -> failed ($st)"
        return 1
        ;;
    esac
    code=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 3 "$url" 2>/dev/null || true)
    [[ -z "$code" ]] && code=000
    if [[ "$code" == "200" ]]; then
      echo "  $label :$port -> ok"
      return 0
    fi
    if [[ "$st" == "restarting" ]]; then
      printf '  %s :%s -> loading (restarting, %ds)\n' "$label" "$port" "$elapsed"
    else
      printf '  %s :%s -> loading (%ds)\n' "$label" "$port" "$elapsed"
    fi
    sleep "$interval"
    elapsed=$((elapsed + interval))
  done
  echo "  $label :$port -> still loading (waited ${max}s — docker logs -f $container)"
}

echo "Deploy plan: embed+yolo GPU=$EMBED_YOLO_GPU reasoner GPU=$REASONER_GPU"
echo "  ports: reasoner=$REASONER_PORT embed=$EMBED_PORT yolo=$YOLO_PORT"
[[ "$DEPLOY_EMBEDDER" == true || "$DEPLOY_YOLO" == true ]] && echo "  embed+yolo: embedder=$DEPLOY_EMBEDDER yolo=$DEPLOY_YOLO"
[[ "$DEPLOY_REASONER" == true ]] && echo "  reasoner: yes"

if [[ "$DEPLOY_REASONER" == true ]]; then deploy_reasoner; fi
if [[ "$DEPLOY_EMBEDDER" == true ]]; then deploy_embed; fi
if [[ "$DEPLOY_YOLO" == true ]]; then deploy_yolo; fi

echo ""
echo "Done. Status:"
docker ps --format 'table {{.Names}}\t{{.Ports}}\t{{.Status}}' \
  | grep -E 'cosmos3-nano|cosmos-embed1|yolo-infer|NAMES' || true
echo ""
echo "Health (polls until ready or timeout):"
if [[ "$DEPLOY_REASONER" == true ]]; then
  wait_health "$REASONER_CONTAINER" reason3 "$REASONER_PORT" /v1/health/ready 600
fi
if [[ "$DEPLOY_EMBEDDER" == true ]]; then
  wait_health cosmos-embed1 embed1 "$EMBED_PORT" /v1/health/ready 180
fi
if [[ "$DEPLOY_YOLO" == true ]]; then
  wait_health yolo-infer yolo "$YOLO_PORT" /healthz 120
fi
