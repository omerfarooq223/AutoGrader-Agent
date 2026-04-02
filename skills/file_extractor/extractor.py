"""
Extraction utilities for reading student submissions.
Supports: PDF, DOCX, .py, .cpp, .ipynb
"""

import json
import logging
import re
import zipfile
import os
import tempfile
from pathlib import Path

import fitz  # PyMuPDF
from docx import Document

import config

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".py", ".cpp", ".ipynb", ".md"}
MAX_FILE_SIZE_MB = 20  # Files larger than this are skipped to avoid huge prompt payloads

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

        # Image description is optional — fail fast, never retry
        # Gemini quota death causes 4-minute freezes if retry_api_call is used
        response = _call()
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

    # Extract table content — tables are not in doc.paragraphs
    table_parts: list[str] = []
    for table in doc.tables:
        for row in table.rows:
            row_text = " | ".join(
                cell.text.strip() for cell in row.cells if cell.text.strip()
            )
            if row_text:
                table_parts.append(row_text)

    all_parts = paragraphs + (["[Table Content]"] + table_parts if table_parts else [])
    text = "\n".join(all_parts)

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
            cell_part = f"[Code]\n{source}"
            # Include cell outputs — printed results, errors, and return values
            outputs = cell.get("outputs", [])
            output_texts = []
            for output in outputs:
                # Stream output (print statements)
                if output.get("output_type") in ("stream", "display_data"):
                    output_texts.append("".join(output.get("text", [])))
                # Execution result (last expression value)
                elif output.get("output_type") == "execute_result":
                    output_texts.append("".join(output.get("text", [])))
                # Errors and tracebacks
                elif output.get("output_type") == "error":
                    output_texts.append(
                        f"ERROR: {output.get('ename')}: {output.get('evalue')}"
                    )
            if output_texts:
                cell_part += f"\n[Output]\n{''.join(output_texts)}"
            parts.append(cell_part)

    return "\n\n".join(parts).strip()


_READERS = {
    ".pdf":   read_pdf,
    ".docx":  read_docx,
    ".py":    read_text_file,
    ".cpp":   read_text_file,
    ".ipynb": read_notebook,
    ".md":    read_text_file,
}


def read_file(file_path: str) -> str:
    """Read a single file based on its extension. Returns extracted text."""
    ext = Path(file_path).suffix.lower()
    reader = _READERS.get(ext)
    if reader is None:
        raise ValueError(f"Unsupported file format: {ext}")
    return reader(file_path)


def _parse_lms_path(file_path: str, base_dir: str) -> dict:
    """
    Extract student name and assignment metadata from LMS folder structure.

    Expected structure:
      <base>/<id>-<course>-<batch>-<section> - <semester>-<assignment>-<id>/
        <STUDENT NAME>_<number>_assignsubmission_file/
          actual_file.ext
    """
    rel = os.path.relpath(file_path, base_dir)
    parts = Path(rel).parts

    student_name = ""
    assignment_name = ""
    course_code = ""
    semester = ""

    for part in parts:
        # Student name folder: "ABDUL REHMAN NAEEM RIAZ_837796_assignsubmission_file"
        name_match = re.match(r"^([A-Za-z][A-Za-z\s]+?)_\d+_assignsubmission", part)
        if name_match:
            student_name = " ".join(name_match.group(1).split()).title()

        # Assignment name from parent folder
        assign_match = re.search(r"(Assignment\s+\d+)", part, re.IGNORECASE)
        if assign_match:
            assignment_name = assign_match.group(1).title()

        # Course code e.g. CC323
        course_match = re.search(r"-([A-Z]{2,6}\d{3})-", part)
        if course_match:
            course_code = course_match.group(1)

        # Semester e.g. F2025
        sem_match = re.search(r"[\s\-]((?:F|S|SP|FA)\d{4})[\s\-]", part, re.IGNORECASE)
        if sem_match:
            semester = sem_match.group(1).upper()

    return {
        "student_name":    student_name,
        "assignment_name": assignment_name,
        "course_code":     course_code,
        "semester":        semester,
    }


def _extract_nested_archives(directory: str, max_passes: int = 3) -> None:
    """
    Extract .zip files found inside the extracted LMS bundle.
    Handles "ZIP within ZIP" submissions by recursively unpacking a few levels.
    """
    for _ in range(max_passes):
        extracted_any = False

        for root, _dirs, files in os.walk(directory):
            if any(part.startswith(".") or part.startswith("__") for part in Path(root).parts):
                continue

            for filename in files:
                if Path(filename).suffix.lower() != ".zip":
                    continue

                zip_path = os.path.join(root, filename)
                target_dir = os.path.join(root, f"{Path(filename).stem}_nested_zip")
                if os.path.exists(target_dir):
                    continue

                try:
                    extract_zip(zip_path, target_dir)
                    extracted_any = True
                    logger.info("Extracted nested ZIP: %s", zip_path)
                except Exception as e:
                    logger.warning("Skipping invalid nested ZIP %s: %s", zip_path, e)

        if not extracted_any:
            break


