#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GRAPH_TOOL="${ROOT_DIR}/.venv/bin/code-review-graph"

if [[ ! -x "${GRAPH_TOOL}" ]]; then
  echo "code-review-graph is not installed at ${GRAPH_TOOL}; skipping graph refresh" >&2
  exit 0
fi

exec "${GRAPH_TOOL}" build --repo "${ROOT_DIR}"
