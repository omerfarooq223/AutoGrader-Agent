# Rubric Generator — Skill Instructions

## Purpose
Reads an assignment brief and generates a structured grading rubric using the Groq API (LLaMA 3.3 70B).

## When to Invoke
- At the start of every grading run, after the assignment brief is loaded.
- Skipped if a cached rubric from a previous run is reused.

## Inputs
| Input | Type | Source |
|-------|------|--------|
| `brief_text` | `str` | Extracted text from the assignment brief file |

## Outputs
| Output | Type | Description |
|--------|------|-------------|
| `rubric` | `str` | Numbered Markdown rubric with categories, mark allocations, and criteria |

## Workflow
1. Send the brief text to LLaMA 3.3 70B with a system prompt that enforces structured output.
2. Display the proposed rubric to the user.
3. User picks one of:
   - **[A]pprove** — use as-is
   - **[E]dit** — paste a manually edited version
   - **[R]egenerate** — call the LLM again
4. Save the approved rubric to `.rubric_cache.json` for reuse.

## Key Functions
- `generate_rubric(brief_text)` — LLM call with retry
- `approve_rubric(rubric)` — interactive approval loop
- `save_rubric(rubric, base_dir)` / `load_rubric(base_dir)` — disk persistence

## Dependencies
- `groq` (Groq API client)
- `config.MODEL`
- `utils.retry.retry_api_call`
