# AutoGrader JavaScript Web UI

This is the recommended browser interface for AutoGrader. It uses a light, teacher-friendly JavaScript frontend and a small local Python API server.

## Start

From the project root:

```bash
python web_ui/server.py
```

If your default `python` does not have the project dependencies installed, run it with the same interpreter or virtual environment you use for tests, for example:

```bash
/usr/local/bin/python3 web_ui/server.py
```

Open:

```text
http://127.0.0.1:8765
```

## Workflows

### Grade Assignments

1. Upload the submissions ZIP.
2. Upload the assignment brief.
3. Optionally upload a roster and answer key.
4. Optionally paste a manual rubric.
5. Set the similarity policy:
   - `Flag threshold (%)` controls how similar two submissions must be before they are flagged.
   - `Marks to deduct if flagged` applies once per flagged student. Use `0` for report-only flags.
6. Start grading and download the Excel report.

The main class upload should be a ZIP file. Student folders inside it may contain nested `.zip`, `.rar`, or `.7z` submissions. ZIP works directly; RAR/7z requires a local extractor such as `bsdtar`/libarchive or `7z`.

### Viva Questions

1. Upload a project proposal or report.
2. Choose difficulty and number of questions.
3. Generate project-specific viva questions with teacher hints.

## Architecture

The UI does not rewrite grading logic in JavaScript. It calls the existing Python modules:

- `skills/file_extractor`
- `skills/rubric_generator`
- `skills/grader`
- `skills/plagiarism_detector`
- `skills/report_writer`
- `skills/viva_generator`

Long-running tasks are handled as background jobs and polled from the browser.
