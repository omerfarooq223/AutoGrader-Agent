# AutoGrader JavaScript Web UI

This is the recommended browser interface for AutoGrader. It uses a light, teacher-friendly JavaScript frontend and a small local Python API server.

## Start

From the project root:

```bash
python web_ui/server.py
```

Activate the project's virtual environment first, or use its interpreter directly:

```bash
.venv/bin/python web_ui/server.py
```

This command starts both the frontend and the local backend. No separate frontend server is needed. Keep it running while you use the app.

Open **[AutoGrader](http://127.0.0.1:8765/)**. For first-time dependency installation and API-key setup, see the [main README](../README.md#quick-start).

## Workflows

### Grade Assignments

1. Upload the submissions ZIP.
2. Upload the assignment brief.
3. Optionally upload a roster and answer key.
4. Paste your own JSON, table, or plain-text rubric and select **Validate & Preview**, or select **Generate Rubric** to create one from the brief. Review criteria and total marks; edit and validate again as needed. A rubric is required before grading starts.
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
