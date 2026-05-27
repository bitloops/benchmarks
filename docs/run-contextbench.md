# Run ContextBench

This flow benchmarks ContextBench Verified tasks with and without Bitloops DevQL.

**Metrics, pipeline design, and appendix column reference:** [contextbench.md](./contextbench.md).

## 1. Install

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[hf]'
python -m pip install tree-sitter==0.20.4
python -m pip install 'tree-sitter-languages==1.10.2; python_version < "3.12"'
python -m pip install pyarrow datasets
```

Clone ContextBench evaluator:

```bash
mkdir -p third_party
git clone https://github.com/EuniAI/ContextBench third_party/ContextBench
```

## 2. Export Dataset

```bash
./.venv/bin/python -m benchkit.contextbench.cli export-hf \
  --benchmark contextbench_verified \
  --dataset Contextbench/ContextBench \
  --dataset-config contextbench_verified \
  --split train \
  --output datasets/contextbench_verified.train.jsonl \
  --overwrite
```

## 3. Pick Config + Mode

Codex:

```bash
CONFIG=configs/contextbench/codex.toml
MODE=baseline
```

OpenCode:

```bash
CONFIG=configs/contextbench/opencode.toml
MODE=with_bitloops
```

The committed OpenCode ContextBench config routes through Ollama and currently resolves `glm-5.1:cloud` to `ollama/glm-5.1:cloud`.

## 4. Plan + Run

```bash
./.venv/bin/python -m benchkit.contextbench.cli plan \
  --config "$CONFIG" \
  --mode "$MODE"

./.venv/bin/python -m benchkit.contextbench.cli run \
  --config "$CONFIG" \
  --mode "$MODE" \
  --appendix
```

By default, runs now use `artifact_retention_policy = "appendix_transcripts"`. This keeps
appendix exports plus copied transcripts under `reports/transcripts/...`, and prunes heavy
workspace/Bitloops artifacts from `runs/...` after appendix generation.

ContextBench evaluation is executed per attempt using:

```text
python -m contextbench.evaluate --gold <dataset> --pred <converted_pred_jsonl> --out <result_jsonl>
```

## 5. Results

Run metadata (retained for traceability):

- `runs/contextbench_verified/...`

Transcript copies (retained by default):

- `reports/transcripts/...`

Appendix + report exports:

- `reports/appendix/...`

Per-task appendix rows include ContextBench core metrics:

- final coverage/precision: file, symbol, span, line
- trajectory AUC/redundancy: file, symbol, span, line
- edit localization: recall, precision
