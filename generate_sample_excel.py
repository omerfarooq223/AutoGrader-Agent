import sys
import os

# Ensure the parent directory is in sys.path so imports work
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from skills.report_writer.excel_writer import write_results

# Sample grading data containing Passes, Fails, and Plagiarism flags for demonstration
dummy_results = [
    {
        "name": "Alice Smith",
        "id": "STU1001",
        "marks": 95.0,
        "category_scores": {"[Code Quality]": 45, "[Correctness]": 50},
        "deductions": "Minor spelling mistake in comments.",
        "plagiarism_flag": ""
    },
    {
        "name": "Bob Jones",
        "id": "STU1002",
        "marks": 45.0, # Fail (below 50% of 100)
        "category_scores": {"[Code Quality]": 25, "[Correctness]": 20},
        "deductions": "-30 for missing test cases.\n-25 for syntax errors in main module.",
        "plagiarism_flag": "Similar to Charlie.pdf (95.0%, cos=96% ngram=96%)"
    },
    {
        "name": "Charlie Brown",
        "id": "STU1003",
        "marks": 40.0, # Fail
        "category_scores": {"[Code Quality]": 20, "[Correctness]": 20},
        "deductions": "-40 logic entirely incorrect.\n-20 no inline documentation.",
        "plagiarism_flag": "Similar to Bob.pdf (95.0%, cos=96% ngram=96%)"
    },
    {
        "name": "Diana Miller",
        "id": "STU1004",
        "marks": 100.0,
        "category_scores": {"[Code Quality]": 50, "[Correctness]": 50},
        "deductions": "Perfect score. Exceptional efficiency.",
        "plagiarism_flag": ""
    },
    {
        "name": "Eve Adams",
        "id": "STU1005",
        "marks": 78.0,
        "category_scores": {"[Code Quality]": 38, "[Correctness]": 40},
        "deductions": "-12 for suboptimal loop performance.\n-10 for missing docstrings.",
        "plagiarism_flag": ""
    }
]

output_filename = "sample_report_preview.xlsx"
write_results(
    results=dummy_results,
    output_path=output_filename,
    return_insights=False,
    assignment_name="CS101 - Algorithms Final",
    course_code="CS101",
    semester="Fall 2026"
)

print(f"Sample Excel sheet generated successfully at: {os.path.abspath(output_filename)}")
