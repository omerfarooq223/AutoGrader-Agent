# Grader — Skill Instructions

## Purpose
Grades each student submission against the approved rubric using the Groq API (LLaMA 3.3 70B). Extracts student name, ID, total marks, per-category scores, deductions, and qualitative feedback.

## When to Invoke
- After the rubric is approved and all submissions have been extracted.

## Inputs
| Input | Type | Source |
|-------|------|--------|
| `rubric` | `str` | Approved grading rubric |
| `submissions` | `list[dict]` | From file_extractor (`filename`, `path`, `content`) |
| `cached` | `dict` | Previously graded results from cache (optional) |

## Outputs
| Output | Type | Description |
|--------|------|-------------|
| `results` | `list[dict]` | Each entry: `name`, `id`, `marks`, `category_scores`, `deductions`, `feedback` |

## Workflow
1. Check cache — skip already-graded submissions.
2. For each remaining submission, send `rubric + content` to LLaMA 3.3 70B.
3. LLM responds with structured JSON containing scores and feedback.
4. Parse JSON (handles code fences, malformed output gracefully).
5. Save each result to cache immediately after grading.

## Concurrency
- Uses `ThreadPoolExecutor` with `MAX_CONCURRENT_GRADES` workers (default: 4).
- Each completed grading triggers `on_complete` callback for cache persistence.

## Key Functions
- `grade_submission(rubric, submission_text, filename)` — single submission grading with retry
- `grade_all(rubric, submissions, cached, on_complete)` — concurrent batch grading

## Dependencies
- `groq` (Groq API client)
- `config.MODEL`, `config.MAX_CONCURRENT_GRADES`
- `utils.retry.retry_api_call`
