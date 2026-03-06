"""
Extraction utilities for reading student submissions.
Supports: PDF, DOCX, .py, .cpp, .ipynb
"""

import json
import shutil
import zipfile
import os
import tempfile
from pathlib import Path

import fitz  # PyMuPDF
from docx import Document


SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".py", ".cpp", ".ipynb"}


def extract_zip(zip_path: str, extract_to: str | None = None) -> str:
    """Extract a ZIP file and return the path to the extraction directory."""
    if extract_to is None:
        extract_to = tempfile.mkdtemp(prefix="submissions_")

    with zipfile.ZipFile(zip_path, "r") as zf:
        # Guard against zip-slip: reject entries with absolute paths or '..'
        for member in zf.namelist():
            member_path = os.path.normpath(member)
            if member_path.startswith("..") or os.path.isabs(member_path):
                raise ValueError(f"Unsafe path in ZIP archive: {member}")
        zf.extractall(extract_to)

    return extract_to


def read_pdf(file_path: str) -> str:
    """Extract text from a PDF file using PyMuPDF."""
    text_parts: list[str] = []
    with fitz.open(file_path) as doc:
        for page in doc:
            page_text = page.get_text()
            if page_text:
                text_parts.append(page_text)
    return "\n".join(text_parts).strip()


def read_docx(file_path: str) -> str:
    """Extract text from a DOCX file using python-docx."""
    doc = Document(file_path)
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n".join(paragraphs).strip()


def read_text_file(file_path: str) -> str:
    """Read plain-text source files (.py, .cpp)."""
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        return f.read().strip()


def read_notebook(file_path: str) -> str:
    """Extract code and markdown cell contents from a Jupyter notebook (.ipynb)."""
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        notebook = json.load(f)

    cells = notebook.get("cells", [])
    parts: list[str] = []

    for cell in cells:
        cell_type = cell.get("cell_type", "")
        source_lines = cell.get("source", [])
        source = "".join(source_lines).strip()

        if not source:
            continue

        if cell_type == "markdown":
            parts.append(f"[Markdown]\n{source}")
        elif cell_type == "code":
            parts.append(f"[Code]\n{source}")
        # Skip raw / other cell types

    return "\n\n".join(parts).strip()


# Map extensions to their reader functions
_READERS = {
    ".pdf": read_pdf,
    ".docx": read_docx,
    ".py": read_text_file,
    ".cpp": read_text_file,
    ".ipynb": read_notebook,
}


def read_file(file_path: str) -> str:
    """Read a single file based on its extension. Returns extracted text."""
    ext = Path(file_path).suffix.lower()
    reader = _READERS.get(ext)
    if reader is None:
        raise ValueError(f"Unsupported file format: {ext}")
    return reader(file_path)


def collect_submissions(directory: str) -> list[dict]:
    """
    Walk through an extracted submissions directory and read every
    supported file.

    Returns a list of dicts:
        [{"filename": "...", "path": "...", "content": "..."}, ...]
    """
    submissions: list[dict] = []

    for root, _dirs, files in os.walk(directory):
        # Skip hidden directories (e.g., __MACOSX)
        if any(part.startswith(".") or part.startswith("__") for part in Path(root).parts):
            continue

        for filename in sorted(files):
            ext = Path(filename).suffix.lower()
            if ext not in SUPPORTED_EXTENSIONS:
                continue

            full_path = os.path.join(root, filename)
            try:
                content = read_file(full_path)
            except Exception as e:
                content = f"[ERROR reading file: {e}]"

            submissions.append({
                "filename": filename,
                "path": full_path,
                "content": content,
            })

    return submissions


def extract_and_collect(zip_path: str) -> list[dict]:
    """
    Convenience function: extract a ZIP file, then collect and read
    all supported submissions inside it. Cleans up the temp directory afterwards.
    """
    extract_dir = extract_zip(zip_path)
    try:
        return collect_submissions(extract_dir)
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)
