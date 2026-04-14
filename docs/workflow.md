# AutoGrader Workflow

## Overview

This document describes the end-to-end workflow of the AutoGrader agent, from input to final report.

---

## Pipeline Steps

### Step 1: Configuration Loading
- On startup, `config.py` reads the `.env` file and populates environment variables.
- No external `dotenv` dependency — uses a built-in parser.
- All settings (API key, model, thresholds, concurrency) are centralized here.
- Key production defaults: `MODEL=llama-3.1-8b-instant`, `MAX_CONCURRENT_GRADES=1`, `MAX_RETRIES=4`, `GRADING_MAX_OUTPUT_TOKENS=800`.

### Step 2: Assignment Brief Ingestion
- The brief file is read using `file_extractor/extractor.py`.
- Supports PDF, DOCX, .py, .cpp, and .ipynb formats.
- The extracted text is passed to the rubric generator.

### Step 3: Rubric Generation & Approval
- Before generating, the system checks `rubrics/` for a matching template by scanning the brief for keywords. A template is used only if it matches **≥2 keywords AND ≥40% of its keyword list** — prevents false matches on short keyword overlap.
- **With template**: The LLM receives the template structure and is asked to keep criterion names, adjust weights, and write detailed descriptions.
- **Without template**: The brief text is sent to the LLM to generate a rubric from scratch.
- In both cases, the LLM produces a **structured JSON rubric**:
  ```json
  {
    "criteria": [
      {"name": "Correctness", "max_score": 40, "description": "..."},
      {"name": "Code Quality", "max_score": 30, "description": "..."}
    ]
  }
  ```
- **Manual Rubric (Auto-Formatting)**: If the user provides a rubric manually, the system automatically detects if it is raw text (PDF paste, CSV, etc.) and uses the LLM to format it into the required JSON structure. This happens automatically when the user finishes pasting and clicks away.
- The output is validated via `_parse_rubric_json()` — invalid JSON or missing fields triggers an automatic retry.
- The user reviews the rubric and chooses to **Approve**. Large-scale edits can be made directly in the JSON or raw text before approval.
- The approved rubric is saved atomically to `.rubric_cache.json` for reuse.
- The LLM used for rubric generation is **Groq llama-3.1-8b-instant** (primary). Gemini is a regional fallback — unavailable in Pakistan.

### Step 4: Answer Key (Optional)
- The user can provide an answer key via file upload, manual paste, or skip entirely.
- If provided, each submission is graded by comparing it against the answer key alongside the rubric — improving accuracy significantly for factual and code assignments.
- The answer key filename is excluded from the submissions list automatically so the answer key author is never graded as a student.

### Step 5: Submission Extraction
- The ZIP file is extracted to a **per-session temp directory** named `.autograder_<zipname>/` in the ZIP's parent folder. This prevents cache collisions when grading multiple classes from the same directory.
- Zip-slip security check rejects unsafe archive entries (path traversal attack prevention).
- All supported files are discovered via recursive directory walk.
- Hidden/system directories (`__MACOSX`, `.git`, `__pycache__`) are skipped.
- Files exceeding **20MB** are skipped with a clear error message stored as the submission content — prevents LLM context overflow.
- Each file is read and stored as `{filename, path, content, cache_key}`. The `cache_key` is a content-hash-disambiguated identifier that handles duplicate filenames across students.
- **Format-specific extraction:**
  - **PDF**: Text extracted page by page via PyMuPDF.
  - **DOCX**: Paragraphs and **table content** both extracted (tables are not in `doc.paragraphs` and were previously invisible to the grader).
  - **.py / .cpp**: Read as plain text.
  - **.ipynb**: Code cells, markdown cells, and **cell outputs** (print results, errors, return values) all extracted — output is critical for grading whether code actually ran correctly.
- **Image extraction** (only when `EXTRACT_IMAGES=True`): Embedded images sent to Gemini Vision for description. Fails fast on quota exhaustion — never retries image description, so a dead Gemini quota does not freeze the pipeline.
- `extract_and_collect` returns `(submissions, extract_dir)`. The caller is responsible for cleaning up `extract_dir` **after** grading completes, so the cache file survives for crash recovery during long grading sessions.
- **Student identity extraction precedence** (name + ID) is deterministic and done before grading:
  1. Parse from submission **filename** (highest priority).
  2. If either is missing, parse from parent **folder name(s)**.
  3. If still missing, parse from submission **document content**.
  4. Final fallback for missing name only: LMS metadata when available.

### Step 6: Grading
- Each submission + the structured JSON rubric (+ answer key if provided) is sent to **Groq llama-3.1-8b-instant**.
- The model was switched from 3.3 70B to 3.1 8B Instant because: (a) 8B has ~5× higher free-tier TPM limit, (b) rubric-based grading against a provided answer key does not require strong open-ended reasoning — the LLM's job is comparison and scoring, not generation.
- **Student name + ID**: The grader consumes pre-extracted identity metadata from extraction step (filename → folder → content precedence). LLM identity output is now fallback-only when extraction cannot infer values.
- **LLM response**: The LLM returns per-criterion scores and brief reasons only: `{id, category_scores: {CritName: {score, reason}}}`. The LLM does NOT calculate totals, write `(-N)` amounts, or format deduction strings.
- **Deterministic Python scoring** (all math done in Python, never by the LLM):
  1. Per-criterion cap: each score is capped to `max_score` from the rubric (never below 0).
  2. Total: `marks = sum(category_scores)` — computed by Python.
  3. Deduction text: Built by Python as `"CritName: reason (-N)"` where `N = max_score - score`. Deduction amounts are guaranteed correct since they're derived from the score, not from LLM text.
  4. If all criteria have full marks: `"No deductions."`.
