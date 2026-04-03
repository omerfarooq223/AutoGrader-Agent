# AutoGrader

AI-powered grading agent that automates assignment evaluation using LLMs. Available as both a **CLI tool** and a **Streamlit web UI**.

## What It Does

- Accepts a **ZIP of student submissions** + an **assignment brief**
- **Generates a structured grading rubric** via LLM (Groq / Gemini) — supports auto-detection from templates or **automatic AI-formatting of pasted text** (pasted raw rubrics are converted to JSON instantly)
- Lets you provide or generate an **answer key/solution** (manual, file upload, or LLM-generated) for accurate grading
- **Grades each submission** against the rubric — LLM evaluates each criterion and provides a score with a brief reason; **all math is done by Python** (totals, deduction amounts, deduction text formatting, score capping)
- **Detects plagiarism** using dual similarity analysis (TF-IDF cosine + character n-gram) — can be toggled on/off in the sidebar
- Outputs a styled **Excel report** with per-criterion breakdown, class statistics, and class insights (top 3 common mistakes)

## Demo

Below is a demo of the AutoGrader Streamlit UI in action:

![Demo of AutoGrader UI](demo.mp4)

## Project Structure

```
AutoGrader/
├── main.py                          # CLI entry point — orchestrates the full pipeline
├── app.py                           # Streamlit web UI
├── config.py                        # Centralized settings (.env loader)
├── requirements.txt                 # Python dependencies
├── .env                             # Local Secrets (Gitignored)
├── .env.example                     # Environment variable template
├── README.md                        # This file
├── LICENSE                          # MIT License
├── demo.mp4                         # UI Demonstration
├── .streamlit/
│   └── config.toml                  # Streamlit theme configuration
├── docs/
│   └── workflow.md                  # Detailed pipeline documentation
├── scripts/
│   └── dev_generate_sample_excel.py # Dev utility: generate sample Excel preview report
├── tests/
│   └── test_autograder.py           # Test suite (pytest)
├── utils/                           # Shared utilities
│   ├── cache.py                     # Crash-recovery grading cache
│   ├── llm_client.py                # Redundant LLM dual-routing API wrapper
│   └── retry.py                     # Exponential backoff for API calls
├── rubrics/                         # Rubric templates for common assignment types
│   ├── programming_assignment.json  # Correctness, Code Quality, etc.
│   └── essay_assignment.json        # Argument, Evidence, etc.
└── skills/                          # Core agent skills
    ├── rubric_generator/
    │   ├── SKILL.md                 # Agent instructions (Self-Correcting Rubric)
    │   └── rubric_agent.py          # LLM rubric generation + approval loop
    ├── grader/
    │   ├── SKILL.md                 # Agent instructions (Concurrency & Accuracy)
    │   └── grader_agent.py          # Concurrent LLM grading engine
    ├── plagiarism_detector/
    │   ├── SKILL.md                 # Agent instructions (Similarity Logic)
    │   └── plagiarism_agent.py      # Dual similarity analysis
    ├── file_extractor/
    │   ├── SKILL.md                 # Agent instructions (Parsing Logic)
    │   └── extractor.py             # ZIP extraction + 5-format readers
    └── report_writer/
        ├── SKILL.md                 # Agent instructions (Export Logic)
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

Embedded images in PDF and DOCX files can be automatically extracted and sent to Gemini's vision model for description by enabling `EXTRACT_IMAGES=True` in your `.env`. Each description is appended to the document text as `[Image: <description>]`, giving the grading LLM full visibility into diagrams, charts, code output screenshots, and handwritten content. If the vision API fails for any image, it is skipped silently — extraction never crashes.

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set up your API keys
cp .env.example .env
# Edit .env and add your GROQ_API_KEY and GEMINI_API_KEY

# 3a. Run via CLI
python main.py submissions.zip assignment_brief.pdf

# 3b. Run via Web UI
streamlit run app.py
```

## Web UI

The Streamlit interface (`app.py`) provides a browser-based alternative to the CLI with a guided 5-step workflow:

1. **Upload** — Drag-and-drop your submissions ZIP and assignment brief
2. **Rubric** — Auto-detects template, generates rubric via LLM, or lets you paste raw text which is **automatically formatted into JSON** upon entry
3. **Answer Key** — Provide manually, upload a file, or generate via LLM
4. **Grade** — Runs concurrent grading + plagiarism detection (controllable via a **sidebar toggle**) with a live progress bar
5. **Results** — View summary metric cards, score distribution and criterion-average charts, apply manual score overrides in the UI, then download the updated Excel report and review "Class Insights" (top 3 mistakes)

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
| `GROQ_API_KEY` | — | Your Groq API key (**Primary — recommended**) |
| `GEMINI_API_KEY` | — | Your Gemini API key (Secondary fallback) |
| `MODEL` | `llama-3.1-8b-instant` | Primary Groq model (higher free-tier quota) |
| `GEMINI_MODEL` | `gemini-2.0-flash` | Fallback Gemini model (unavailable in some regions) |
| `EXTRACT_IMAGES` | `False` | Turn on to extract and describe images |
| `MAX_CONCURRENT_GRADES` | `1` | Parallel grading workers |

## Tech Stack

- **LLM Engine**: Dual-Redundant Routing (**Groq Llama 3.1 8B Instant** as primary (Gemini fallback disabled in Pakistan))
- **Vision**: Gemini API (for diagram understanding in PDFs/DOCX)
- **Plagiarism**: Scikit-Learn (TF-IDF) + Character N-Gram Jaccard
- **Reports**: Styled Excel output with `openpyxl`
- **Frontend**: Streamlit 
- **CLI**: Rich-enhanced python scripting

## Author

Muhammad Umar Farooq — [GitHub](https://github.com/omerfarooq223)
