# Report Writer — Skill Instructions

## Purpose
Generates the final Excel grading report with styled formatting, per-category breakdowns, and summary statistics.

## When to Invoke
- At the very end of the pipeline, after grading and plagiarism detection are complete.

## Inputs
| Input | Type | Source |
|-------|------|--------|
| `results` | `list[dict]` | Grading results with plagiarism flags applied |
| `output_path` | `str` | Destination file path for the `.xlsx` |

## Outputs
| Output | Type | Description |
|--------|------|-------------|
| `output_path` | `str` | Path to the written Excel file |

## Excel Sheets

### Sheet 1: Grading Report
| Column | Source |
|--------|--------|
| Name | Extracted from submission |
| ID | Extracted from submission |
| Marks | Total score (green = pass, red = fail) |
| [Category ...] | Dynamic columns from rubric categories |
| Deductions / Reason | LLM-generated deduction details |
| Feedback | Qualitative feedback summary |
| Plagiarism Flag | Similarity details (red bold if flagged) |

### Sheet 2: Summary Statistics
- Total submissions, average, median, std deviation, min, max
- Pass/fail counts and pass rate
- Grade distribution: A (≥90%), B (80-89%), C (70-79%), D (60-69%), F (<60%)

## Styling
- Blue header row with white bold text
- Green/red conditional fill on marks (pass/fail threshold from config)
- Red bold font on plagiarism flags
- Thin borders on all cells
- Auto-fit column widths

## Key Functions
- `write_results(results, output_path)` — main entry point

## Dependencies
- `openpyxl`
- `config.PASS_THRESHOLD`, `config.TOTAL_MARKS`