- **Concurrency**: `MAX_CONCURRENT_GRADES=1` by default. Architecture supports higher parallelism via `.env` — but rate limits make >1 unsafe on free tier.
- **Throttle**: 30s sleep between submissions (60s for large submissions >5k chars). Ensures Token-Per-Minute quota replenishes.
- **Cache**: Each result is saved atomically to `.grading_cache.json` immediately after completion using `cache_key` as the key. Cache files are **versioned** — when the scoring format changes, stale caches from prior versions are auto-discarded. Duplicate filenames are handled correctly. Atomic write (temp → rename) prevents cache corruption on crash.
- **Retry**: Failed API calls are retried with exponential backoff. **429 rate limit errors parse the exact retry time from Groq's error message** and wait precisely that long before retrying Groq — never fall through to Gemini on rate limit. Other Groq failures fall through to Gemini fallback.
- **Error submissions**: Files that could not be read (too large, corrupt, permission error) receive a clear error string as their content. These are graded with an "Error" mark — not silently passed through the LLM with garbage input.

### Step 7: Plagiarism Detection
- **Toggleable**: This step can be disabled via the "Enable Plagiarism Analysis" toggle in the sidebar. If disabled, similarity scoring is skipped entirely to save time.
- **Error and skipped submissions are excluded** from plagiarism analysis — identical error placeholder strings would generate false flags between all unreadable files.
- **Minimum content guard**: Submissions under 200 characters are excluded — too short for reliable similarity scoring.
- Remaining pairs are compared using:
  1. **TF-IDF Cosine Similarity** — vocabulary overlap
  2. **Character 4-gram Jaccard** — structural overlap
- Combined score: `0.6 × cosine + 0.4 × n-gram`
- Pairs scoring ≥ 65% similarity are flagged.
- **Flags preserve student names**: the plagiarism flag for each student lists exactly who they matched and at what percentage (e.g. `⚠️ Similar to: ali.pdf (96%), sara.docx (88%)`).
- Uses `cache_key` for matching — works correctly even when multiple students submitted files with identical filenames.
- No API calls — runs entirely locally using scikit-learn.

### Step 8: Report Generation
- Results are written atomically to `grading_report.xlsx` (temp → rename to prevent corruption).
- Two sheets:
  - **Grading Report**: per-student data with criterion columns, pass/fail coloring, and plagiarism flags showing matched names.
  - **Summary Statistics**: class-level metrics including pass rate, grade distribution, and Class Insights.
- **Marks column header** shows the assignment total: `Marks (/ 15)`.
- **Grade distribution** is calculated as a percentage of the **actual rubric total** (derived from marks data), not the config `TOTAL_MARKS` value. This prevents all students landing in F when `TOTAL_MARKS=100` but the rubric only adds to 15.
- **Pass threshold** is computed as 50% of the rubric total — shown explicitly in stats as `Pass Mark: ≥ 8 (50%)`.
- **Class Insights**: deduction reasons are trimmed (300 chars each, 8000 char total cap) and sent to the LLM to identify top 3 most common mistakes. Students with grading errors are excluded. In `app.py`, insights are generated in a **background thread** so the UI stays responsive — the report file is available immediately regardless of how long insights take.
- The grading cache is cleared only **after** the report is successfully written — a crash during report generation does not lose grading progress.

### Step 9: Results UI Analytics & Manual Override
- In Streamlit Step 5, results render two side-by-side charts:
  - **Score Distribution** (bar chart): score values on X-axis, student count on Y-axis.
  - **Average Score per Criterion** (horizontal bars): criterion means with threshold colors based on rubric max score.
- Teachers can edit per-student criterion scores directly in the UI table.
- On **Apply score overrides**, totals are recalculated, in-memory results are updated, and the downloadable Excel report is regenerated immediately.

---

## Error Handling Strategy

| Scenario | Handling |
|----------|----------|
| Groq 429 rate limit | Parses exact retry time from error message, waits precisely, retries Groq |
| Groq non-429 failure | Falls through to Gemini fallback (regional availability varies) |
| Gemini daily quota exhausted (`limit: 0`) | Detected immediately, all Gemini fallbacks skipped with clear error |
| Process crash mid-grading | Atomic cache survives; next run resumes from `cache_key` checkpoint |
| Malformed LLM JSON | Graceful fallback with "Error" mark; raw response snippet in deductions |
| Individual criterion score > max_score | Capped to rubric max by Python |
| Stale cache from old scoring format | Auto-discarded via cache version check |
| File too large (> 20MB) | Skipped with student-facing error message; grader sees it, scores 0 |
| Corrupt/unreadable file | Error stored as content; submission not silently dropped |
| Unsupported file format in ZIP | Skipped silently |
| Unsafe ZIP entries (zip-slip) | Rejected with ValueError before extraction |
| Duplicate filenames in ZIP | Handled via content-hash `cache_key` — no result collisions |
| Answer key file left in ZIP | Excluded by filename stem matching before grading |
| Image extraction failure | Fails fast (no retry); text extraction continues unaffected |
| Class insights LLM failure | Skipped silently; report still generated without insights section |
| Report write interrupted mid-file | Atomic write (temp → rename) leaves previous report intact |