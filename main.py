"""
AutoGrader — AI-powered grading agent.

Usage:
    python main.py <submissions.zip> <assignment_brief_file>

Features:
    - LLM rubric generation with approval workflow
    - Concurrent grading with per-category breakdown
    - Cache/resume support (survives crashes)
    - Dual plagiarism detection (TF-IDF + n-gram)
    - Excel report with summary statistics
    - Rich progress display & structured logging
"""

import logging
import sys
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

import config  # noqa: F401  — triggers .env loading
from skills.file_extractor.extractor import extract_and_collect, read_file
from utils.cache import load_cache, save_cache, clear_cache
from skills.rubric_generator.rubric_agent import generate_rubric, approve_rubric, save_rubric, load_rubric
from skills.grader.grader_agent import grade_all
from skills.plagiarism_detector.plagiarism_agent import check_plagiarism, apply_flags
from skills.report_writer.excel_writer import write_results

# ── Logging setup ───────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[RichHandler(rich_tracebacks=True, show_time=False)],
)
logger = logging.getLogger("analyzer")
console = Console()


def main() -> None:
    if len(sys.argv) != 3:
        console.print("[bold red]Usage:[/] python main.py <submissions.zip> <assignment_brief>")
        sys.exit(1)

    if not config.GROQ_API_KEY:
        console.print("[bold red]Error:[/] GROQ_API_KEY is not set. Add it to your .env file.")
        sys.exit(1)

    zip_path = sys.argv[1]
    brief_path = sys.argv[2]
    base_dir = str(Path(zip_path).parent)

    # ── 1. Read the assignment brief ────────────────────────────
    console.rule("[bold blue]Step 1: Assignment Brief")
    brief_text = read_file(brief_path)
    logger.info("Brief loaded (%d chars) from %s", len(brief_text), brief_path)

    # ── 2. Generate & approve rubric (with cache) ───────────────
    console.rule("[bold blue]Step 2: Grading Rubric")
    rubric = load_rubric(base_dir)
    if rubric:
        console.print("[dim]Found saved rubric from previous run.[/]")
        choice = input("Use saved rubric? [Y]es / [N]o → ").strip().upper()
        if choice != "Y":
            rubric = None

    while not rubric:
        with console.status("Generating rubric via LLM…"):
            rubric = generate_rubric(brief_text)
        rubric = approve_rubric(rubric)
        if not rubric:
            console.print("[yellow]Regenerating rubric…[/]")

    save_rubric(rubric, base_dir)

    # ── 3. Extract & read submissions ───────────────────────────
    console.rule("[bold blue]Step 3: Extract Submissions")
    with console.status("Extracting ZIP…"):
        submissions = extract_and_collect(zip_path)
    logger.info("Found %d submission(s).", len(submissions))

    if not submissions:
        console.print("[bold red]No supported files found. Exiting.[/]")
        sys.exit(0)

    # ── 4. Grade each submission (with cache + progress) ────────
    console.rule("[bold blue]Step 4: Grading")
    cached = load_cache(base_dir)
    cached_count = sum(1 for s in submissions if s["filename"] in cached)
    if cached_count:
        logger.info("Resuming: %d/%d already graded (cached).", cached_count, len(submissions))

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    )

    with progress:
        task = progress.add_task("Grading submissions…", total=len(submissions))
        progress.advance(task, cached_count)

        def _on_complete(filename: str, result: dict):
            cached[filename] = result
            save_cache(base_dir, cached)
            progress.advance(task, 1)

        results = grade_all(rubric, submissions, cached=cached, on_complete=_on_complete)

    # ── 5. Plagiarism detection ─────────────────────────────────
    console.rule("[bold blue]Step 5: Plagiarism Check")
    with console.status("Analyzing similarity…"):
        flags = check_plagiarism(submissions)
    results = apply_flags(results, flags)

    flagged = sum(1 for r in results if r.get("plagiarism_flag"))
    logger.info("%d submission(s) flagged for similarity.", flagged)

    # ── 6. Write Excel report ───────────────────────────────────
    console.rule("[bold blue]Step 6: Report")
    output_path = str(Path(base_dir) / config.OUTPUT_FILENAME)
    write_results(results, output_path)
    clear_cache(base_dir)

    console.print(f"\n[bold green]✅ Report saved to:[/] {output_path}")
    console.print(f"   [dim]{len(results)} students graded • {flagged} plagiarism flags[/]")


if __name__ == "__main__":
    main()
