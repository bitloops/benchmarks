#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
CLI_MODULE="benchkit.swebench.cli"

BASELINE_CONFIG="${BASELINE_CONFIG:-configs/swebench/rust_tokio_phase1_claude.toml}"
EXPERIMENT_CONFIG="${EXPERIMENT_CONFIG:-configs/swebench/rust_tokio_phase1_claude_with_bitloops.toml}"
APPENDIX_DIR="${APPENDIX_DIR:-reports/appendix/bitloops_ab}"
RUN_MAX_WORKERS="${RUN_MAX_WORKERS:-}"

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

run_condition() {
  local config_path="$1"
  local output_file="$2"

  if [[ -n "$RUN_MAX_WORKERS" ]]; then
    if ! [[ "$RUN_MAX_WORKERS" =~ ^[0-9]+$ ]] || (( RUN_MAX_WORKERS < 1 )); then
      echo "RUN_MAX_WORKERS must be an integer >= 1 (got: $RUN_MAX_WORKERS)" >&2
      exit 1
    fi
    run_cli run --config "$config_path" --max-workers "$RUN_MAX_WORKERS" >"$output_file" 2>&1
    return
  fi

  run_cli run --config "$config_path" >"$output_file" 2>&1
}

echo "[0/4] Preflight checks"
require_file "$BASELINE_CONFIG"
require_file "$EXPERIMENT_CONFIG"

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/benchkit-ab.XXXXXX")"
cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

BASELINE_LOG="$TMP_DIR/baseline.log"
EXPERIMENT_LOG="$TMP_DIR/experiment.log"

echo "[1/4] Running baseline config: $BASELINE_CONFIG"
run_condition "$BASELINE_CONFIG" "$BASELINE_LOG"

echo "[2/4] Running experiment config: $EXPERIMENT_CONFIG"
run_condition "$EXPERIMENT_CONFIG" "$EXPERIMENT_LOG"

echo "[3/4] Collecting run roots"
echo "--- Baseline run output ---"
BASELINE_OUTPUT="$(cat "$BASELINE_LOG")"
printf "%s\n" "$BASELINE_OUTPUT"
BASELINE_RUN_ROOT="$(extract_run_root "$BASELINE_OUTPUT")"

echo "--- Experiment run output ---"
EXPERIMENT_OUTPUT="$(cat "$EXPERIMENT_LOG")"
printf "%s\n" "$EXPERIMENT_OUTPUT"
EXPERIMENT_RUN_ROOT="$(extract_run_root "$EXPERIMENT_OUTPUT")"

echo "[4/4] Generating comparison appendix at: $APPENDIX_DIR"
APPENDIX_OUTPUT="$(
  run_cli appendix \
    --run-root "$BASELINE_RUN_ROOT" \
    --run-root "$EXPERIMENT_RUN_ROOT" \
    --output-dir "$APPENDIX_DIR"
)"
printf "%s\n" "$APPENDIX_OUTPUT"

echo "Done."
echo "Baseline run root:   $BASELINE_RUN_ROOT"
echo "Experiment run root: $EXPERIMENT_RUN_ROOT"
echo "Appendix output dir: $APPENDIX_DIR"
