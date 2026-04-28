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
<<<<<<< Updated upstream
CODEX_CONFIG="${CODEX_CONFIG:-configs/swebench/rust_tokio_phase1_codex.toml}"
=======
OPENCODE_CONFIG="${OPENCODE_CONFIG:-configs/swebench/rust_tokio_phase1_opencode.toml}"
>>>>>>> Stashed changes
APPENDIX_DIR="${APPENDIX_DIR:-reports/appendix/phase1_tokio}"
RUN_AGENTS_IN_PARALLEL="${RUN_AGENTS_IN_PARALLEL:-1}"
RUN_MAX_WORKERS="${RUN_MAX_WORKERS:-2}"

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

run_baseline() {
  local config_path="$1"
  local output_file="$2"

  run_cli run --config "$config_path" --max-workers "$RUN_MAX_WORKERS" >"$output_file" 2>&1
}

is_truthy() {
  local value
  value="$(printf "%s" "$1" | tr '[:upper:]' '[:lower:]')"
  case "$value" in
    1|true|yes|on)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

<<<<<<< Updated upstream
echo "[0/8] Preflight checks"
require_file "$TOKIO_TASK_IDS"
require_file "$CLAUDE_CONFIG"
require_file "$CURSOR_CONFIG"
require_file "$CODEX_CONFIG"
=======
echo "[0/9] Preflight checks"
require_file "$TOKIO_TASK_IDS"
require_file "$CLAUDE_CONFIG"
require_file "$CURSOR_CONFIG"
require_file "$OPENCODE_CONFIG"
>>>>>>> Stashed changes

if ! [[ "$RUN_MAX_WORKERS" =~ ^[0-9]+$ ]] || (( RUN_MAX_WORKERS < 1 )); then
  echo "RUN_MAX_WORKERS must be an integer >= 1 (got: $RUN_MAX_WORKERS)" >&2
  exit 1
fi

if ! "$PYTHON_BIN" -c "import swebench" >/dev/null 2>&1; then
  echo "Missing Python dependency: swebench. Install with: python3 -m pip install swebench" >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker daemon is not reachable. Start Docker and retry." >&2
  exit 1
fi

<<<<<<< Updated upstream
if ! command -v codex >/dev/null 2>&1; then
  echo "Missing Codex CLI in PATH. Install/enable codex and retry." >&2
  exit 1
fi

echo "[1/8] Exporting Tokio dataset subset from Hugging Face"
=======
echo "[1/9] Exporting Tokio dataset subset from Hugging Face"
>>>>>>> Stashed changes
run_cli export-hf \
  --split "$SPLIT" \
  --repo "$REPO" \
  --output "$TOKIO_DATASET" \
  --overwrite

<<<<<<< Updated upstream
echo "[2/8] Validating Claude config/model mapping"
run_cli plan --config "$CLAUDE_CONFIG" --show 3

echo "[3/8] Validating Cursor config/model mapping"
run_cli plan --config "$CURSOR_CONFIG" --show 3

echo "[4/8] Validating Codex config/model mapping"
run_cli plan --config "$CODEX_CONFIG" --show 3
=======
echo "[2/9] Validating Claude config/model mapping"
run_cli plan --config "$CLAUDE_CONFIG" --show 3

echo "[3/9] Validating Cursor config/model mapping"
run_cli plan --config "$CURSOR_CONFIG" --show 3

echo "[4/9] Validating OpenCode config/model mapping"
run_cli plan --config "$OPENCODE_CONFIG" --show 3
>>>>>>> Stashed changes

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/benchkit-phase1.XXXXXX")"
cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

CLAUDE_LOG="$TMP_DIR/claude_run.log"
CURSOR_LOG="$TMP_DIR/cursor_run.log"
<<<<<<< Updated upstream
CODEX_LOG="$TMP_DIR/codex_run.log"

echo "[5/8] Running baselines (agents_parallel=$RUN_AGENTS_IN_PARALLEL, run_max_workers=$RUN_MAX_WORKERS)"
=======
OPENCODE_LOG="$TMP_DIR/opencode_run.log"

echo "[5/9] Running baselines (agents_parallel=$RUN_AGENTS_IN_PARALLEL, run_max_workers=$RUN_MAX_WORKERS)"
>>>>>>> Stashed changes
if is_truthy "$RUN_AGENTS_IN_PARALLEL"; then
  run_baseline "$CLAUDE_CONFIG" "$CLAUDE_LOG" &
  CLAUDE_PID=$!
  run_baseline "$CURSOR_CONFIG" "$CURSOR_LOG" &
  CURSOR_PID=$!
