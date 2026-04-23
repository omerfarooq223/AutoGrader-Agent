## Purpose
Reads an assignment brief and generates a structured grading rubric with a deterministic-first strategy. It automatically selects a rubric template from `rubrics/` when the brief matches a known assignment type, or formats raw pasted text into the required JSON structure using deterministic parsers first and LLM fallback only when needed.

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
| `rubric` | `str` | Structured JSON rubric: `{"criteria": [{"name": "...", "max_score": N, "description": "..."}, ...]}` |

## Rubric Templates
The `rubrics/` directory contains JSON templates for common assignment types:
- **programming_assignment.json** — Correctness, Code Quality, Documentation, Testing, Formatting & Style
- **essay_assignment.json** — Argument, Evidence, Structure, Clarity, Formatting & Citations

Each template includes `match_keywords`. Before generating, `_match_template()` scans the brief text for keyword hits (≥2 required). When a template matches, rubric criteria are built deterministically from template data (including default full/partial/minimal band descriptions when missing). When no template matches, generation proceeds from scratch via LLM.

Custom templates can be added by placing a new `.json` file in `rubrics/` with the same format.

## Rubric Schema
Rubrics follow this exact structure:
```json
{
  "criteria": [
    {"name": "Concept Understanding", "max_score": 4, "description": "..."},
    {"name": "Answer Completeness", "max_score": 3, "description": "..."}
  ]
}
```
- Each criterion has a `name`, `max_score` (positive number), and `description` (explains full/partial/zero mark criteria).
- The `max_score` values across all criteria should sum to the total assignment marks.
- Output is validated via `_parse_rubric_json()` — invalid JSON triggers fallback/retry paths.

## Workflow
1. Check `rubrics/` for a matching template based on brief keywords.
2. If a template matches, build rubric deterministically from template criteria and scale marks.
3. If no template matches, send the brief to the configured LLM model with a system prompt that enforces structured output.
4. Display the proposed rubric to the user.
3. User picks one of:
   - **[A]pprove** — use as-is
   - **[E]dit** — paste a manually edited version
   - **[R]egenerate** — call the LLM again
4. Save the approved rubric to `.rubric_cache.json` for reuse.

## Key Functions
- `_load_templates()` — reads all JSON templates from `rubrics/`
- `_match_template(brief_text)` — keyword matching to select the best template (≥2 hits required)
- `format_rubric_to_json(rubric_text)` — deterministic parser (JSON/table/line formats) with LLM fallback
- `_parse_rubric_json(raw)` — validates LLM output matches the required JSON schema
- `generate_rubric(brief_text)` — deterministic template path + LLM fallback for unmatched briefs
- `approve_rubric(rubric)` — interactive approval loop
- `save_rubric(rubric, base_dir)` / `load_rubric(base_dir)` — disk persistence

## Dependencies
- `groq` / `google-genai` (when fallback LLM path is used)
- `config.MODEL`
- `utils.retry.retry_api_call`
