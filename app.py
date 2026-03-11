"""
Dataset viewer: browse bug reports and patches (JSONL/JSON) with a GitHub-style diff viewer.
Run: streamlit run app.py
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

DEFAULT_DATASET = Path(__file__).resolve().parent / "datasets" / "swebench_multilingual.test.tokio.jsonl"
DIFF2HTML_CDN_CSS = "https://cdn.jsdelivr.net/npm/diff2html/bundles/css/diff2html.min.css"
DIFF2HTML_CDN_JS = "https://cdn.jsdelivr.net/npm/diff2html/bundles/js/diff2html.min.js"


@st.cache_data
def load_dataset(path: str) -> pd.DataFrame:
    """Load dataset from JSONL or JSON file; return a DataFrame for browsing."""
    p = Path(path).resolve()
    if not p.exists():
        raise FileNotFoundError(f"Dataset not found: {p}")
    if p.suffix == ".jsonl":
        rows = []
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    else:
        data = json.loads(p.read_text(encoding="utf-8"))
        rows = data if isinstance(data, list) else data.get("data", data)
        if not isinstance(rows, list):
            rows = [data]
    return pd.DataFrame(rows)


def _diff_html(diff_str: str, height: int = 400) -> str:
    """Render a unified diff string as HTML using diff2html (side-by-side, syntax highlight)."""
    if not (diff_str or diff_str.strip()):
        return "<p><em>No patch content.</em></p>"
    escaped = json.dumps(diff_str)  # safe for embedding in JS
    return f"""
<!DOCTYPE html>
<html>
<head>
  <link rel="stylesheet" href="{DIFF2HTML_CDN_CSS}">
</head>
<body>
  <div id="diff-output"></div>
  <script src="{DIFF2HTML_CDN_JS}"></script>
  <script>
    const diff = {escaped};
    const html = Diff2Html.getPrettyHtml(diff, {{
      inputFormat: "diff",
      outputFormat: "side-by-side",
      showFiles: true,
      matching: "lines",
      drawFileList: true,
      synchronisedScroll: true,
      highlightCode: true
    }});
    document.getElementById("diff-output").innerHTML = html;
  </script>
</body>
</html>
"""


def render_metadata(record: dict) -> None:
    """Display record metadata (repo, instance_id, base_commit, created_at, language)."""
    cols = st.columns([1, 1, 2])
    with cols[0]:
        st.metric("Repo", record.get("repo", "—"))
        st.metric("Instance ID", record.get("instance_id", "—"))
    with cols[1]:
        st.metric("Language", record.get("language", "—"))
        st.metric("Created", record.get("created_at", "—"))
    with cols[2]:
        commit = record.get("base_commit", "") or "—"
        st.text_input("Base commit", commit, disabled=True, label_visibility="collapsed")


def render_patch_diff(patch: str, title: str = "Patch", height: int = 420) -> None:
    """Render a patch string with diff2html in an embedded iframe."""
    if title:
        st.subheader(title)
    html = _diff_html(patch or "", height=height)
    st.components.v1.html(html, height=height, scrolling=True)


def render_test_patch(patch: str, height: int = 400) -> None:
    """Render the test patch with diff2html."""
    render_patch_diff(patch or "", title="Test patch", height=height)


def main() -> None:
    st.set_page_config(page_title="Dataset viewer", layout="wide")
    st.title("Dataset viewer")

    path = st.sidebar.text_input(
        "Dataset path",
        value=str(DEFAULT_DATASET),
        help="JSONL or JSON file path",
    )
    try:
        df = load_dataset(path)
    except Exception as e:
        st.error(f"Failed to load dataset: {e}")
        return

    repos = sorted(df["repo"].dropna().unique().tolist()) if "repo" in df.columns else []
    repo_filter = st.sidebar.selectbox("Filter by repo", ["All"] + repos)
    search = st.sidebar.text_input("Search problem statement", "").strip().lower()

    subset = df
    if repo_filter != "All":
        subset = subset[subset["repo"] == repo_filter]
    if search and "problem_statement" in subset.columns:
        subset = subset[
            subset["problem_statement"].fillna("").str.lower().str.contains(search, regex=False)
        ]
    subset = subset.reset_index(drop=True)
    n = len(subset)
    if n == 0:
        st.warning("No records match the filters.")
        return

    idx_label = f"Record (1–{n})"
    if "viewer_idx" not in st.session_state:
        st.session_state["viewer_idx"] = 0
    jump_id = st.sidebar.text_input("Jump to instance_id", "").strip()
    if jump_id and "instance_id" in subset.columns:
        mask = subset["instance_id"].astype(str) == jump_id
        if mask.any():
            st.session_state["viewer_idx"] = int(subset.index[mask][0])
    idx = st.sidebar.number_input(
        idx_label, min_value=0, max_value=max(0, n - 1), value=st.session_state["viewer_idx"], step=1, key="idx_input"
    )
    st.session_state["viewer_idx"] = idx
    col_prev, _, col_next = st.sidebar.columns([1, 1, 1])
    with col_prev:
        if st.button("Previous") and idx > 0:
            st.session_state["viewer_idx"] = idx - 1
            st.rerun()
    with col_next:
        if st.button("Next") and idx < n - 1:
            st.session_state["viewer_idx"] = idx + 1
            st.rerun()

    row = subset.iloc[idx].to_dict()
    for k, v in row.items():
        if isinstance(v, float) and pd.isna(v):
            row[k] = None

    # --- Main panel ---
    render_metadata(row)

    st.subheader("Problem statement")
    st.markdown((row.get("problem_statement") or "").replace("\r\n", "\n") or "*No problem statement.*")

    if row.get("hints_text"):
        with st.expander("Hints", expanded=False):
            st.markdown(row["hints_text"].replace("\r\n", "\n"))

    fail_to_pass = row.get("FAIL_TO_PASS")
    pass_to_pass = row.get("PASS_TO_PASS")
    if isinstance(fail_to_pass, list) or isinstance(pass_to_pass, list):
        with st.expander("Tests", expanded=False):
            if fail_to_pass:
                st.write("**Failing → passing (FAIL_TO_PASS):**", ", ".join(fail_to_pass))
            if pass_to_pass:
                st.write("**Passing (PASS_TO_PASS):**", ", ".join(pass_to_pass[:20]), "..." if len(pass_to_pass) > 20 else "")

    with st.expander("Patch", expanded=True):
        render_patch_diff(row.get("patch") or "", title="", height=480)
    if row.get("test_patch"):
        with st.expander("Test patch", expanded=True):
            render_test_patch(row.get("test_patch"), height=400)

    if st.button("Show patch as text (for copying)") and row.get("patch"):
        st.code(row["patch"], language="diff")


if __name__ == "__main__":
    main()