def _student_group_key(file_path: str, base_dir: str, lms_meta: dict) -> str:
    """Derive a stable grouping key so each student folder becomes one submission."""
    rel_parts = Path(os.path.relpath(file_path, base_dir)).parts

    for part in rel_parts:
        if re.match(r"^.+?_\d+_assignsubmission", part, re.IGNORECASE):
            return part

    if lms_meta.get("student_name"):
        return f"student::{lms_meta['student_name'].lower()}"

    if len(rel_parts) >= 2:
        if re.search(r"assignment|submissions?|class|course|section|semester", rel_parts[0], re.IGNORECASE):
            return rel_parts[1]
        return rel_parts[0]

    return "root_submission"


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
    list[dict] — one combined submission per student folder.
    """
    exclude_set = {f.lower() for f in (exclude_filenames or [])}
    grouped: dict[str, dict] = {}

    # First expand any student-provided archive uploads (ZIP inside ZIP).
    _extract_nested_archives(directory)

    from utils.cache import _make_safe_key

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
            rel_path = os.path.relpath(full_path, directory)
            try:
                file_size_mb = os.path.getsize(full_path) / (1024 * 1024)
                if file_size_mb > MAX_FILE_SIZE_MB:
                    logger.warning(
                        "Skipping %s — file size %.1fMB exceeds limit of %dMB",
                        filename, file_size_mb, MAX_FILE_SIZE_MB,
                    )
                    content = f"[SKIPPED: File too large ({file_size_mb:.1f}MB). Student must resubmit.]"
                    error = "file_too_large"
                else:
                    content = read_file(full_path)
                    error = None
            except Exception as e:
                logger.error("Failed to read %s: %s", filename, e)
                content = "[ERROR: File could not be read. Student must resubmit.]"
                error = "read_failed"

            lms_meta = _parse_lms_path(full_path, directory)
            group_key = _student_group_key(full_path, directory, lms_meta)
            group = grouped.setdefault(
                group_key,
                {
                    "files": [],
                    "lms_meta": lms_meta,
                },
            )

            # Prefer richer LMS metadata if a later file has populated fields.
            if not group["lms_meta"].get("student_name") and lms_meta.get("student_name"):
                group["lms_meta"] = lms_meta

            group["files"].append(
                {
                    "filename": filename,
                    "path": full_path,
                    "rel_path": rel_path,
                    "content": content,
                    "error": error,
                }
            )

    submissions: list[dict] = []
    for group_key in sorted(grouped):
        group = grouped[group_key]
        files = sorted(group["files"], key=lambda f: f["rel_path"].lower())
        lms_meta = group["lms_meta"]

        combined_parts = []
        combined_error = None
        skipped_files = []
        for item in files:
            if item.get("error"):
                # Exclude placeholder errors from graded submission content.
                combined_error = combined_error or item["error"]
                skipped_files.append(item["filename"])
                continue
            combined_parts.append(
                f"\n\n===== FILE: {item['rel_path']} =====\n{item['content']}"
            )

        student_label = lms_meta.get("student_name") or group_key
        combined_filename = student_label  # Clean name — no suffix needed
        combined_content = "".join(combined_parts).strip()

        # If all files failed, store a clear error message as content
        if not combined_content and (combined_error or skipped_files):
            file_list = ", ".join(skipped_files) if skipped_files else "all files"
            combined_content = (
                f"[ERROR: Could not read submission. "
                f"Failed files: {file_list}. "
                f"Likely scanned images — student must resubmit as searchable PDF.]"
            )

        cache_key = _make_safe_key(combined_filename, combined_content)

        submissions.append(
            {
                "filename":     combined_filename,
                "path":         files[0]["path"],
                "content":      combined_content,
                "cache_key":    cache_key,
                "lms_meta":     lms_meta,
                "source_files": [f["path"] for f in files],
                **({"error": combined_error} if combined_error else {}),
            }
        )

    return submissions


def extract_and_collect(
    zip_path: str,
    exclude_filenames: list[str] | None = None,
) -> tuple[list[dict], str]:
    """
    Extract a ZIP file and collect all supported submissions.

    Returns (submissions, extract_dir) — caller is responsible for
    cleaning up extract_dir after grading completes. This preserves
    the cache file for crash recovery during long grading sessions.

    Parameters
    ----------
    zip_path          : Path to the ZIP file.
    exclude_filenames : Optional filenames to exclude (e.g. answer key filename).
    """
    extract_dir = extract_zip(zip_path)
    submissions = collect_submissions(extract_dir, exclude_filenames=exclude_filenames)
    return submissions, extract_dir