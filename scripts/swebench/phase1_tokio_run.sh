#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
CLI_MODULE="benchkit.swebench.cli"

SPLIT="${SPLIT:-test}"
REPO="${REPO:-tokio-rs/tokio}"
TOKIO_DATASET="${TOKIO_DATASET:-datasets/swebench_multilingual.test.tokio.jsonl}"
TOKIO_TASK_IDS="${TOKIO_TASK_IDS:-configs/swebench/tokio_task_ids.txt}"
CLAUDE_CONFIG="${CLAUDE_CONFIG:-configs/swebench/rust_tokio_phase1_claude.toml}"
CURSOR_CONFIG="${CURSOR_CONFIG:-configs/swebench/rust_tokio_phase1_cursor.toml}"
APPENDIX_DIR="${APPENDIX_DIR:-reports/appendix/phase1_tokio}"

run_cli() {
  PYTHONPATH=src "$PYTHON_BIN" -m "$CLI_MODULE" "$@"
}

require_file() {
  local file_path="$1"
  if [[ ! -f "$file_path" ]]; then
    echo "Required file not found: $file_path" >&2
    exit 1
  fi
}

extract_run_root() {
  local output="$1"
  local run_root
  run_root="$(printf "%s\n" "$output" | awk -F': ' '/^Run root: / {print $2}' | tail -n 1)"
  if [[ -z "$run_root" ]]; then
    echo "Unable to parse run root from command output." >&2
    exit 1
  fi
  printf "%s" "$run_root"
}

echo "[0/7] Preflight checks"
require_file "$TOKIO_TASK_IDS"
require_file "$CLAUDE_CONFIG"
require_file "$CURSOR_CONFIG"

if ! "$PYTHON_BIN" -c "import swebench" >/dev/null 2>&1; then
  echo "Missing Python dependency: swebench. Install with: python3 -m pip install swebench" >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker daemon is not reachable. Start Docker and retry." >&2
  exit 1
fi

echo "[1/7] Exporting Tokio dataset subset from Hugging Face"
run_cli export-hf \
  --split "$SPLIT" \
  --repo "$REPO" \
  --output "$TOKIO_DATASET" \
  --overwrite

echo "[2/7] Validating Claude config/model mapping"
run_cli plan --config "$CLAUDE_CONFIG" --show 3

echo "[3/7] Validating Cursor config/model mapping"
run_cli plan --config "$CURSOR_CONFIG" --show 3

echo "[4/7] Running Claude baseline"
CLAUDE_OUTPUT="$(run_cli run --config "$CLAUDE_CONFIG")"
printf "%s\n" "$CLAUDE_OUTPUT"
CLAUDE_RUN_ROOT="$(extract_run_root "$CLAUDE_OUTPUT")"

echo "[5/7] Running Cursor baseline"
CURSOR_OUTPUT="$(run_cli run --config "$CURSOR_CONFIG")"
printf "%s\n" "$CURSOR_OUTPUT"
CURSOR_RUN_ROOT="$(extract_run_root "$CURSOR_OUTPUT")"

echo "[6/7] Generating appendix files"
APPENDIX_OUTPUT="$(run_cli appendix \
  --run-root "$CLAUDE_RUN_ROOT" \
  --run-root "$CURSOR_RUN_ROOT" \
  --output-dir "$APPENDIX_DIR")"
printf "%s\n" "$APPENDIX_OUTPUT"

echo "[7/7] Done"
echo "Claude run root: $CLAUDE_RUN_ROOT"
echo "Cursor run root: $CURSOR_RUN_ROOT"
echo "Appendix output dir: $APPENDIX_DIR"
