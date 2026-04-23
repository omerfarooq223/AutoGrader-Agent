# Grader — Skill Instructions

## Purpose
Grades each student submission against the approved structured JSON rubric using the LLM (Groq / Gemini). The LLM evaluates each criterion and provides a score with a brief reason. All scoring math (totals, deduction text, capping) is done deterministically in Python — the LLM never calculates totals or formats deduction strings.

## When to Invoke
- After the rubric is approved and all submissions have been extracted.

## Inputs
| Input | Type | Source |
|-------|------|--------|
| `rubric` | `str` | Approved structured JSON rubric (`{"criteria": [...]}`) |
| `submissions` | `list[dict]` | From file_extractor (`filename`, `path`, `content`, `lms_meta`) |
| `cached` | `dict` | Previously graded results from cache (optional) |
| `answer_key` | `str` | Optional model answer for comparison-based grading |

## Outputs
| Output | Type | Description |
|--------|------|-------------|
| `results` | `list[dict]` | Each entry: `name`, `id`, `marks`, `category_scores`, `deductions` |

## Workflow
1. Check cache — skip already-graded submissions. Stale caches (wrong version) are auto-discarded.
2. For each remaining submission, send the structured JSON rubric + content to the LLM.
3. LLM returns per-criterion scores and brief reasons as `{id, category_scores: {CritName: {score, reason}}}`.
4. **Python deterministic scoring** (no LLM math):
   - Cap each score to `max_score` from rubric (never below 0, never above max).
   - Compute `marks = sum(category_scores)`.
   - Build deduction text: `"CritName: reason (-N)"` where N = `max_score - score`.
   - If all criteria have full marks: `"No deductions."`.
5. Student name comes from LMS folder structure (`lms_meta.student_name`), LLM is fallback only.
6. Student ID is taken from extracted identity metadata (roster/path/content precedence); LLM extraction is fallback only when metadata is missing.
7. Save each result to cache immediately after grading.

## Concurrency
- Uses `ThreadPoolExecutor` with `MAX_CONCURRENT_GRADES` workers (default: 1).
- Each completed grading triggers `on_complete` callback for cache persistence.

## Key Functions
- `_build_rubric_maxes(rubric)` — Parse rubric to get max_score per criterion
- `_parse_json(raw, fallback_name, rubric, lms_name)` — JSON extraction + deterministic Python scoring
- `grade_submission(rubric, submission_text, filename, answer_key, lms_name)` — single submission grading with retry
- `grade_all(rubric, submissions, cached, on_complete, answer_key)` — concurrent batch grading

## Dependencies
- `groq` (Groq API client) / `google-genai` (Gemini fallback)
- `config.MODEL`, `config.MAX_CONCURRENT_GRADES`
- `utils.retry.retry_api_call`
- `utils.llm_client.call_llm`
