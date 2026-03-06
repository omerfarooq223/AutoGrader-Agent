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
- The brief text is sent to LLaMA 3.3 70B via the Groq API.
- The LLM produces a structured rubric with:
  - Categories (e.g., Correctness, Code Quality, Documentation)
  - Mark allocations per category
  - Criteria for full, partial, and zero marks
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

### Step 5: Grading
- Each submission + the rubric is sent to LLaMA 3.3 70B.
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
  - **Grading Report**: per-student data with dynamic category columns, pass/fail coloring, and plagiarism flags.
  - **Summary Statistics**: class-level metrics (average, median, pass rate, grade distribution).
- The grading cache is cleared after successful report generation.

---

## Error Handling Strategy

| Scenario | Handling |
|----------|----------|
| API rate limit | Exponential backoff retry (up to 3 attempts) |
| Process crash mid-grading | Cache-based resume on next run |
| Malformed LLM JSON response | Graceful fallback with "Error" mark and raw response snippet |
| Unsupported file format in ZIP | Skipped silently, only supported formats processed |
| Corrupt file in ZIP | Error message stored as content instead of crashing |
| Unsafe ZIP entries (zip-slip) | Rejected with ValueError before extraction |

---

## Design Decisions

1. **Skills-based structure**: Each capability is isolated in its own directory with a SKILL.md describing its purpose, inputs, outputs, and dependencies.
2. **Cache separation**: Rubric cache (`.rubric_cache.json`) is intentional reuse; grading cache (`.grading_cache.json`) is automatic crash recovery that self-cleans.
3. **Dual plagiarism detection**: Single-method detection has blind spots — cosine misses verbatim copy-paste, n-grams miss paraphrasing. Combined scoring covers both.
4. **Concurrent grading**: Sequential grading of 60+ submissions is slow. ThreadPoolExecutor with 4 workers provides ~4x speedup while staying within Groq rate limits.
5. **Config via .env**: Keeps secrets out of code, lets users tune thresholds without editing Python files.
