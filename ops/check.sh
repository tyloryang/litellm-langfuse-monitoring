#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python_bin="${OBSERVABILITY_PYTHON:-$HOME/llm-observability/.venv/bin/python}"
if [[ ! -x "$python_bin" ]]; then
  printf 'Observability Python was not found at %s; set OBSERVABILITY_PYTHON.\n' "$python_bin" >&2
  exit 1
fi
exec "$python_bin" ops/verify_runtime.py "$@"
