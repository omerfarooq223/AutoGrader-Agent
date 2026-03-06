# File Extractor — Skill Instructions

## Purpose
Extracts and reads the content of student submissions from a ZIP archive. Supports 5 file formats: PDF, DOCX, .py, .cpp, and .ipynb.

## When to Invoke
- At the start of the pipeline, to load both the assignment brief and all student submissions.

## Inputs
| Input | Type | Source |
|-------|------|--------|
| `zip_path` | `str` | Path to the submissions ZIP file |
| `file_path` | `str` | Path to a single file (for the assignment brief) |

## Outputs
| Output | Type | Description |
|--------|------|-------------|
| `submissions` | `list[dict]` | Each entry: `filename`, `path`, `content` (extracted text) |
| `text` | `str` | Extracted text from a single file |

## Supported Formats
| Extension | Library | Strategy |
|-----------|---------|----------|
| `.pdf` | PyMuPDF (`fitz`) | Page-by-page text extraction + embedded image descriptions via Groq Vision |
| `.docx` | `python-docx` | Paragraph text extraction + media image descriptions via Groq Vision |
| `.py` | stdlib | Plain UTF-8 read |
| `.cpp` | stdlib | Plain UTF-8 read |
| `.ipynb` | stdlib `json` | Parse JSON, extract `[Code]` and `[Markdown]` cells |

## Image Extraction
- PDF and DOCX embedded images are extracted and sent to `meta-llama/llama-4-scout-17b-16e-instruct` via Groq's vision API.
- Each description is appended as `[Image: <description>]` in the extracted text.
- Uses `utils/retry.py` for exponential backoff on API failures.
- Any failure (extraction or API) is caught silently — never crashes the pipeline.

## Security
- **Zip-slip protection**: rejects archive entries with `..` or absolute paths.
- Skips hidden directories (`__MACOSX`, `.git`, etc.).
- Uses `errors="replace"` for text encoding — never crashes on bad bytes.

## Key Functions
- `_describe_image(image_bytes)` — send image to Groq Vision, return description or `None`
- `extract_zip(zip_path)` — unzip with safety checks
- `read_file(file_path)` — dispatch to format-specific reader
- `collect_submissions(directory)` — walk directory, read all supported files
- `extract_and_collect(zip_path)` — one-call convenience wrapper

## Dependencies
- `PyMuPDF` (`fitz`)
- `python-docx`
- `groq` (vision API client)
- `config.py` (API key)
- `utils/retry.py` (exponential backoff)
