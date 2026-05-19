# ContextBench metrics

[ContextBench](https://github.com/EuniAI/ContextBench) measures whether an agent **read the right code** while fixing a task. Benchkit also records whether the patch **passed tests** (SWE-Bench harness). Those are separate scores.

How to run: [run-contextbench.md](./run-contextbench.md).

## Gold vs what the agent read

Each task has **gold context**: the files and line ranges a human needed (in the dataset as `gold_ctx`).

**Predicted context** is rebuilt from the agent’s tool log (`Read`, `grep`, `bash` + `sed`, etc.) in `trace.jsonl`.

For each granularity (file, line, byte-span, symbol):

| Term in appendix | Same as | Formula |
|------------------|---------|---------|
| `*_coverage` | Recall | how much of gold the agent touched |
| `*_precision` | Precision | how much of what they read was actually gold |

```
coverage  = overlap / gold_size
precision = overlap / pred_size
```

**Final** columns (`contextbench_final_*`) use the **union of everything read by the end of the run**. That is what papers often report for file / block (span) / line retrieval.

## Trajectory metrics (`contextbench_traj_*`)

**Final** metrics only ask: “Did they eventually read the right stuff?”

**Trajectory** metrics ask: “**When** did they read it, and how wastefully?”

After each retrieval tool call, ContextBench computes **cumulative** coverage (all files/lines read so far vs gold). That produces a curve over steps, e.g. step 1 = 0%, step 2 = 25%, … step 9 = 70%.

| Metric | Plain meaning |
|--------|----------------|
| **AUC** (`traj_auc_*`) | Average of that cumulative coverage across steps. **Higher** = gold found **earlier**, not only in the last few reads. If final line coverage is 70% but AUC is 40%, they stumbled around before finding gold. |
| **Redundancy** (`traj_redundancy_*`) | How much was re-read. High **file** redundancy = opened the same files many times. **Line** redundancy can stay low if each `sed` range is new. |

Example: two agents both end at 70% line coverage. Agent A hits 70% on step 3; Agent B wanders until step 20. A gets a **higher AUC**; B may have **higher redundancy**.

Step-by-step values live in `evaluator_result` → `trajectory.steps` on each appendix row.

## EditLoc (`contextbench_editloc_*`)

**Not** “did they read well.” **Not** solve rate.

EditLoc checks: among **lines removed** in the agent’s patch, how many fall inside **`init_ctx`** (initial context regions from the dataset)?

| Metric | Meaning |
|--------|---------|
| `editloc_recall` / `editloc_precision` | In practice both track the same thing here: fraction of **deletion** lines in the patch that land in `init_ctx` ranges |

**Add-only** patches often score **0** EditLoc (`pred_size: 0` deletions) even when the fix is correct and retrieval was good. Do not read EditLoc as Pass@1.

Use EditLoc when you care whether edits happened **where the benchmark marks the bug context**, not whether the agent explored broadly.

## What counts as “reading”

Benchkit infers reads from tool calls: `Read`, `grep`, and bash patterns like `sed -n '10,50p' file.py`, `cat`, `head`/`tail`, `rg … path`.

Not counted: `WebFetch`, `pytest`, bare `git diff`, etc.

If the agent never uses those tools, context can be inferred from **edit diffs** only (weak signal; often 0% overlap with gold).

## Appendix columns (quick map)

| Prefix | What |
|--------|------|
| `contextbench_final_*` | End-of-run retrieval (file / line / span≈block / symbol) |
| `contextbench_traj_auc_*` | Speed of finding gold over steps |
| `contextbench_traj_redundancy_*` | Repeated reading |
| `contextbench_editloc_*` | Patch deletions vs `init_ctx` |
| `status` / `solve_rate` | Harness pass (Pass@1-style) |

Full JSON: `evaluator_result` on each row in `appendix_minimal_per_task_log.csv`.

## Paper-style Table 3

You can build recall / precision / F1 tables from **final** coverage and precision:

- **Recall** = `*_coverage`
- **Precision** = `*_precision`
- **F1** = `2 × precision × coverage / (precision + coverage)`
- **Block-level** ≈ `span_*`
- **Pass@1** ≈ `solve_rate`

Aggregate **macro means** over many instances per model; single-task runs are not enough for a published table.

## Caveats

- **100% coverage + low precision**: found all gold but read lots of extra code (common with wide `sed` windows).
- **0% retrieval + solved**: edited without overlapping gold reads (or only non-counted tools).
- **EditLoc 0 + solved**: add-only patch or edits outside `init_ctx`.
- **Local pytest failed, still solved**: harness runs in Docker; trace pytest errors are normal.
