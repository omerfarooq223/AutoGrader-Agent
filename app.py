"""
AutoGrader — Streamlit UI wrapper.

Run with:  streamlit run app.py
"""

import json
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
from skills.report_writer.excel_writer import write_results, shorten_plagiarism_flag

# ── Page config (must be first Streamlit call) ──────────────────
st.set_page_config(page_title="AutoGrader", page_icon="📝", layout="wide")

# ── Session state defaults ──────────────────────────────────────
_DEFAULTS = {
    "rubric_approved": False,
    "answer_key_approved": False,
    "answer_key_final": None,
    "answer_key_file": None,
    "answer_key_uploaded_filename": None,
    "rubric": None,
    "class_insights": None,
    "results": None,
    "report_bytes": None,
    "brief_text": None,
    "rubric_manual_text": "",
    "rubric_refined": False,
    "rubric_refined_text": "",
    "rubric_refine_view": False,
    "answer_key_manual_text": "",
    "answer_key_auto_text": "",
    "answer_key_mode": "manual",
    "rubric_mode": "manual",
    "grading_in_progress": False,
    "rubric_key_v": 0,
}

for k, v in _DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ── Custom CSS ──────────────────────────────────────────────────
st.markdown(
    '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">',
    unsafe_allow_html=True,
)

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
[data-testid="stFileUploader"] section:hover { border-color: #3b82f6; }
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
.stButton > button:active { background: #1d4ed8 !important; }
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
.streamlit-expanderHeader { font-weight: 600; color: #334155; }
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

/* ── Stepper ────────────────────────────────────────────────── */
.ag-stepper {
    display: flex;
    gap: 12px;
    margin: 1rem 0 1.2rem 0;
    flex-wrap: wrap;
}
.ag-step {
    flex: 1;
    min-width: 160px;
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    padding: 0.65rem 0.85rem;
    position: relative;
    box-shadow: 0 1px 3px rgba(0,0,0,0.03);
}
.ag-step .k {
    font-size: 0.7rem;
    color: #64748b;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.35px;
}
.ag-step .v {
    font-size: 0.95rem;
    font-weight: 800;
    color: #0f172a;
    margin-top: 0.25rem;
}
.ag-step .pill {
    position: absolute;
    top: 10px;
    right: 12px;
    font-size: 0.7rem;
    padding: 0.18rem 0.55rem;
    border-radius: 999px;
    font-weight: 800;
}
.ag-step.done { border-color: #22c55e; }
.ag-step.done .pill { background: #dcfce7; color: #166534; }
.ag-step.active { border-color: #3b82f6; }
.ag-step.active .pill { background: #dbeafe; color: #1d4ed8; }
.ag-step.todo .pill { background: #f1f5f9; color: #475569; }
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
    st.markdown(
        f'<div class="status-msg status-{kind}">{text}</div>',
        unsafe_allow_html=True,
    )


def _metric(value, label):
    return f"""
    <div class="m-card">
        <p class="m-val">{value}</p>
        <p class="m-lbl">{label}</p>
    </div>
    """


def _divider():
    st.markdown('<hr class="step-divider">', unsafe_allow_html=True)


def _save_temp(uploaded_file, suffix: str) -> str:
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(uploaded_file.getvalue())
        return tmp.name


# ── Guard: API key ──────────────────────────────────────────────
if not config.GROQ_API_KEY:
    _status(
        "<strong>GROQ_API_KEY</strong> is not set. Add it to your <code>.env</code> file and restart.",
        "error",
    )
    st.stop()

if config.EXTRACT_IMAGES:
    _status(
        "Image extraction is enabled (<code>EXTRACT_IMAGES=True</code>). "
        "This can consume Gemini quota quickly. Disable it unless your files contain important diagrams/screenshots.",
        "warn",
    )


# Variables initialization from session state for early UI components
zip_file = st.session_state.get("zip_uploader")
brief_file = st.session_state.get("brief_uploader")

# ── Stepper (Top Progress Bar) ──────────────────────────────────
upload_done = bool(zip_file and brief_file)
active_step = (
    5
    if st.session_state.results is not None
    else 4
    if upload_done and st.session_state.rubric_approved and st.session_state.answer_key_approved and st.session_state.results is None
    else 3
    if upload_done and st.session_state.rubric_approved
    else 2
    if upload_done
    else 1
)
steps = [
    (1, "Upload files", upload_done),
    (2, "Rubric", st.session_state.rubric_approved),
    (3, "Answer key", st.session_state.answer_key_approved),
    (4, "Grade", st.session_state.results is not None),
    (5, "Results", st.session_state.results is not None),
]
stepper_html = '<div class="ag-stepper">'
for idx, title, done in steps:
    status = "done" if done else "active" if idx == active_step else "todo"
    pill = "Done" if done else "In progress" if idx == active_step else "To do"
    stepper_html += (
        f'<div class="ag-step {status}">'
        f'<div class="k">Step {idx}</div>'
        f'<div class="v">{title}</div>'
        f'<div class="pill">{pill}</div>'
        f'</div>'
    )
stepper_html += "</div>"
st.markdown(stepper_html, unsafe_allow_html=True)
_divider()

# ── Step 1 — Upload Files ───────────────────────────────────────
_step(1, "Upload Files", "Upload your submissions ZIP and assignment brief.")
col1, col2 = st.columns(2)
with col1:
    zip_file_new = st.file_uploader("Submissions ZIP", type=["zip"], key="zip_uploader")
with col2:
    brief_file_new = st.file_uploader("Assignment Brief", type=["pdf", "docx"], key="brief_uploader")

# Sync file references
if zip_file_new: zip_file = zip_file_new
if brief_file_new: brief_file = brief_file_new
upload_done = bool(zip_file and brief_file)

_divider()

# ── Step 2 — Grading Rubric ─────────────────────────────────────
_divider()
_step(2, "Grading Rubric", "Choose how to provide the grading rubric.")

if zip_file and brief_file:
    if not st.session_state.rubric_approved:
        rubric_mode = st.radio(
            "How would you like to provide the rubric?",
            ["Provide manually", "Generate automatically"],
            index=0,
            format_func=lambda x: (
                "Provide manually  (Recommended — produces more consistent results)"
                if x == "Provide manually" else x
            ),
            key="rubric_mode_radio",
        )
        st.session_state.rubric_mode = "manual" if rubric_mode == "Provide manually" else "auto"

        if st.session_state.rubric_mode == "manual":
            st.markdown(
                "<span style='color:#64748b; font-size:0.85em;'>"
                "Manual rubric is recommended for technical, math, and diagram-based assignments"
                "</span>",
                unsafe_allow_html=True,
            )
            st.session_state.rubric_manual_text = st.text_area(
                "Paste your rubric (any format):",
                value=st.session_state.rubric_manual_text,
                height=220,
                key=f"rubric_manual_textarea_{st.session_state.rubric_key_v}",
            )

            if st.session_state.rubric_manual_text.strip():
                # Formatting
                if not st.session_state.rubric_refined:
                    if st.button("Format as JSON / Table", help="Converts your raw text into a structured table without changing the wording."):
                        from skills.rubric_generator.rubric_agent import format_rubric_to_json
                        with st.spinner("Formatting rubric…"):
                            st.session_state.rubric_refined_text = format_rubric_to_json(
                                st.session_state.rubric_manual_text
                            )
                        st.session_state.rubric_refined = True
                        st.session_state.rubric_refine_view = True
                        st.rerun()

                if st.session_state.rubric_refined and st.session_state.rubric_refine_view:
                    st.markdown("#### Compare Rubric Versions")
                    c1, c2 = st.columns(2)
                    with c1:
                        st.markdown("**Your original**")
                        st.code(st.session_state.rubric_manual_text, language="text")
                    with c2:
                        st.markdown("**AI formatted**")
                        try:
                            from skills.rubric_generator.rubric_agent import _parse_rubric_json
                            refined_obj = _parse_rubric_json(st.session_state.rubric_refined_text)
                            rubric_md = "| Criterion | Max Score | Description |\n|---|---|---|\n"
                            for c in refined_obj.get("criteria", []):
                                rubric_md += (
                                    f"| {c.get('name','')} "
                                    f"| {c.get('max_score','')} "
                                    f"| {c.get('description','')} |\n"
                                )
                            st.markdown(rubric_md)
                        except Exception:
                            st.code(st.session_state.rubric_refined_text, language="text")
                    btn1, btn2 = st.columns(2)
                    with btn1:
                        if st.button("Use formatted version", key="use_refined"):
                            st.session_state.rubric_manual_text = st.session_state.rubric_refined_text
                            st.session_state.rubric_key_v += 1
                            st.session_state.rubric_refine_view = False
                            st.rerun()
                    with btn2:
                        if st.button("Keep my original", key="keep_original"):
                            st.session_state.rubric_refine_view = False
                            st.rerun()

                # Approve
                if not st.session_state.rubric_approved:
                    if st.button("Approve rubric", key="approve_manual_rubric"):
                        st.session_state.rubric = st.session_state.rubric_manual_text
                        st.session_state.rubric_approved = True
                        st.rerun()

        else:
            # Auto-generate
            if st.button("Generate rubric from brief", key="generate_auto_rubric"):
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
                st.rerun()

            if st.session_state.rubric and not st.session_state.rubric_approved:
                if "rubric_edit_textarea" in st.session_state:
                    st.session_state.rubric = st.session_state.rubric_edit_textarea
                try:
                    rubric_data = json.loads(st.session_state.rubric)
                    st.markdown("### Grading Criteria")
                    for criterion in rubric_data.get("criteria", []):
                        st.markdown(f"**{criterion['name']}** — {criterion['max_score']} marks")
                        st.markdown(criterion.get("description", ""))
                        st.markdown("---")
                except Exception:
                    st.text_area(
                        "Review and edit the rubric:",
                        value=st.session_state.rubric,
                        height=300,
                        key="rubric_edit_textarea",
                    )
                    st.session_state.rubric = st.session_state.rubric_edit_textarea
                st.markdown(
                    "<span style='color:#f59e0b; font-size:0.85em;'>"
                    "AI-generated rubric — accuracy depends on assignment type. "
                    "Manual rubric is more reliable for code, math, and diagram assignments."
                    "</span>",
                    unsafe_allow_html=True,
                )
                if st.button("Approve rubric", key="approve_auto_rubric"):
                    st.session_state.rubric_approved = True
                    st.rerun()

    if st.session_state.rubric_approved:
        _status("Rubric approved and locked.", "success")
        with st.expander("View approved rubric"):
            try:
                rubric_obj = json.loads(st.session_state.rubric)
                if isinstance(rubric_obj, dict) and "criteria" in rubric_obj:
                    rubric_md = "| Criterion | Max Score | Description |\n|---|---|---|\n"
                    for c in rubric_obj["criteria"]:
                        rubric_md += (
                            f"| {c.get('name','')} "
                            f"| {c.get('max_score','')} "
                            f"| {c.get('description','')} |\n"
                        )
                    st.markdown(rubric_md)
                else:
                    st.markdown(st.session_state.rubric)
            except Exception:
                text = st.session_state.rubric.strip()
                if "\n" in text and "," in text and text.count(",") >= text.count("\n"):
                    try:
                        import io
                        import pandas as pd
                        df = pd.read_csv(io.StringIO(text))
                        st.dataframe(df, use_container_width=True, hide_index=True)
                    except Exception:
                        st.code(text, language="text")
                else:
                    st.code(text, language="text")
    else:
        _status("Upload both files in **Step 1** to unlock rubric controls.", "info")
else:
    _status("Upload both files in **Step 1** to unlock rubric controls.", "info")



# ── Step 3 — Answer Key ─────────────────────────────────────────
_divider()
_step(3, "Answer Key", "Provide an answer key to improve grading accuracy.")

if st.session_state.rubric_approved:


    if not st.session_state.answer_key_approved:
        answer_key_mode = st.radio(
            "How would you like to provide the answer key?",
            ["Provide manually", "Generate automatically"],
            index=0,
            format_func=lambda x: (
                "Provide manually  (Recommended — especially for technical and diagram-based assignments)"
                if x == "Provide manually" else x
            ),
            key="answer_key_mode_radio",
        )
        st.session_state.answer_key_mode = "manual" if answer_key_mode == "Provide manually" else "auto"

        if st.session_state.answer_key_mode == "manual":
            st.session_state.answer_key_manual_text = st.text_area(
                "Paste the solution / answer key:",
                value=st.session_state.answer_key_manual_text,
                height=180,
                key="answer_key_manual_textarea",
            )
            uploaded_ak = st.file_uploader(
                "Or upload answer key file (PDF, DOCX, PY, CPP, IPYNB):",
                type=["pdf", "docx", "py", "cpp", "ipynb"],
                key="answer_key_file_uploader",
            )
            if uploaded_ak:
                st.session_state.answer_key_file = uploaded_ak
                st.session_state.answer_key_uploaded_filename = uploaded_ak.name
                suffix = Path(uploaded_ak.name).suffix
                tmp_path = _save_temp(uploaded_ak, suffix)
                try:
                    st.session_state.answer_key_final = read_file(tmp_path)
                except Exception:
                    st.session_state.answer_key_final = f"[File uploaded: {uploaded_ak.name}]"
                finally:
                    os.unlink(tmp_path)
            elif st.session_state.answer_key_manual_text.strip():
                st.session_state.answer_key_final = st.session_state.answer_key_manual_text
            else:
                st.session_state.answer_key_final = None

        else:
            if st.button("Generate answer key from brief", key="generate_auto_answer_key"):
                if not st.session_state.brief_text:
                    suffix = Path(brief_file.name).suffix
                    tmp_path = _save_temp(brief_file, suffix)
                    try:
                        st.session_state.brief_text = read_file(tmp_path)
                    finally:
                        os.unlink(tmp_path)
                if not st.session_state.brief_text.strip():
                    st.error(
                        "Error: Could not extract any readable text from the assignment brief. "
                        "If this is a scanned/image-only document, you must temporarily set **EXTRACT_IMAGES=True** "
                        "in your `.env` file for the AI to read it."
                    )
                else:
                    from utils.llm_client import call_llm
                    with st.spinner("Generating answer key… (this uses the LLM fallback engine)"):
                        try:
                            sys_prompt = "You are an expert academic assistant. Given the assignment brief, generate a thorough model answer or solution key."
                            result = call_llm(
                                system_prompt=sys_prompt,
                                user_prompt=st.session_state.brief_text,
                                max_tokens=2048
                            )
                            st.session_state.answer_key_auto_text = result
                            st.session_state.answer_key_final = result
                            st.rerun()
                        except Exception as e:
                            st.error(f"Generation failed: {e}")

            if st.session_state.answer_key_auto_text:
                st.text_area(
                    "Generated answer key:",
                    value=st.session_state.answer_key_auto_text,
                    height=180,
                    key="answer_key_auto_display",
                )
                # Keep the grading input in sync with user edits.
                if not st.session_state.answer_key_approved:
                    st.session_state.answer_key_auto_text = st.session_state.answer_key_auto_display
                    st.session_state.answer_key_final = st.session_state.answer_key_auto_display
                st.markdown(
                    "<span style='color:#f59e0b; font-size:0.85em;'>"
                    "AI-generated answer key may be incorrect for code, math, or diagram assignments. "
                    "Always verify before grading."
                    "</span>",
                    unsafe_allow_html=True,
                )

    if st.session_state.answer_key_final:
        if not st.session_state.answer_key_approved:
            _status("Review the answer key and click Approve to proceed.", "info")
            if st.button("Approve Answer Key", key="approve_answer_key"):
                st.session_state.answer_key_approved = True
                st.rerun()
        else:
            _status("Answer key approved and locked.", "success")
    else:
        _status("The rubric is approved! Now provide or generate an answer key to proceed.", "info")
else:
    _status("Step locked. Approve the grading rubric in **Step 2** to unlock.", "info")



# ── Step 4 — Grade Submissions ──────────────────────────────────
_divider()
_step(4, "Grade Submissions", "Each submission is graded against the rubric and answer key, then checked for plagiarism.")

if st.session_state.rubric_approved and st.session_state.answer_key_approved and zip_file and st.session_state.results is None:
    if st.session_state.grading_in_progress:
        # Show a cancel button while grading is happening
        if st.button("Cancel Grading", type="secondary", use_container_width=True):
            st.session_state.grading_in_progress = False
            st.rerun()

    c1, c2 = st.columns([2, 8])
    with c1:
        start_btn = st.button(
            "Start Grading",
            use_container_width=True,
            disabled=st.session_state.grading_in_progress,
        )

    if start_btn:
        st.session_state.grading_in_progress = True
        tmp_zip = _save_temp(zip_file, ".zip")
        try:
            with st.spinner("Extracting submissions…"):
                exclude = []
                if st.session_state.get("answer_key_uploaded_filename"):
                    exclude.append(st.session_state.answer_key_uploaded_filename)
                submissions = extract_and_collect(tmp_zip, exclude_filenames=exclude)

            if not submissions:
                _status("No supported files found in the ZIP archive.", "error")
                st.stop()

            _status(f"Found <strong>{len(submissions)}</strong> submission(s). Grading…", "info")

            progress = st.progress(0, text="Grading submissions…")
            log_area = st.empty()
            
            lock = threading.Lock()
            progress_stats = {"n": 0}
            total = len(submissions)
            logs: list[str] = []

            def _on_complete(filename: str, _result: dict):
                with lock:
                    progress_stats["n"] += 1
                    progress.progress(
                        progress_stats["n"] / total,
                        text=f"Graded {progress_stats['n']}/{total} — {filename}",
                    )
                    score = _result.get("marks", "Error")
                    logs.append(f"✓ {filename} — Score: {score}")
                    log_area.code("\n".join(logs[-10:]), language="text")  # type: ignore

            results = grade_all(
                st.session_state.rubric,
                submissions,
                on_complete=_on_complete,
                answer_key=st.session_state.answer_key_final,
            )
            progress.progress(1.0, text="Grading complete.")

            with st.spinner("Running plagiarism check…"):
                flags = check_plagiarism(submissions)
                results = apply_flags(results, flags)

            with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp_xl:
                report_path = tmp_xl.name
            _, insights = write_results(results, report_path, return_insights=True)
            st.session_state.class_insights = insights

            st.session_state.results = results
            st.session_state.report_bytes = Path(report_path).read_bytes()
            os.unlink(report_path)
            st.rerun()
        finally:
            st.session_state.grading_in_progress = False
            os.unlink(tmp_zip)


elif st.session_state.results is not None:
    _status("Grading complete. View results below.", "success")
elif not zip_file or not brief_file:
    _status("Step locked. Complete **Step 1** to unlock.", "info")
elif not st.session_state.rubric_approved:
    _status("Step locked. Approve the rubric in **Step 2** to unlock.", "info")
elif not st.session_state.answer_key_approved:
    _status("Step locked. Approve the answer key in **Step 3** to unlock.", "info")

# ── Step 5 — Results ────────────────────────────────────────────
if st.session_state.results is not None:
    _divider()
    _step(5, "Results", "Summary metrics and the full grading table.")
    results = st.session_state.results

    numeric = [r["marks"] for r in results if isinstance(r.get("marks"), (int, float))]
    if numeric:
        flagged = sum(1 for r in results if r.get("plagiarism_flag"))
        passed  = sum(1 for m in numeric if m >= config.PASS_THRESHOLD)
        avg     = sum(numeric) / len(numeric)
        m1, m2, m3, m4 = st.columns(4)
        with m1:
            st.markdown(_metric(len(results), "Students"), unsafe_allow_html=True)
        with m2:
            st.markdown(_metric(f"{avg:.1f}", "Average"), unsafe_allow_html=True)
        with m3:
            st.markdown(
                _metric(f"{passed / len(numeric) * 100:.0f}%", "Pass Rate"),
                unsafe_allow_html=True,
            )
        with m4:
            st.markdown(_metric(flagged, "Plagiarism Flags"), unsafe_allow_html=True)

    st.write("")

    if st.session_state.class_insights:
        with st.expander("Class Insights (Top 3 Common Mistakes)", expanded=False):
            for i, insight in enumerate(st.session_state.class_insights, start=1):
                st.markdown(f"**{i}.** {insight}")

    df = pd.DataFrame(results)
    cols = ["name", "id", "marks"]
    all_cats = sorted({c for r in results for c in r.get("category_scores", {})})
    for i, cat in enumerate(all_cats):
        df[cat] = df["category_scores"].apply(
            lambda x, c=cat: x.get(c, "") if isinstance(x, dict) else ""
        )
        cols.insert(3 + i, cat)
    cols += ["deductions", "plagiarism_flag"]
    table_cols = [c for c in cols if c in df.columns]
    if "plagiarism_flag" in table_cols:
        df["plagiarism_flag"] = df["plagiarism_flag"].apply(shorten_plagiarism_flag)
    with st.expander("View detailed grading table", expanded=True):
        st.dataframe(df[table_cols], use_container_width=True, hide_index=True)

    st.write("")
    st.download_button(
        "Download Excel Report",
        data=st.session_state.report_bytes,
        file_name=config.OUTPUT_FILENAME,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

# ── Reset ────────────────────────────────────────────────────────
if st.session_state.rubric or st.session_state.results is not None:
    _divider()
    st.markdown('<div class="reset-btn">', unsafe_allow_html=True)
    if st.button("Start Over", use_container_width=True):
        for key, val in _DEFAULTS.items():
            st.session_state[key] = val
        st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

st.markdown("---")
st.markdown(
    "<p style='text-align: center; color: #94a3b8; font-size: 0.8rem;'>Built by Muhammad Umar Farooq</p>",
    unsafe_allow_html=True,
)
