#!/usr/bin/env bash
# Validate site.yaml, then deploy one VSS namespace.
#   ./deploy.sh site.yaml              validate and deploy; clean up on failure
#   ./deploy.sh site.yaml --check      validate only
#   ./deploy.sh site.yaml --cleanup    remove the stack; keep the namespace
#   ./deploy.sh site.yaml --no-cleanup deploy, and leave objects in place if it fails
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${ROOT}/deploy-work/venv"

pick_python() {
  local bin
  for bin in python3.12 python3.13 python3.11 python3.10 python3; do
    if command -v "$bin" >/dev/null 2>&1 \
      && "$bin" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
      command -v "$bin"
      return
    fi
  done
  echo "need Python 3.10 or newer" >&2
  exit 1
}

venv_ok() {
  [[ -x "${VENV}/bin/python" ]] \
    && "${VENV}/bin/python" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'
}

if ! venv_ok; then
  rm -rf "${VENV}"
  mkdir -p "${ROOT}/deploy-work"
  "$(pick_python)" -m venv "${VENV}"
fi
if ! "${VENV}/bin/python" -c 'import yaml' >/dev/null 2>&1; then
  "${VENV}/bin/pip" install -q pyyaml
fi
exec "${VENV}/bin/python" -u "${ROOT}/vss_deploy.py" "$@"
