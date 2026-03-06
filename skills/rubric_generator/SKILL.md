# Rubric Generator — Skill Instructions

## Purpose
Reads an assignment brief and generates a structured grading rubric using the Groq API (LLaMA 3.3 70B). Automatically selects a rubric template from `rubrics/` when the brief matches a known assignment type, otherwise generates from scratch.

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

Each template includes `match_keywords`. Before generating, `_match_template()` scans the brief text for keyword hits (≥2 required). When a template matches, the LLM receives the template structure and is asked to keep criterion names, adjust weights, and fill in descriptions tailored to the brief. When no template matches, generation proceeds from scratch.

Custom templates can be added by placing a new `.json` file in `rubrics/` with the same format.

## Rubric Schema
The LLM is forced to produce valid JSON matching this exact structure:
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
- Output is validated via `_parse_rubric_json()` — invalid JSON triggers an automatic retry.

## Workflow
1. Check `rubrics/` for a matching template based on brief keywords.
2. If a template matches, send it + the brief to the LLM to fill in descriptions and adjust weights.
3. If no template matches, send the brief to LLaMA 3.3 70B with a system prompt that enforces structured output from scratch.
4. Display the proposed rubric to the user.
3. User picks one of:
   - **[A]pprove** — use as-is
   - **[E]dit** — paste a manually edited version
   - **[R]egenerate** — call the LLM again
4. Save the approved rubric to `.rubric_cache.json` for reuse.

## Key Functions
- `_load_templates()` — reads all JSON templates from `rubrics/`
- `_match_template(brief_text)` — keyword matching to select the best template (≥2 hits required)
- `_parse_rubric_json(raw)` — validates LLM output matches the required JSON schema
- `generate_rubric(brief_text)` — template-aware LLM call with retry + schema validation
- `approve_rubric(rubric)` — interactive approval loop
- `save_rubric(rubric, base_dir)` / `load_rubric(base_dir)` — disk persistence

## Dependencies
- `groq` (Groq API client)
- `config.MODEL`
- `utils.retry.retry_api_call`
