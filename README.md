# AutoGrader

AI-powered grading agent that automates assignment evaluation using LLMs. Available as both a **CLI tool** and a **Streamlit web UI**.

## What It Does

- Accepts a **ZIP of student submissions** + an **assignment brief**
- Generates a **structured grading rubric** via LLM (Groq / LLaMA 3.3 70B) as validated JSON with per-criterion scoring — uses **rubric templates** when a matching assignment type is detected, human approval required
- **Grades each submission** against the rubric — scores every criterion individually, then sums to a total with auditable subscores; **auto-corrects** if the LLM's total doesn't match the category sum
- **Detects plagiarism** using dual similarity analysis (TF-IDF cosine + character n-gram)
- Outputs a styled **Excel report** with per-category breakdown, class statistics, and **LLM-generated class insights** (top 3 common mistakes)

## Project Structure

```
AutoGrader/
├── main.py                          # CLI entry point — orchestrates the full pipeline
├── app.py                           # Streamlit web UI
├── config.py                        # Centralized settings (.env loader)
├── requirements.txt                 # Python dependencies
├── .env.example                     # Environment variable template
├── README.md                        # This file
├── LICENSE                          # MIT License
│
├── .streamlit/
│   └── config.toml                  # Streamlit theme configuration
│
├── docs/
│   └── workflow.md                  # Detailed pipeline documentation
│
├── tests/
│   └── test_autograder.py           # Test suite (pytest)
│
├── utils/                           # Shared utilities
│   ├── cache.py                     # Crash-recovery grading cache
│   └── retry.py                     # Exponential backoff for API calls
│
├── rubrics/                         # Rubric templates for common assignment types
│   ├── programming_assignment.json  # Correctness, Code Quality, Documentation, Testing
│   └── essay_assignment.json        # Argument, Evidence, Structure, Clarity
│
└── skills/                          # Core agent skills
    ├── rubric_generator/
    │   ├── SKILL.md                 # Agent instructions
    │   └── rubric_agent.py          # LLM rubric generation + approval loop
    ├── grader/
    │   ├── SKILL.md                 # Agent instructions
    │   └── grader_agent.py          # Concurrent LLM grading engine
    ├── plagiarism_detector/
    │   ├── SKILL.md                 # Agent instructions
    │   └── plagiarism_agent.py      # Dual similarity analysis
    ├── file_extractor/
    │   ├── SKILL.md                 # Agent instructions
    │   └── extractor.py             # ZIP extraction + 5-format readers
    └── report_writer/
        ├── SKILL.md                 # Agent instructions
        └── excel_writer.py          # Styled Excel report generator
```

## Supported File Formats

| Format | Library | Image Support |
|--------|---------|---------------|
| PDF | PyMuPDF | Yes — embedded images extracted and described via Groq Vision |
| DOCX | python-docx | Yes — media images extracted and described via Groq Vision |
| .py | stdlib | — |
| .cpp | stdlib | — |
| .ipynb | stdlib JSON | — |

### Image Extraction

Embedded images in PDF and DOCX files are automatically extracted and sent to Groq's vision model (`meta-llama/llama-4-scout-17b-16e-instruct`) for description. Each description is appended to the document text as `[Image: <description>]`, giving the grading LLM full visibility into diagrams, charts, code output screenshots, and handwritten content. If the vision API fails for any image, it is skipped silently — extraction never crashes.

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set up your API key
cp .env.example .env
# Edit .env and add your GROQ_API_KEY

# 3a. Run via CLI
python main.py submissions.zip assignment_brief.pdf

# 3b. Run via Web UI
streamlit run app.py
```

## Web UI

The Streamlit interface (`app.py`) provides a browser-based alternative to the CLI with a guided 4-step workflow:

1. **Upload** — Drag-and-drop your submissions ZIP and assignment brief (PDF/DOCX)
2. **Rubric** — Auto-generates a grading rubric from the brief; review, edit, or approve it
3. **Grade** — Runs concurrent grading + plagiarism detection with a live progress bar
4. **Results** — View a summary table and download the full Excel report

The UI calls the same underlying skill modules as the CLI — no logic is duplicated.

```bash
# Start the web UI
streamlit run app.py

# Or specify the full path if streamlit isn't on PATH
.venv/bin/streamlit run app.py
```

## Configuration

All settings are in `.env` (see `.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `GROQ_API_KEY` | — | Your Groq API key |
| `MODEL` | `llama-3.3-70b-versatile` | LLM model to use |
| `MAX_CONCURRENT_GRADES` | `4` | Parallel grading workers |
| `SIMILARITY_THRESHOLD` | `0.65` | Plagiarism flag threshold |
| `TOTAL_MARKS` | `100` | Maximum marks for grading |
| `PASS_THRESHOLD` | `50` | Pass/fail cutoff |

## Output

Generates `grading_report.xlsx` with two sheets:

1. **Grading Report** — Name, ID, Marks, Per-Criterion Scores, Deductions, Feedback, Plagiarism Flag
2. **Summary Statistics** — Average, Median, Std Dev, Pass Rate, Grade Distribution (A–F), and a **Class Insights** section listing the top 3 most common mistakes across all students (generated via an additional LLM call)

## Tech Stack

- **LLM**: Groq API → LLaMA 3.3 70B Versatile (128K context)
- **Vision**: Groq API → LLaMA 4 Scout 17B (image understanding for embedded images)
- **Plagiarism**: scikit-learn TF-IDF + custom n-gram Jaccard
- **Reports**: openpyxl with conditional formatting
- **CLI UX**: Rich (progress bars, styled logging)
- **Web UI**: Streamlit (interactive browser-based interface)

## Author

Muhammad Umar Farooq — [GitHub](https://github.com/omerfarooq223)
