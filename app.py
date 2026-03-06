"""
AutoGrader — Streamlit UI wrapper.

Run with:  streamlit run app.py
"""

import os
import tempfile
import threading
from pathlib import Path

import pandas as pd
import streamlit as st

import config
from skills.file_extractor.extractor import extract_and_collect, read_file
from skills.rubric_generator.rubric_agent import generate_rubric
from skills.grader.grader_agent import grade_all
from skills.plagiarism_detector.plagiarism_agent import check_plagiarism, apply_flags
from skills.report_writer.excel_writer import write_results

# ── Page config ─────────────────────────────────────────────────
st.set_page_config(page_title="AutoGrader", page_icon="📝", layout="wide")

# ── Custom CSS ──────────────────────────────────────────────────
st.markdown('<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">', unsafe_allow_html=True)

st.markdown("""<style>
html, body, [class*="st-"], .stMarkdown, .stTextArea textarea,
input, button, select, .stExpander, p, h1, h2, h3, h4, span, div {
    font-family: 'Inter', sans-serif !important;
}
#MainMenu, footer, header {visibility: hidden;}
.ag-header {
    background: #0f172a;
    margin: -6rem -4rem 0 -4rem;
    padding: 2.8rem 4rem 2.2rem 4rem;
}
.ag-header h1 {
    color: #ffffff;
    font-size: 1.9rem;
    font-weight: 800;
    margin: 0;
    letter-spacing: -0.3px;
}
.ag-header .accent-line {
    width: 48px;
    height: 3px;
    background: #3b82f6;
    border-radius: 2px;
    margin-top: 0.65rem;
}
.ag-header p {
    color: #94a3b8;
    font-size: 0.88rem;
    margin: 0.55rem 0 0 0;
    font-weight: 400;
}
.step-section {
    position: relative;
    padding: 2rem 0 0.5rem 0;
}
.step-watermark {
    position: absolute;
    top: 0.2rem;
    left: -0.15rem;
    font-size: 5rem;
    font-weight: 800;
    color: #e2e8f0;
    line-height: 1;
    user-select: none;
    pointer-events: none;
    z-index: 0;
}
.step-content {
    position: relative;
    z-index: 1;
    padding-left: 3.6rem;
}
.step-content h3 {
    font-size: 1.15rem;
    font-weight: 700;
    color: #0f172a;
    margin: 0.6rem 0 0.15rem 0;
}
.step-content .step-desc {
    font-size: 0.82rem;
    color: #64748b;
    margin: 0 0 1rem 0;
}
.step-divider {
    border: none;
    border-top: 1px solid #e2e8f0;
    margin: 1.5rem 0 0 0;
}
[data-testid="stFileUploader"] section {
    background: #ffffff;
    border: 2px dashed #cbd5e1;
    border-radius: 12px;
    transition: border-color 0.2s;
}
[data-testid="stFileUploader"] section:hover {
    border-color: #3b82f6;
}
.status-msg {
    padding: 0.6rem 0.9rem;
    border-radius: 6px;
    font-size: 0.85rem;
    font-weight: 500;
    margin: 0.5rem 0;
    background: transparent;
}
.status-info    { border-left: 3px solid #3b82f6; color: #334155; }
.status-success { border-left: 3px solid #22c55e; color: #166534; }
.status-warn    { border-left: 3px solid #f59e0b; color: #92400e; }
.status-error   { border-left: 3px solid #ef4444; color: #991b1b; }
.stButton > button {
    background: #3b82f6 !important;
    color: #ffffff !important;
    border: none !important;
    border-radius: 8px;
    font-weight: 600;
    font-size: 0.85rem;
    padding: 0.5rem 1.4rem;
    transition: background 0.15s, box-shadow 0.15s;
}
.stButton > button:hover {
    background: #2563eb !important;
    box-shadow: 0 2px 8px rgba(59,130,246,0.3);
}
.stButton > button:active {
    background: #1d4ed8 !important;
}
div[data-testid="stHorizontalBlock"] > div:nth-child(1) .stButton > button[kind="secondary"] {
    background: #22c55e !important;
}
div[data-testid="stHorizontalBlock"] > div:nth-child(1) .stButton > button[kind="secondary"]:hover {
    background: #16a34a !important;
    box-shadow: 0 2px 8px rgba(34,197,94,0.3);
}
div[data-testid="stHorizontalBlock"] > div:nth-child(2) .stButton > button[kind="secondary"] {
    background: #f59e0b !important;
}
div[data-testid="stHorizontalBlock"] > div:nth-child(2) .stButton > button[kind="secondary"]:hover {
    background: #d97706 !important;
    box-shadow: 0 2px 8px rgba(245,158,11,0.3);
}
.stDownloadButton > button {
    background: #0f172a !important;
    color: #ffffff !important;
    border: none !important;
    border-radius: 8px;
    font-weight: 600;
    padding: 0.55rem 1.6rem;
}
.stDownloadButton > button:hover {
    background: #1e293b !important;
    box-shadow: 0 2px 8px rgba(15,23,42,0.25);
}
.m-card {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 10px;
    padding: 1.15rem 1rem;
    text-align: center;
    box-shadow: 0 1px 3px rgba(0,0,0,0.04);
}
.m-card .m-val {
    font-size: 1.65rem;
    font-weight: 800;
    color: #0f172a;
    margin: 0;
}
.m-card .m-lbl {
    font-size: 0.7rem;
    font-weight: 600;
    color: #64748b;
    text-transform: uppercase;
    letter-spacing: 0.6px;
    margin: 0.3rem 0 0 0;
}
[data-testid="stDataFrame"] {
    border: 1px solid #e2e8f0;
    border-radius: 10px;
    overflow: hidden;
}
.stProgress > div > div > div {
    background: #3b82f6;
    border-radius: 6px;
}
.stTextArea textarea {
    border: 1px solid #cbd5e1;
    border-radius: 8px;
}
.stTextArea textarea:focus {
    border-color: #3b82f6;
    box-shadow: 0 0 0 2px rgba(59,130,246,0.15);
}
.streamlit-expanderHeader {
    font-weight: 600;
    color: #334155;
}
.reset-btn .stButton > button {
    background: transparent !important;
    color: #64748b !important;
    border: 1px solid #cbd5e1 !important;
}
.reset-btn .stButton > button:hover {
    background: #f8fafc !important;
    color: #334155 !important;
    border-color: #94a3b8 !important;
    box-shadow: none;
}
</style>""", unsafe_allow_html=True)


