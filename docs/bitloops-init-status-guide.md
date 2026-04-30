# `bitloops-init-status` guide

This guide shows how to run `scripts/swebench/bitloops_init_status.py` to inspect
the Bitloops init state for a benchmark task sandbox.

Use it for `with_bitloops` benchmark runs when you want to see:
- the current `bitloops init status` session output for a task
- whether embeddings and summaries have started landing in the task-local stores
- whether background workplane jobs are still queued or already completed

## 1) Prerequisites

From the repo root:

```bash
source .venv/bin/activate
command -v bitloops
```

The script expects benchmark artifacts under `runs/` by default and is most
useful after a `with_bitloops` run has already prepared task workspaces.

## 2) Basic usage

Run it against one task in one benchmark run:

```bash
./.venv/bin/python scripts/swebench/bitloops_init_status.py \
  --run-id 20260427_103024_a73844 \
  --repo tokio-rs/axum \
  --instance-id tokio-rs__axum-1119
```

If the run is using parallel attempts, either pass `--attempt` or let the
script prompt you to choose one:

```bash
./.venv/bin/python scripts/swebench/bitloops_init_status.py \
  --run-id 20260430_144543_c48652 \
  --repo nushell/nushell \
  --instance-id nushell__nushell-13831 \
  --attempt 1
```

Keep each trailing `\` as the final character on its line. If a line ends with
`\ ` instead, `zsh` splits the command and later flags such as `--repo` look
like separate commands.

That prints a snapshot like:
- run metadata: run id, repo, instance id, workspace path
- init session state: overall status plus lane-level progress from `bitloops init status`
- task-local store state: stored embeddings, mailbox counts, summary queue size, and workplane jobs

## 3) How run selection works

The script can find the run in three ways:

1. `--run-root /absolute/or/relative/path/to/run`
2. `--run-id 20260427_103024_a73844`
3. no run flag at all: it searches under `--runs-root` and picks the latest run folder by name

If you keep the default layout, `--runs-root` can be omitted because it already
defaults to `runs`.

Example using the latest run automatically:

```bash
./.venv/bin/python scripts/swebench/bitloops_init_status.py \
  --repo tokio-rs/axum \
  --instance-id tokio-rs__axum-1119
```

Example with an explicit run root:

```bash
./.venv/bin/python scripts/swebench/bitloops_init_status.py \
  --run-root runs/swebench_multilingual/20260427/20260427_103024_a73844 \
  --repo tokio-rs/axum \
  --instance-id tokio-rs__axum-1119
```

## 4) How task selection works

If the selected run contains more than one task, pass both:
- `--repo`
- `--instance-id`

If you do not pass enough filters:
- in an interactive terminal, the script prompts you to choose a repo and task
- in a non-interactive context, it exits and tells you to re-run with `--repo`
  and `--instance-id`

For parallel-attempt runs, the same applies to attempts:
- in an interactive terminal, the script prompts you to choose an attempt
- in a non-interactive context, re-run with `--attempt`

## 5) Watch mode

Use watch mode to refresh the snapshot until you stop it with `Ctrl+C`:

```bash
./.venv/bin/python scripts/swebench/bitloops_init_status.py \
  --run-id 20260427_103024_a73844 \
  --repo tokio-rs/axum \
  --instance-id tokio-rs__axum-1119 \
  --watch
```

Change the polling interval if you want faster or slower refreshes:

```bash
./.venv/bin/python scripts/swebench/bitloops_init_status.py \
  --run-id 20260427_103024_a73844 \
  --repo tokio-rs/axum \
  --instance-id tokio-rs__axum-1119 \
  --watch \
  --interval 1.5
```

## 6) JSON output

Use `--json` when you want the aggregated snapshot as machine-readable output:

```bash
./.venv/bin/python scripts/swebench/bitloops_init_status.py \
  --run-id 20260427_103024_a73844 \
  --repo tokio-rs/axum \
  --instance-id tokio-rs__axum-1119 \
  --json
```

The JSON includes:
- `status_payload`: raw `bitloops init status --json` output from the task sandbox
- `status_error`: any error returned while trying to fetch init status
- `db_snapshot`: counts pulled from the task-local runtime and relational stores

## 7) What the output means

Common text fields:
- `Init Status`: overall session state, for example `Running` or `Completed`
- `Summary`: top-level session summary text
- lane lines such as `Sync:` or `Code Embeddings:`: per-lane progress reported by Bitloops
- `Stored embeddings`: how many embeddings are already persisted by representation kind
- `Embedding mailbox`: queued or completed embedding mailbox items grouped by kind and status
- `Summary mailbox items`: how many summary items are waiting in the summary mailbox
- `Workplane jobs`: grouped job counts from the task-local workplane runtime

If the script says `Init Status: unavailable`, it still tries to show the
database snapshot. That usually means either:
- the task-local Bitloops runtime has not finished coming up yet
- the selected task was not run with Bitloops init enabled
- the `bitloops` command failed inside that sandbox

## 8) Fastest troubleshooting path

If the script cannot find your task, double-check:
- the run id or run root
- the exact repo slug, for example `tokio-rs/axum`
- the exact SWE-bench instance id, for example `tokio-rs__axum-1119`

If you are unsure which run folder to inspect, start with:

```bash
ls -1t runs/swebench_multilingual/$(date +%Y%m%d) | head -n 5
```

Then re-run the script with `--run-id` or `--run-root`.
