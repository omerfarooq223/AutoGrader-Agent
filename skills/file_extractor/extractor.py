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
    """Send an image to Gemini's vision model to bypass strict Groq quotas."""
    try:
        import os
        from google import genai
        from google.genai import types
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return None
        os.environ.pop("GOOGLE_API_KEY", None)
        client = genai.Client(api_key=api_key)
        model_name = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")

        def _call():
            return client.models.generate_content(
                model=model_name,
                contents=[
                    _VISION_PROMPT,
                    types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
                ]
            )

        response = retry_api_call(_call)
        return response.text.strip()
    except Exception as e:
        logger.debug(f"Vision API call failed for an image: {e}", exc_info=True)
        return None



def extract_zip(zip_path: str, extract_to: str | None = None) -> str:
    """Extract a ZIP file and return the path to the extraction directory."""
    if extract_to is None:
        extract_to = tempfile.mkdtemp(prefix="submissions_")

    with zipfile.ZipFile(zip_path, "r") as zf:
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

            if config.EXTRACT_IMAGES:
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

    if config.EXTRACT_IMAGES:
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

    return "\n\n".join(parts).strip()


_READERS = {
    ".pdf":   read_pdf,
    ".docx":  read_docx,
    ".py":    read_text_file,
    ".cpp":   read_text_file,
    ".ipynb": read_notebook,
}


def read_file(file_path: str) -> str:
    """Read a single file based on its extension. Returns extracted text."""
    ext = Path(file_path).suffix.lower()
    reader = _READERS.get(ext)
    if reader is None:
        raise ValueError(f"Unsupported file format: {ext}")
    return reader(file_path)


def collect_submissions(
    directory: str,
    exclude_filenames: list[str] | None = None,
) -> list[dict]:
    """
    Walk through an extracted submissions directory and read every
    supported file.

    Parameters
    ----------
    directory         : Path to the extracted ZIP directory.
    exclude_filenames : Optional list of filenames to skip (e.g. answer key).

    Returns
    -------
    list[dict] — [{"filename": "...", "path": "...", "content": "..."}, ...]
    """
    exclude_set = {f.lower() for f in (exclude_filenames or [])}
    submissions: list[dict] = []

    for root, _dirs, files in os.walk(directory):
        # Skip hidden / system directories
        if any(
            part.startswith(".") or part.startswith("__")
            for part in Path(root).parts
        ):
            continue

        for filename in sorted(files):
            ext = Path(filename).suffix.lower()
            if ext not in SUPPORTED_EXTENSIONS:
                continue

            # Skip answer key or any other excluded file
            if filename.lower() in exclude_set:
                logger.info("Skipping excluded file: %s", filename)
                continue

            full_path = os.path.join(root, filename)
            try:
                content = read_file(full_path)
            except Exception as e:
                content = f"[ERROR reading file: {e}]"

            submissions.append({
                "filename": filename,
                "path":     full_path,
                "content":  content,
            })

    return submissions


def extract_and_collect(
    zip_path: str,
    exclude_filenames: list[str] | None = None,
) -> list[dict]:
    """
    Convenience function: extract a ZIP file, collect and read all supported
    submissions inside it, then clean up the temp directory.

    Parameters
    ----------
    zip_path          : Path to the ZIP file.
    exclude_filenames : Optional filenames to exclude (e.g. answer key filename).
    """
    extract_dir = extract_zip(zip_path)
    try:
        return collect_submissions(extract_dir, exclude_filenames=exclude_filenames)
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)