# ── Header ──────────────────────────────────────────────────────
st.markdown("""
<div class="ag-header">
    <h1>AutoGrader</h1>
    <div class="accent-line"></div>
    <p>AI-powered Assignment Grading — upload, review, grade, download.</p>
</div>
""", unsafe_allow_html=True)
st.write("")


# ── Helpers ─────────────────────────────────────────────────────
def _step(number: int, title: str, subtitle: str):
    st.markdown(f"""
    <div class="step-section">
        <span class="step-watermark">{number}</span>
        <div class="step-content">
            <h3>{title}</h3>
            <p class="step-desc">{subtitle}</p>
        </div>
    </div>
    """, unsafe_allow_html=True)


def _status(text: str, kind: str = "info"):
    st.markdown(f'<div class="status-msg status-{kind}">{text}</div>', unsafe_allow_html=True)


def _metric(value, label):
    return f"""
    <div class="m-card">
        <p class="m-val">{value}</p>
        <p class="m-lbl">{label}</p>
    </div>
    """


def _divider():
    st.markdown('<hr class="step-divider">', unsafe_allow_html=True)


# ── Session state defaults ──────────────────────────────────────
_DEFAULTS = {
    "brief_text": "",
    "rubric": "",
    "rubric_approved": False,
    "results": None,
    "report_bytes": None,
}
for _k, _v in _DEFAULTS.items():
    st.session_state.setdefault(_k, _v)

# ── Guard: API key ──────────────────────────────────────────────
if not config.GROQ_API_KEY:
    _status("<strong>GROQ_API_KEY</strong> is not set. Add it to your <code>.env</code> file and restart.", "error")
    st.stop()


def _save_temp(uploaded_file, suffix: str) -> str:
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(uploaded_file.getvalue())
        return tmp.name


def _generate_rubric_from_brief():
    if not st.session_state.brief_text:
        suffix = Path(brief_file.name).suffix
        tmp_path = _save_temp(brief_file, suffix)
        try:
            st.session_state.brief_text = read_file(tmp_path)
        finally:
            os.unlink(tmp_path)

    with st.spinner("Generating rubric…"):
        st.session_state.rubric = generate_rubric(st.session_state.brief_text)
    st.session_state.rubric_approved = False


# ── Step 1 — Upload files ──────────────────────────────────────
_step(1, "Upload Files", "Drag and drop your submissions archive and the assignment brief.")
col1, col2 = st.columns(2)
with col1:
    zip_file = st.file_uploader("Submissions ZIP", type=["zip"])
with col2:
    brief_file = st.file_uploader("Assignment Brief", type=["pdf", "docx"])

_divider()

# ── Step 2 — Rubric ────────────────────────────────────────────
_step(2, "Grading Rubric", "Generate a rubric from the brief, then review and approve it.")

