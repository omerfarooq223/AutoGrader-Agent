"""
Excel writer utility — generates the final grading report with:
  - Main grading sheet (Name, ID, Marks, Category Scores, Deductions, Feedback, Plagiarism)
  - Summary statistics sheet (avg, median, min, max, pass/fail, grade distribution)
"""

import statistics

import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils import get_column_letter

from config import PASS_THRESHOLD, TOTAL_MARKS


# ── Styling constants ───────────────────────────────────────────
_HEADER_FONT = Font(bold=True, color="FFFFFF", size=11)
_HEADER_FILL = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
_PASS_FILL = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
_FAIL_FILL = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
_FLAG_FONT = Font(color="FF0000", bold=True)
_THIN_BORDER = Border(
    left=Side(style="thin"), right=Side(style="thin"),
    top=Side(style="thin"), bottom=Side(style="thin"),
)


def _auto_width(ws) -> None:
    """Auto-fit column widths based on cell contents."""
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            if cell.value:
                max_len = max(max_len, len(str(cell.value)))
        ws.column_dimensions[col_letter].width = min(max_len + 4, 60)


def _collect_all_categories(results: list[dict]) -> list[str]:
    """Gather a sorted union of all category names across results."""
    cats: set[str] = set()
    for r in results:
        cats.update(r.get("category_scores", {}).keys())
    return sorted(cats)


def _write_grading_sheet(wb: openpyxl.Workbook, results: list[dict]) -> None:
    """Write the main Grading Report sheet."""
    ws = wb.active
    ws.title = "Grading Report"

    categories = _collect_all_categories(results)

    # Build headers dynamically
    headers = ["Name", "ID", "Marks"]
    headers += [f"[{c}]" for c in categories]
    headers += ["Deductions / Reason", "Feedback", "Plagiarism Flag"]

    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
        cell.border = _THIN_BORDER

    # Data rows
    for row_idx, entry in enumerate(results, start=2):
        col = 1
        ws.cell(row=row_idx, column=col, value=entry.get("name", "")); col += 1
        ws.cell(row=row_idx, column=col, value=entry.get("id", "")); col += 1

        # Marks cell with pass/fail coloring
        marks = entry.get("marks", "")
        marks_cell = ws.cell(row=row_idx, column=col, value=marks); col += 1
        if isinstance(marks, (int, float)):
            marks_cell.fill = _PASS_FILL if marks >= PASS_THRESHOLD else _FAIL_FILL

        # Category scores
        cat_scores = entry.get("category_scores", {})
        for cat in categories:
            ws.cell(row=row_idx, column=col, value=cat_scores.get(cat, "")); col += 1

        ws.cell(row=row_idx, column=col, value=entry.get("deductions", "")); col += 1
        ws.cell(row=row_idx, column=col, value=entry.get("feedback", "")); col += 1

        flag = entry.get("plagiarism_flag", "")
        flag_cell = ws.cell(row=row_idx, column=col, value=flag)
        if flag:
            flag_cell.font = _FLAG_FONT

        # Apply border to all cells in this row
        for c in range(1, col + 1):
            ws.cell(row=row_idx, column=c).border = _THIN_BORDER

    _auto_width(ws)


def _write_stats_sheet(wb: openpyxl.Workbook, results: list[dict]) -> None:
    """Write a Summary Statistics sheet."""
    ws = wb.create_sheet("Summary Statistics")

    # Collect numeric marks
    marks = [r["marks"] for r in results if isinstance(r.get("marks"), (int, float))]
    total_students = len(results)

    stats_data = [
        ("Total Submissions", total_students),
        ("Graded (numeric marks)", len(marks)),
    ]

    if marks:
        passed = sum(1 for m in marks if m >= PASS_THRESHOLD)
        stats_data += [
            ("", ""),
            ("Average", round(statistics.mean(marks), 2)),
            ("Median", round(statistics.median(marks), 2)),
            ("Std Deviation", round(statistics.stdev(marks), 2) if len(marks) > 1 else "N/A"),
            ("Minimum", min(marks)),
            ("Maximum", max(marks)),
            ("", ""),
            (f"Passed (≥ {PASS_THRESHOLD})", passed),
            (f"Failed (< {PASS_THRESHOLD})", len(marks) - passed),
            ("Pass Rate", f"{passed / len(marks) * 100:.1f}%"),
        ]

        # Grade distribution (A/B/C/D/F based on percentage of TOTAL_MARKS)
        buckets = {"A (≥90%)": 0, "B (80-89%)": 0, "C (70-79%)": 0, "D (60-69%)": 0, "F (<60%)": 0}
        for m in marks:
            pct = (m / TOTAL_MARKS) * 100 if TOTAL_MARKS else 0
            if pct >= 90:
                buckets["A (≥90%)"] += 1
            elif pct >= 80:
                buckets["B (80-89%)"] += 1
            elif pct >= 70:
                buckets["C (70-79%)"] += 1
            elif pct >= 60:
                buckets["D (60-69%)"] += 1
            else:
                buckets["F (<60%)"] += 1

        stats_data.append(("", ""))
        stats_data.append(("Grade Distribution", "Count"))
        for grade, count in buckets.items():
            stats_data.append((grade, count))

    # Write to sheet
    header_labels = ["Metric", "Value"]
    for col_idx, h in enumerate(header_labels, start=1):
        cell = ws.cell(row=1, column=col_idx, value=h)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.border = _THIN_BORDER

    for row_idx, (metric, value) in enumerate(stats_data, start=2):
        ws.cell(row=row_idx, column=1, value=metric).border = _THIN_BORDER
        ws.cell(row=row_idx, column=2, value=value).border = _THIN_BORDER

    _auto_width(ws)


def write_results(results: list[dict], output_path: str = "results.xlsx") -> str:
    """
    Write grading results to an Excel file with two sheets:
      1. Grading Report — per-student results
      2. Summary Statistics — class-level stats

    Returns the path to the written file.
    """
    wb = openpyxl.Workbook()
    _write_grading_sheet(wb, results)
    _write_stats_sheet(wb, results)
    wb.save(output_path)
    return output_path
