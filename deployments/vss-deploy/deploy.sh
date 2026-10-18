#!/usr/bin/env bash
# Validate site.yaml, then deploy one VSS namespace.
#   ./deploy.sh site.yaml              validate and deploy; clean up on failure
#   ./deploy.sh site.yaml --check      validate only
#   ./deploy.sh site.yaml --cleanup    remove the stack; keep the namespace
#   ./deploy.sh site.yaml --no-cleanup deploy, and leave objects in place if it fails
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${ROOT}/deploy-work/venv"
if [[ ! -x "${VENV}/bin/python" ]]; then
  mkdir -p "${ROOT}/deploy-work"
  python3 -m venv "${VENV}"
  "${VENV}/bin/pip" install pyyaml
fi
exec "${VENV}/bin/python" -u "${ROOT}/vss_deploy.py" "$@"
