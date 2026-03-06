"""
Extraction utilities for reading student submissions.
Supports: PDF, DOCX, .py, .cpp, .ipynb
"""

import base64
import json
import logging
import shutil
import zipfile
import os
import tempfile
from pathlib import Path

import fitz  # PyMuPDF
from docx import Document
from groq import Groq

import config
from utils.retry import retry_api_call

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".py", ".cpp", ".ipynb"}

_VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"
_VISION_PROMPT = (
    "Describe what is shown in this image in the context of a student assignment. "
    "Be specific about any code output, charts, diagrams, or handwritten content you see."
)


def _describe_image(image_bytes: bytes) -> str | None:
    """Send an image to Groq's vision model and return the description."""
    try:
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        client = Groq(api_key=config.GROQ_API_KEY)

        def _call():
            return client.chat.completions.create(
                model=_VISION_MODEL,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{b64}",
                                },
                            },
                            {
                                "type": "text",
                                "text": _VISION_PROMPT,
                            },
                        ],
                    }
                ],
                max_tokens=512,
            )

        response = retry_api_call(_call)
        return response.choices[0].message.content.strip()
    except Exception:
        logger.debug("Vision API call failed for an image, skipping.", exc_info=True)
        return None


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
    """Extract text and embedded images from a PDF file using PyMuPDF."""
    text_parts: list[str] = []
    with fitz.open(file_path) as doc:
        for page in doc:
            page_text = page.get_text() or ""

            # Extract embedded images from the page
            for img_info in page.get_images(full=True):
                xref = img_info[0]
                try:
                    base_image = doc.extract_image(xref)
                    image_bytes = base_image["image"]
                    description = _describe_image(image_bytes)
                    if description:
                        page_text += f"\n[Image: {description}]"
                except Exception:
                    logger.debug("Failed to extract PDF image xref=%s, skipping.", xref, exc_info=True)

            if page_text.strip():
                text_parts.append(page_text)
    return "\n".join(text_parts).strip()


def read_docx(file_path: str) -> str:
    """Extract text and embedded images from a DOCX file using python-docx."""
    doc = Document(file_path)
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    text = "\n".join(paragraphs)

    # Extract images from the docx media folder
    try:
        for rel in doc.part.rels.values():
            if "image" in rel.reltype:
                try:
                    image_bytes = rel.target_part.blob
                    description = _describe_image(image_bytes)
                    if description:
                        text += f"\n[Image: {description}]"
                except Exception:
                    logger.debug("Failed to extract DOCX image, skipping.", exc_info=True)
    except Exception:
        logger.debug("Failed to iterate DOCX relationships, skipping images.", exc_info=True)

    return text.strip()


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