<<<<<<< Updated upstream
  run_baseline "$CODEX_CONFIG" "$CODEX_LOG" &
  CODEX_PID=$!

  CLAUDE_STATUS=0
  CURSOR_STATUS=0
  CODEX_STATUS=0
  wait "$CLAUDE_PID" || CLAUDE_STATUS=$?
  wait "$CURSOR_PID" || CURSOR_STATUS=$?
  wait "$CODEX_PID" || CODEX_STATUS=$?

  if (( CLAUDE_STATUS != 0 || CURSOR_STATUS != 0 || CODEX_STATUS != 0 )); then
=======
  run_baseline "$OPENCODE_CONFIG" "$OPENCODE_LOG" &
  OPENCODE_PID=$!

  CLAUDE_STATUS=0
  CURSOR_STATUS=0
  OPENCODE_STATUS=0
  wait "$CLAUDE_PID" || CLAUDE_STATUS=$?
  wait "$CURSOR_PID" || CURSOR_STATUS=$?
  wait "$OPENCODE_PID" || OPENCODE_STATUS=$?

  if (( CLAUDE_STATUS != 0 || CURSOR_STATUS != 0 || OPENCODE_STATUS != 0 )); then
>>>>>>> Stashed changes
    echo "One or more baseline runs failed." >&2
    echo "--- Claude run output ---" >&2
    cat "$CLAUDE_LOG" >&2 || true
    echo "--- Cursor run output ---" >&2
    cat "$CURSOR_LOG" >&2 || true
<<<<<<< Updated upstream
    echo "--- Codex run output ---" >&2
    cat "$CODEX_LOG" >&2 || true
=======
    echo "--- OpenCode run output ---" >&2
    cat "$OPENCODE_LOG" >&2 || true
>>>>>>> Stashed changes
    exit 1
  fi
else
  run_baseline "$CLAUDE_CONFIG" "$CLAUDE_LOG"
  run_baseline "$CURSOR_CONFIG" "$CURSOR_LOG"
<<<<<<< Updated upstream
  run_baseline "$CODEX_CONFIG" "$CODEX_LOG"
fi

echo "[6/8] Collecting baseline outputs"
=======
  run_baseline "$OPENCODE_CONFIG" "$OPENCODE_LOG"
fi

echo "[6/9] Collecting baseline outputs"
>>>>>>> Stashed changes
echo "--- Claude baseline ---"
CLAUDE_OUTPUT="$(cat "$CLAUDE_LOG")"
printf "%s\n" "$CLAUDE_OUTPUT"
CLAUDE_RUN_ROOT="$(extract_run_root "$CLAUDE_OUTPUT")"

echo "--- Cursor baseline ---"
CURSOR_OUTPUT="$(cat "$CURSOR_LOG")"
printf "%s\n" "$CURSOR_OUTPUT"
CURSOR_RUN_ROOT="$(extract_run_root "$CURSOR_OUTPUT")"

<<<<<<< Updated upstream
echo "--- Codex baseline ---"
CODEX_OUTPUT="$(cat "$CODEX_LOG")"
printf "%s\n" "$CODEX_OUTPUT"
CODEX_RUN_ROOT="$(extract_run_root "$CODEX_OUTPUT")"

echo "[7/8] Generating appendix files"
APPENDIX_OUTPUT="$(run_cli appendix \
  --run-root "$CLAUDE_RUN_ROOT" \
  --run-root "$CURSOR_RUN_ROOT" \
  --run-root "$CODEX_RUN_ROOT" \
  --output-dir "$APPENDIX_DIR")"
printf "%s\n" "$APPENDIX_OUTPUT"

echo "[8/8] Done"
echo "Claude run root: $CLAUDE_RUN_ROOT"
echo "Cursor run root: $CURSOR_RUN_ROOT"
echo "Codex run root: $CODEX_RUN_ROOT"
=======
echo "--- OpenCode baseline ---"
OPENCODE_OUTPUT="$(cat "$OPENCODE_LOG")"
printf "%s\n" "$OPENCODE_OUTPUT"
OPENCODE_RUN_ROOT="$(extract_run_root "$OPENCODE_OUTPUT")"

echo "[7/9] Generating appendix files"
APPENDIX_OUTPUT="$(run_cli appendix \
  --run-root "$CLAUDE_RUN_ROOT" \
  --run-root "$CURSOR_RUN_ROOT" \
  --run-root "$OPENCODE_RUN_ROOT" \
  --output-dir "$APPENDIX_DIR")"
printf "%s\n" "$APPENDIX_OUTPUT"

echo "[8/9] Summary"
echo "Claude run root: $CLAUDE_RUN_ROOT"
echo "Cursor run root: $CURSOR_RUN_ROOT"
echo "OpenCode run root: $OPENCODE_RUN_ROOT"
echo "[9/9] Done"
>>>>>>> Stashed changes
echo "Appendix output dir: $APPENDIX_DIR"
