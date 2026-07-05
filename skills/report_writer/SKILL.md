# Report Writer — Skill Instructions

## Purpose
Generates the final Excel grading report with styled formatting, per-criterion breakdowns, summary statistics, and deterministic class insights (top 3 common mistakes).

## When to Invoke
- At the very end of the pipeline, after grading and plagiarism detection are complete.

## Inputs
| Input | Type | Source |
|-------|------|--------|
| `results` | `list[dict]` | Grading results with plagiarism flags applied |
| `output_path` | `str` | Destination file path for the `.xlsx` |
| `assignment_name` | `str` | Optional — used as Excel sheet title |
| `course_code` | `str` | Optional — shown in summary statistics |
| `semester` | `str` | Optional — shown in summary statistics |

## Outputs
| Output | Type | Description |
|--------|------|-------------|
| `output_path` | `str` | Path to the written Excel file |

## Excel Sheets

### Sheet 1: Grading Report
| Column | Source |
|--------|--------|
| Name | From LMS folder name (Python-extracted, not LLM) |
| ID | From extractor identity metadata (roster/path/content precedence; LLM fallback only in grading) |
| Marks | Total score — computed by Python as sum of category scores (green = pass, red = fail) |
| [Criterion ...] | Dynamic columns from rubric criteria |
| Deductions / Reason | Built deterministically by Python: `"CritName: reason (-N)"` |
| Plagiarism Flag | Similarity details (red bold if flagged) |

### Sheet 2: Summary Statistics
- Total submissions, average, median, std deviation, min, max
- Pass/fail counts and pass rate
- Grade distribution: A (≥90%), B (80-89%), C (70-79%), D (60-69%), F (<60%)
- **Class Insights** — Top 3 most common mistakes across all students, generated deterministically by frequency analysis of deduction criteria/reasons. Styled with gold highlighting.

## Styling
- Light teacher-friendly workbook styling
- Blue header rows with white bold text
- Excel table formatting, filters, frozen header rows, and hidden gridlines
- Green/red pass/fail emphasis on marks
- Subtle score data bars for numeric score columns
- Highlighted plagiarism flags
- Thin borders, zebra rows, and teacher-readable column widths

## Key Functions
- `_generate_class_insights(results)` — deterministic extraction of top 3 common mistakes from deduction patterns
- `_write_insights_section(ws, start_row, insights)` — appends insights section to stats sheet
- `write_results(results, output_path)` — main entry point

## Dependencies
- `openpyxl`
- `config.PASS_THRESHOLD`, `config.TOTAL_MARKS`
