#!/usr/bin/env bash
# Generate full appendix + run_summary (same as `python -m benchkit.swebench.reports`)
# for a single run root, writing to reports/appendix/<run_id>/ where <run_id> is the
# basename of the run directory (e.g. 20260427_140322_1f39e8).
#
# Usage (from repo root or any cwd):
#   ./scripts/swebench/generate_run_report.sh runs/swebench_multilingual/20260427/20260427_140322_1f39e8
#
# Optional: pass a second argument to override the output directory name (under reports/appendix/).

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

if [[ "${1:-}" == "" || "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  echo "Usage: $0 <run-root> [output-basename]" >&2
  echo "  run-root: path to run folder containing run_manifest.json" >&2
  echo "  output-basename: optional; default is basename(run-root). Report dir: reports/appendix/<basename>/" >&2
  exit 1
fi

RUN_ROOT_INPUT="$1"
if [[ "$RUN_ROOT_INPUT" == /* ]]; then
  RUN_ROOT="$RUN_ROOT_INPUT"
else
  RUN_ROOT="$ROOT_DIR/$RUN_ROOT_INPUT"
fi

if [[ ! -d "$RUN_ROOT" ]]; then
  echo "Run directory not found: $RUN_ROOT" >&2
  exit 1
fi

if [[ ! -f "$RUN_ROOT/run_manifest.json" ]]; then
  echo "Missing run_manifest.json under: $RUN_ROOT" >&2
  exit 1
fi

OUT_BASENAME="${2:-$(basename "$RUN_ROOT")}"
OUT_DIR="$ROOT_DIR/reports/appendix/$OUT_BASENAME"

PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

export PYTHONPATH="${PYTHONPATH:-$ROOT_DIR/src}"

"$PYTHON_BIN" -m benchkit.swebench.reports \
  --run-root "$RUN_ROOT" \
  --output-dir "$OUT_DIR"

echo "Report output: $OUT_DIR"