if zip_file and brief_file:
    if not st.session_state.rubric:
        if st.button("Generate Rubric"):
            _generate_rubric_from_brief()
            st.rerun()

    if st.session_state.rubric and not st.session_state.rubric_approved:
        edited = st.text_area(
            "Review and edit the rubric:",
            value=st.session_state.rubric,
            height=400,
        )
        c1, c2, _ = st.columns([1, 1, 2])
        with c1:
            if st.button("Approve", use_container_width=True):
                st.session_state.rubric = edited
                st.session_state.rubric_approved = True
                st.rerun()
        with c2:
            if st.button("Regenerate", use_container_width=True):
                _generate_rubric_from_brief()
                st.rerun()

    if st.session_state.rubric_approved:
        _status("Rubric approved and locked.", "success")
        with st.expander("View approved rubric"):
            st.markdown(st.session_state.rubric)
else:
    _status("Upload both files above to get started.", "info")

_divider()

# ── Step 3 — Grade ─────────────────────────────────────────────
_step(3, "Grade Submissions", "Each submission is graded against the rubric, then checked for plagiarism.")

if st.session_state.rubric_approved and zip_file and st.session_state.results is None:
    if st.button("Start Grading"):
        tmp_zip = _save_temp(zip_file, ".zip")
        try:
            with st.spinner("Extracting submissions…"):
                submissions = extract_and_collect(tmp_zip)

            if not submissions:
                _status("No supported files found in the ZIP archive.", "error")
                st.stop()

            _status(f"Found <strong>{len(submissions)}</strong> submission(s). Grading…", "info")

            progress = st.progress(0, text="Grading submissions…")
            lock = threading.Lock()
            done = {"n": 0}
            total = len(submissions)

            def _on_complete(filename: str, _result: dict):
                with lock:
                    done["n"] += 1
                    progress.progress(
                        done["n"] / total,
                        text=f"Graded {done['n']}/{total} — {filename}",
                    )

            results = grade_all(
                st.session_state.rubric,
                submissions,
                on_complete=_on_complete,
            )
            progress.progress(1.0, text="Grading complete.")

            with st.spinner("Running plagiarism check…"):
                flags = check_plagiarism(submissions)
                results = apply_flags(results, flags)

            with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp_xl:
                report_path = tmp_xl.name
            write_results(results, report_path)

            st.session_state.results = results
            st.session_state.report_bytes = Path(report_path).read_bytes()
            os.unlink(report_path)
            st.rerun()
        finally:
            os.unlink(tmp_zip)

elif not st.session_state.rubric_approved:
    _status("Approve the rubric first to start grading.", "info")

# ── Step 4 — Results ───────────────────────────────────────────
if st.session_state.results is not None:
    _divider()
    _step(4, "Results", "Summary metrics and the full grading table.")
    results = st.session_state.results

    # Metrics row
    numeric = [r["marks"] for r in results if isinstance(r.get("marks"), (int, float))]
    if numeric:
        flagged = sum(1 for r in results if r.get("plagiarism_flag"))
        passed = sum(1 for m in numeric if m >= config.PASS_THRESHOLD)
        avg = sum(numeric) / len(numeric)
        m1, m2, m3, m4 = st.columns(4)
        with m1:
            st.markdown(_metric(len(results), "Students"), unsafe_allow_html=True)
        with m2:
            st.markdown(_metric(f"{avg:.1f}", "Average"), unsafe_allow_html=True)
        with m3:
            st.markdown(_metric(f"{passed / len(numeric) * 100:.0f}%", "Pass Rate"), unsafe_allow_html=True)
        with m4:
            st.markdown(_metric(flagged, "Plagiarism Flags"), unsafe_allow_html=True)

    st.write("")

    # Build display table
    df = pd.DataFrame(results)
    cols = ["name", "id", "marks"]
    all_cats = sorted({c for r in results for c in r.get("category_scores", {})})
    for i, cat in enumerate(all_cats):
        df[cat] = df["category_scores"].apply(
            lambda x, c=cat: x.get(c, "") if isinstance(x, dict) else ""
        )
        cols.insert(3 + i, cat)
    cols += ["deductions", "feedback", "plagiarism_flag"]
    st.dataframe(df[[c for c in cols if c in df.columns]], use_container_width=True)

    st.write("")
    st.download_button(
        "Download Excel Report",
        data=st.session_state.report_bytes,
        file_name=config.OUTPUT_FILENAME,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

# ── Reset ───────────────────────────────────────────────────────
if st.session_state.rubric or st.session_state.results is not None:
    _divider()
    st.markdown('<div class="reset-btn">', unsafe_allow_html=True)
    if st.button("Start Over", use_container_width=True):
        for key, val in _DEFAULTS.items():
            st.session_state[key] = val
        st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)
