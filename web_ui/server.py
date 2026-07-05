"""
AutoGrader Web UI server.

Runs a small local HTTP API and serves the JavaScript frontend from
web_ui/static. The existing Python grading engine remains the source of truth.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import shutil
import sys
import tempfile
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config
from skills.file_extractor.extractor import extract_and_collect, load_student_roster, read_file
from skills.grader.grader_agent import grade_all
from skills.plagiarism_detector.plagiarism_agent import apply_flags_and_penalty, check_plagiarism
from skills.report_writer.excel_writer import write_results
from skills.rubric_generator.rubric_agent import format_rubric_to_json, generate_rubric
from skills.viva_generator.viva_agent import generate_viva_questions
from utils.cache import clear_cache, load_cache, save_cache

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
RUN_DIR = Path(tempfile.gettempdir()) / "autograder_web_jobs"
RUN_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("autograder_web")


@dataclass
class Job:
    id: str
    kind: str
    status: str = "queued"
    message: str = "Queued"
    progress: int = 0
    result: dict = field(default_factory=dict)
    error: str = ""
    work_dir: Path | None = None


JOBS: dict[str, Job] = {}
JOBS_LOCK = threading.Lock()


def _json_response(handler: BaseHTTPRequestHandler, payload: dict, status: int = 200) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _set_job(job_id: str, **changes) -> None:
    with JOBS_LOCK:
        job = JOBS[job_id]
        for key, value in changes.items():
            setattr(job, key, value)


def _get_job(job_id: str) -> Job | None:
    with JOBS_LOCK:
        return JOBS.get(job_id)


def _safe_filename(name: str, fallback: str) -> str:
    cleaned = Path(name or fallback).name.strip()
    cleaned = "".join(ch for ch in cleaned if ch.isalnum() or ch in "._- ()")
    return cleaned or fallback


def _parse_multipart(headers, body: bytes) -> tuple[dict[str, str], dict[str, dict]]:
    content_type = headers.get("Content-Type", "")
    message = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + body
    )
    fields: dict[str, str] = {}
    files: dict[str, dict] = {}

    for part in message.iter_parts():
        disposition = part.get("Content-Disposition", "")
        if "form-data" not in disposition:
            continue
        name = part.get_param("name", header="Content-Disposition")
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if not name:
            continue
        if filename:
            files[name] = {
                "filename": _safe_filename(filename, "upload.bin"),
                "content": payload,
            }
        else:
            charset = part.get_content_charset() or "utf-8"
            fields[name] = payload.decode(charset, errors="replace")
    return fields, files


def _save_upload(files: dict[str, dict], key: str, directory: Path) -> Path | None:
    item = files.get(key)
    if not item or not item.get("content"):
        return None
    path = directory / item["filename"]
    path.write_bytes(item["content"])
    return path


def _parse_float_field(fields: dict[str, str], key: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(fields.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _run_grading_job(job_id: str, fields: dict[str, str], files: dict[str, dict]) -> None:
    job_dir = RUN_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    extract_dir = None

    try:
        _set_job(job_id, status="running", message="Saving uploads", progress=5, work_dir=job_dir)
        zip_path = _save_upload(files, "submissions_zip", job_dir)
        brief_path = _save_upload(files, "assignment_brief", job_dir)
        roster_path = _save_upload(files, "student_roster", job_dir)
        answer_key_path = _save_upload(files, "answer_key_file", job_dir)

        if not zip_path or not brief_path:
            raise ValueError("Please upload both a submissions ZIP and an assignment brief.")

        _set_job(job_id, message="Reading assignment brief", progress=12)
        brief_text = read_file(str(brief_path))

        manual_rubric = fields.get("manual_rubric", "").strip()
        if manual_rubric:
            rubric = format_rubric_to_json(manual_rubric)
            _set_job(job_id, message="Using teacher-provided rubric", progress=20)
        else:
            raise ValueError("Please generate, paste, or approve a rubric before starting grading.")

        answer_key = fields.get("answer_key_text", "").strip() or None
        if answer_key_path:
            _set_job(job_id, message="Reading answer key", progress=24)
            answer_key = read_file(str(answer_key_path))

        roster = None
        if roster_path:
            _set_job(job_id, message="Reading student roster", progress=28)
            roster = load_student_roster(str(roster_path))

        _set_job(job_id, message="Extracting submissions", progress=35)
        submissions, extract_dir = extract_and_collect(str(zip_path), student_roster=roster)
        if not submissions:
            raise ValueError("No readable supported submissions were found in the ZIP.")

        cache_dir = job_dir / ".cache"
        cache_dir.mkdir(exist_ok=True)
        cached = load_cache(str(cache_dir))
        total = len(submissions)

        _set_job(job_id, message=f"Grading {total} submission(s)", progress=45)

        def on_complete(filename: str, result: dict) -> None:
            key = result.get("cache_key", filename)
            cached[key] = result
            save_cache(str(cache_dir), cached)
            done = len(cached)
            progress = 45 + int((done / max(total, 1)) * 35)
            _set_job(job_id, message=f"Graded {done}/{total} submission(s)", progress=min(progress, 80))

        results = grade_all(
            rubric,
            submissions,
            cached=cached,
            on_complete=on_complete,
            answer_key=answer_key,
        )

        threshold_percent = _parse_float_field(fields, "plagiarism_threshold", 65.0, 0.0, 100.0)
        penalty_marks = _parse_float_field(fields, "plagiarism_penalty", 0.0, 0.0, 1000.0)

        _set_job(job_id, message=f"Checking similarity at {threshold_percent:g}% threshold", progress=84)
        flags = check_plagiarism(submissions, results, threshold=threshold_percent)
        results = apply_flags_and_penalty(results, flags, penalty_marks=penalty_marks)
        flagged = sum(1 for item in results if item.get("plagiarism_flag"))

        _set_job(job_id, message="Writing Excel report", progress=92)
        output_path = job_dir / "grading_report.xlsx"
        write_results(
            results,
            str(output_path),
            assignment_name=fields.get("assignment_name", "").strip(),
            course_code=fields.get("course_code", "").strip(),
            semester=fields.get("semester", "").strip(),
        )
        clear_cache(str(cache_dir))

        _set_job(
            job_id,
            status="done",
            message="Report ready",
            progress=100,
            result={
                "download_url": f"/api/jobs/{job_id}/download",
                "students": len(results),
                "plagiarism_flags": flagged,
                "plagiarism_threshold": threshold_percent,
                "plagiarism_penalty": penalty_marks,
                "rubric_generated": False,
            },
        )
    except Exception as exc:
        logger.error("Grading job failed: %s\n%s", exc, traceback.format_exc())
        _set_job(job_id, status="error", message="Grading failed", error=str(exc), progress=100)
    finally:
        if extract_dir and Path(extract_dir).exists():
            shutil.rmtree(extract_dir, ignore_errors=True)


def _run_viva_job(job_id: str, fields: dict[str, str], files: dict[str, dict]) -> None:
    job_dir = RUN_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    try:
        _set_job(job_id, status="running", message="Reading project document", progress=20, work_dir=job_dir)
        project_path = _save_upload(files, "project_document", job_dir)
        if not project_path:
            raise ValueError("Please upload a project proposal or report.")

        document_text = read_file(str(project_path))
        _set_job(job_id, message="Generating viva questions", progress=55)
        data = generate_viva_questions(
            document_text,
            project_name=fields.get("project_name", "").strip(),
            difficulty=fields.get("difficulty", "mixed"),
            question_count=int(fields.get("question_count", "12") or 12),
        )

        _set_job(
            job_id,
            status="done",
            message="Viva questions ready",
            progress=100,
            result=data,
        )
    except Exception as exc:
        logger.error("Viva job failed: %s\n%s", exc, traceback.format_exc())
        _set_job(job_id, status="error", message="Viva generation failed", error=str(exc), progress=100)


def _run_rubric_job(job_id: str, fields: dict[str, str], files: dict[str, dict]) -> None:
    job_dir = RUN_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    try:
        _set_job(job_id, status="running", message="Reading assignment brief", progress=25, work_dir=job_dir)
        brief_path = _save_upload(files, "assignment_brief", job_dir)
        if not brief_path:
            raise ValueError("Please upload an assignment brief before generating a rubric.")

        brief_text = read_file(str(brief_path))
        _set_job(job_id, message="Generating editable rubric", progress=65)
        rubric = generate_rubric(brief_text)

        _set_job(
            job_id,
            status="done",
            message="Rubric ready for review",
            progress=100,
            result={"rubric": rubric},
        )
    except Exception as exc:
        logger.error("Rubric generation failed: %s\n%s", exc, traceback.format_exc())
        _set_job(job_id, status="error", message="Rubric generation failed", error=str(exc), progress=100)


def _rubric_preview_payload(rubric: str) -> dict:
    data = json.loads(rubric)
    criteria = data.get("criteria", [])
    total = sum(float(item.get("max_score", 0) or 0) for item in criteria)
    return {
        "rubric": rubric,
        "criteria": criteria,
        "total": total,
        "count": len(criteria),
    }


def _create_job(kind: str, runner, fields: dict[str, str], files: dict[str, dict]) -> str:
    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[job_id] = Job(id=job_id, kind=kind)
    thread = threading.Thread(target=runner, args=(job_id, fields, files), daemon=True)
    thread.start()
    return job_id


class AutoGraderHandler(BaseHTTPRequestHandler):
    server_version = "AutoGraderWeb/1.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path.startswith("/api/jobs/"):
            self._handle_job_get(path)
            return

        if path == "/":
            path = "/index.html"
        static_path = (STATIC_DIR / path.lstrip("/")).resolve()
        if not str(static_path).startswith(str(STATIC_DIR.resolve())) or not static_path.exists():
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return

        content = static_path.read_bytes()
        mime_type, _ = mimetypes.guess_type(str(static_path))
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path not in {"/api/grade", "/api/viva", "/api/rubric", "/api/rubric/validate"}:
            self.send_error(HTTPStatus.NOT_FOUND, "Endpoint not found")
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            fields, files = _parse_multipart(self.headers, body)
            if parsed.path == "/api/grade":
                job_id = _create_job("grading", _run_grading_job, fields, files)
            elif parsed.path == "/api/rubric":
                job_id = _create_job("rubric", _run_rubric_job, fields, files)
            elif parsed.path == "/api/rubric/validate":
                rubric_text = fields.get("manual_rubric", "").strip()
                if not rubric_text:
                    raise ValueError("Paste or generate a rubric before validating it.")
                rubric = format_rubric_to_json(rubric_text)
                _json_response(self, _rubric_preview_payload(rubric))
                return
            else:
                job_id = _create_job("viva", _run_viva_job, fields, files)
            _json_response(self, {"job_id": job_id})
        except Exception as exc:
            _json_response(self, {"error": str(exc)}, status=400)

    def _handle_job_get(self, path: str) -> None:
        parts = path.strip("/").split("/")
        if len(parts) < 3:
            self.send_error(HTTPStatus.NOT_FOUND, "Job not found")
            return
        job_id = parts[2]
        job = _get_job(job_id)
        if not job:
            self.send_error(HTTPStatus.NOT_FOUND, "Job not found")
            return

        if len(parts) == 4 and parts[3] == "download":
            output_path = (job.work_dir / "grading_report.xlsx") if job.work_dir else None
            if not output_path or not output_path.exists():
                self.send_error(HTTPStatus.NOT_FOUND, "Report not found")
                return
            content = output_path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header(
                "Content-Type",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            self.send_header("Content-Disposition", 'attachment; filename="grading_report.xlsx"')
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        _json_response(
            self,
            {
                "id": job.id,
                "kind": job.kind,
                "status": job.status,
                "message": job.message,
                "progress": job.progress,
                "result": job.result,
                "error": job.error,
            },
        )

    def log_message(self, fmt: str, *args) -> None:
        logger.info("%s - %s", self.address_string(), fmt % args)


def run(host: str = "127.0.0.1", port: int = 8765) -> None:
    server = ThreadingHTTPServer((host, port), AutoGraderHandler)
    print(f"AutoGrader web UI running at http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    run(
        host=os.environ.get("AUTOGRADER_WEB_HOST", "127.0.0.1"),
        port=int(os.environ.get("AUTOGRADER_WEB_PORT", "8765")),
    )
