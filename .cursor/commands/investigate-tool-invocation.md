---
description: Investigate tool breakdown + full invocation log for a report folder and explain where tool outputs were useful vs fallback noise.
---

You are investigating an agent run from a report folder root.

Input argument:
- `$ARGUMENTS` is the report folder root (example: `reports/appendix/20260430_080105_86fb68`).

Tasks:
1. Resolve these files from the provided root:
   - `<root>/appendix_tool_invocation_breakdown.md`
   - `<root>/appendix_tool_invocation_log.jsonl`
2. Read both files fully.
3. Analyze call-by-call flow with emphasis on discovery quality:
   - For each `bitloops devql query` call, summarize whether results were relevant, noisy, empty, or misleading.
   - Compare returned artefacts to the task/topic context.
   - Check what happened immediately after each devql call (especially `sed`, `rg`, or other search/read commands).
   - Decide whether post-devql `sed` usage was:
     - normal follow-up inspection of relevant paths, or
     - compensating fallback because devql output was not useful.
4. Output a concise report with:
   - `Verdict` (overall quality of devql usefulness in this run)
   - `Helpful devql calls`
   - `Not helpful devql calls`
   - `Why sed/rg happened after devql`
   - `Suggested better devql queries` (2-4 concrete replacements tailored to this run)

Rules:
- Be evidence-based and cite call indices from the log.
- Do not modify repository files.
- If `$ARGUMENTS` is empty or files are missing, ask for the correct report root path.
