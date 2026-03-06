# My Work Description — AutoGrader Workflow

## Overview

This document describes the end-to-end workflow of the AutoGrader agent, from input to final report.

---

## Pipeline Steps

### Step 1: Configuration Loading
- On startup, `config.py` reads the `.env` file and populates environment variables.
- No external `dotenv` dependency — uses a built-in parser.
- All settings (API key, model, thresholds, concurrency) are centralized here.

### Step 2: Assignment Brief Ingestion
- The brief file is read using `file_extractor/extractor.py`.
- Supports PDF, DOCX, .py, .cpp, and .ipynb formats.
- The extracted text is passed to the rubric generator.

### Step 3: Rubric Generation & Approval
- Before generating, the system checks `rubrics/` for a matching template by scanning the brief for keywords (≥2 hits required).
- **With template**: The LLM receives the template structure (criterion names and default weights) and is asked to keep the names, adjust weights for the specific assignment, and fill in detailed descriptions.
- **Without template**: The brief text is sent to LLaMA 3.3 70B to generate a rubric from scratch.
- In both cases, the LLM produces a **structured JSON rubric** matching this schema:
  ```json
  {
    "criteria": [
      {"name": "Correctness", "max_score": 40, "description": "..."},
      {"name": "Code Quality", "max_score": 30, "description": "..."},
      {"name": "Documentation", "max_score": 20, "description": "..."},
      {"name": "Formatting", "max_score": 10, "description": "..."}
    ]
  }
  ```
- The output is validated via `_parse_rubric_json()` — invalid JSON triggers an automatic retry.
- The user reviews the rubric and chooses to:
  - **Approve** it as-is
  - **Edit** it manually
  - **Regenerate** a new one
- The approved rubric is cached to disk (`.rubric_cache.json`) for reuse in future runs.

### Step 4: Submission Extraction
- The ZIP file is extracted to a temporary directory.
- Zip-slip security check rejects unsafe archive entries.
- All supported files are discovered via recursive directory walk.
- Hidden/system directories (`__MACOSX`, `.git`) are skipped.
- Each file is read and stored as `{filename, path, content}`.
- **Image extraction**: Embedded images in PDF and DOCX files are extracted, sent to Groq's vision model (`meta-llama/llama-4-scout-17b-16e-instruct`), and the returned descriptions are appended to the document text as `[Image: <description>]`. This gives downstream grading full context on diagrams, charts, screenshots, and handwritten content. Vision API failures are handled silently with retries.

### Step 5: Grading
- Each submission + the structured JSON rubric is sent to LLaMA 3.3 70B.
- The LLM scores **each criterion individually** (0 to `max_score`), then sums to a total.
- The LLM returns structured JSON:
  ```json
  {
    "name": "John Doe",
    "id": "22F-1234",
    "marks": 82,
    "category_scores": {"Correctness": 30, "Code Quality": 22, "Documentation": 15, "Formatting": 15},
    "deductions": "-8: Missing error handling in main function. -5: No docstrings. -5: Inconsistent indentation.",
    "feedback": "Solid implementation with correct logic. Code quality could be improved with better documentation and consistent formatting."
  }
  ```
- `category_scores` must contain one entry per rubric criterion, and `marks` must equal their sum — this replaces vague totals with auditable subscores.
- **Score validation**: After parsing, if the sum of `category_scores` doesn't match `marks`, the total is auto-corrected and a note is appended to deductions. This prevents LLM arithmetic errors from propagating into the report.
- **Concurrency**: 4 submissions graded in parallel via ThreadPoolExecutor.
- **Cache**: Each result is saved to `.grading_cache.json` immediately after completion. If the process crashes, the next run resumes from where it left off.
- **Retry**: Failed API calls are retried with exponential backoff (2s → 4s → 8s, up to 3 attempts).

### Step 6: Plagiarism Detection
- All submission pairs are compared using two methods:
  1. **TF-IDF Cosine Similarity** — semantic overlap (catches paraphrasing)
  2. **Character 4-gram Jaccard** — structural overlap (catches copy-paste)
- Combined score: `0.6 × cosine + 0.4 × n-gram`
- Pairs scoring ≥ 65% similarity are flagged.
- No API calls — runs entirely locally using scikit-learn.

