# Grader — Skill Instructions

## Purpose
Grades each student submission against the approved structured JSON rubric using the Groq API (LLaMA 3.3 70B). Scores every criterion individually before summing to a total, producing auditable per-criterion subscores alongside deductions and qualitative feedback.

## When to Invoke
- After the rubric is approved and all submissions have been extracted.

## Inputs
| Input | Type | Source |
|-------|------|--------|
| `rubric` | `str` | Approved structured JSON rubric (`{"criteria": [...]}`) |
| `submissions` | `list[dict]` | From file_extractor (`filename`, `path`, `content`) |
| `cached` | `dict` | Previously graded results from cache (optional) |

## Outputs
| Output | Type | Description |
|--------|------|-------------|
| `results` | `list[dict]` | Each entry: `name`, `id`, `marks`, `category_scores`, `deductions`, `feedback` |

## Workflow
1. Check cache — skip already-graded submissions.
2. For each remaining submission, send the structured JSON rubric + content to LLaMA 3.3 70B.
3. LLM scores each criterion individually (0 to `max_score`), then sums to total `marks`.
4. LLM responds with structured JSON: `{name, id, marks, category_scores, deductions, feedback}`.
5. The `category_scores` dict must contain one entry per rubric criterion, and `marks` must equal the sum.
6. **Score validation**: After parsing, if `category_scores` is non-empty and its sum doesn't match `marks`, the total is auto-corrected and `[Auto-corrected: LLM total did not match category sum]` is appended to deductions.
7. Parse JSON (handles code fences, malformed output gracefully).
8. Save each result to cache immediately after grading.

## Concurrency
- Uses `ThreadPoolExecutor` with `MAX_CONCURRENT_GRADES` workers (default: 4).
- Each completed grading triggers `on_complete` callback for cache persistence.

## Key Functions
- `_parse_json(raw, fallback_name)` — JSON extraction + score validation (auto-corrects mismatched totals)
- `grade_submission(rubric, submission_text, filename)` — single submission grading with retry
- `grade_all(rubric, submissions, cached, on_complete)` — concurrent batch grading

## Dependencies
- `groq` (Groq API client)
- `config.MODEL`, `config.MAX_CONCURRENT_GRADES`
- `utils.retry.retry_api_call`
