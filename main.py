"""
AutoGrader — AI-powered grading agent.

Usage:
    python main.py <submissions.zip> <assignment_brief_file>

Features:
    - LLM rubric generation with approval workflow
    - Answer key support (manual input or file)
    - Concurrent grading with per-category breakdown
    - Cache/resume support (survives crashes)
    - Dual plagiarism detection (TF-IDF + n-gram)
    - Excel report with summary statistics
    - Rich progress display & structured logging
"""

import logging
import shutil
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


def _collect_answer_key() -> str | None:
    """Ask user for an optional answer key via CLI."""
    console.print("\n[bold]Answer Key[/] (optional — improves grading accuracy)")
    choice = input("Provide answer key? [F]ile / [T]ype manually / [S]kip → ").strip().upper()
    if choice == "F":
        file_path = input("Answer key file path → ").strip()
        try:
            return read_file(file_path)
        except Exception as e:
            console.print(f"[yellow]Could not read file: {e}. Skipping answer key.[/]")
            return None
    elif choice == "T":
        console.print("Paste answer key (end with a blank line):")
        lines = []
        while True:
            line = input()
            if line == "":
                break
            lines.append(line)
        return "\n".join(lines) if lines else None
    return None


def main() -> None:
    if len(sys.argv) != 3:
        console.print("[bold red]Usage:[/] python main.py <submissions.zip> <assignment_brief>")
        sys.exit(1)

    if not config.GROQ_API_KEY:
        console.print("[bold red]Error:[/] GROQ_API_KEY is not set. Add it to your .env file.")
        sys.exit(1)

    zip_path   = sys.argv[1]
    brief_path = sys.argv[2]

    # Session directory: named after the ZIP so two classes never share a cache
    zip_stem = Path(zip_path).stem
    base_dir = str(Path(zip_path).parent / f".autograder_{zip_stem}")
    Path(base_dir).mkdir(exist_ok=True)

    extract_dir = None  # track for cleanup

    try:
        # ── 1. Read the assignment brief ────────────────────────
        console.rule("[bold blue]Step 1: Assignment Brief")
        brief_text = read_file(brief_path)
        logger.info("Brief loaded (%d chars) from %s", len(brief_text), brief_path)

        # ── 2. Generate & approve rubric (with cache) ───────────
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

        # ── 3. Answer key ───────────────────────────────────────
        console.rule("[bold blue]Step 3: Answer Key")
        answer_key = _collect_answer_key()
        if answer_key:
            logger.info("Answer key loaded (%d chars).", len(answer_key))
        else:
            logger.info("No answer key provided — grading against rubric only.")

        # ── 4. Extract & read submissions ───────────────────────
        console.rule("[bold blue]Step 4: Extract Submissions")
        with console.status("Extracting ZIP…"):
            # extract_and_collect now returns (submissions, extract_dir)
            # extract_dir is kept alive for cache crash recovery
            submissions, extract_dir = extract_and_collect(zip_path)
        logger.info("Found %d submission(s).", len(submissions))

        if not submissions:
            console.print("[bold red]No supported files found. Exiting.[/]")
            sys.exit(0)

        # ── 5. Grade each submission (with cache + progress) ────
        console.rule("[bold blue]Step 5: Grading")
        cached = load_cache(base_dir)

        # Use cache_key for consistent duplicate-filename handling
        cached_count = sum(
            1 for s in submissions
            if s.get("cache_key", s["filename"]) in cached
        )
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
                # Save under cache_key so resume logic works for duplicate filenames
                key = result.get("cache_key", filename)
                cached[key] = result
                save_cache(base_dir, cached)
                progress.advance(task, 1)

            results = grade_all(
                rubric,
                submissions,
                cached=cached,
                on_complete=_on_complete,
                answer_key=answer_key,
            )

        # ── 6. Plagiarism detection ─────────────────────────────
        console.rule("[bold blue]Step 6: Plagiarism Check")
        with console.status("Analyzing similarity…"):
            flags = check_plagiarism(submissions)
        results = apply_flags(results, flags)

        flagged = sum(1 for r in results if r.get("plagiarism_flag"))
        logger.info("%d submission(s) flagged for similarity.", flagged)

        # ── 7. Write Excel report ───────────────────────────────
        console.rule("[bold blue]Step 7: Report")
        output_path = str(Path(base_dir).parent / config.OUTPUT_FILENAME)
        write_results(results, output_path)

        # Clear cache only after report is successfully written
        clear_cache(base_dir)

        console.print(f"\n[bold green]Report saved to:[/] {output_path}")
        console.print(f"   [dim]{len(results)} students graded • {flagged} plagiarism flags[/]")

    finally:
        # Clean up temp extraction directory now that grading is complete
        if extract_dir and Path(extract_dir).exists():
            shutil.rmtree(extract_dir, ignore_errors=True)
            logger.info("Cleaned up temp directory: %s", extract_dir)


if __name__ == "__main__":
    main()