### Step 7: Report Generation
- Results are written to `grading_report.xlsx` with two sheets:
  - **Grading Report**: per-student data with dynamic criterion columns, pass/fail coloring, and plagiarism flags.
  - **Summary Statistics**: class-level metrics (average, median, pass rate, grade distribution) plus a **Class Insights** section.
- **Class Insights**: All deduction reasons across all students are sent to the LLM in one call, which returns the top 3 most common mistakes. These are appended to the Summary Statistics sheet with gold highlighting. If the API call fails, the section is skipped silently.
- The grading cache is cleared after successful report generation.

---

## Error Handling Strategy

| Scenario | Handling |
|----------|----------|
| API rate limit | Exponential backoff retry (up to 3 attempts) |
| Process crash mid-grading | Cache-based resume on next run |
| Malformed LLM JSON response | Graceful fallback with "Error" mark and raw response snippet |
| LLM total ≠ category sum | Auto-corrected to correct sum; note appended to deductions |
| Unsupported file format in ZIP | Skipped silently, only supported formats processed |
| Corrupt file in ZIP | Error message stored as content instead of crashing |
| Unsafe ZIP entries (zip-slip) | Rejected with ValueError before extraction |
| Vision API failure for an image | Skipped silently, text extraction continues |
| Corrupt/unreadable embedded image | Skipped silently, other images still processed |
| Class insights LLM call failure | Insights section omitted, report still generates |

---

## Web UI (Streamlit)

In addition to the CLI (`main.py`), AutoGrader provides a browser-based interface via `app.py`. The UI wraps the same skill modules — no grading logic is duplicated.

### UI Workflow

| Step | User Action | Backend Call |
|------|-------------|-------------|
| 1. Upload | Drag-and-drop ZIP + assignment brief | Files saved to a temp directory |
| 2. Rubric | Review auto-generated rubric, edit or approve | `read_file()` → `generate_rubric()` |
| 3. Grade | Click "Grade All" — progress bar shows completion | `extract_and_collect()` → `grade_all()` → `check_plagiarism()` → `apply_flags()` |
| 4. Results | View summary table, download Excel report | `write_results()` → in-memory download via `st.download_button` |

### Key Implementation Details

- **Session state**: All intermediate data (brief text, rubric, results, report bytes) is stored in `st.session_state` so the page survives Streamlit reruns.
- **Progress tracking**: `grade_all()` accepts an `on_complete` callback that increments a Streamlit progress bar after each submission is graded.
- **Temp files**: Uploaded files are written to `tempfile.mkdtemp()` — cleaned up by the OS.
- **No server-side file persistence**: The Excel report is held in memory as bytes and served via `st.download_button`. Nothing is written to the project directory.
- **Reset**: A "Start Over" button clears all session state and reruns the app.

### Running the Web UI

```bash
streamlit run app.py
```

---

## Design Decisions

1. **Skills-based structure**: Each capability is isolated in its own directory with a SKILL.md describing its purpose, inputs, outputs, and dependencies.
2. **Cache separation**: Rubric cache (`.rubric_cache.json`) is intentional reuse; grading cache (`.grading_cache.json`) is automatic crash recovery that self-cleans.
3. **Dual plagiarism detection**: Single-method detection has blind spots — cosine misses verbatim copy-paste, n-grams miss paraphrasing. Combined scoring covers both.
4. **Concurrent grading**: Sequential grading of 60+ submissions is slow. ThreadPoolExecutor with 4 workers provides ~4x speedup while staying within Groq rate limits.
5. **Config via .env**: Keeps secrets out of code, lets users tune thresholds without editing Python files.
6. **Streamlit UI as a wrapper**: The web UI calls existing skill functions directly instead of duplicating logic, keeping a single source of truth for all grading behavior.
7. **Rubric templates**: Pre-defined templates for common assignment types (programming, essay) reduce hallucination risk and improve consistency. The LLM fills in context-specific details rather than inventing the entire structure. Custom templates can be added by dropping a JSON file into `rubrics/`.
8. **Post-parse score validation**: LLMs occasionally miscalculate sums. Auto-correcting `marks` to equal the actual `category_scores` sum ensures the report is always arithmetically consistent without requiring an extra API call.
