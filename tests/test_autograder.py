"""
Tests for AutoGrader — covers pure-logic modules (no API calls).
Run with: python -m pytest tests/ -v
"""

import json
import os
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest


# ── Cache tests ─────────────────────────────────────────────────

from utils.cache import load_cache, save_cache, clear_cache


class TestCache:
    def test_save_and_load(self, tmp_path):
        data = {"file1.py": {"marks": 80}, "file2.py": {"marks": 65}}
        save_cache(str(tmp_path), data)
        loaded = load_cache(str(tmp_path))
        assert loaded == data

    def test_load_missing_cache(self, tmp_path):
        assert load_cache(str(tmp_path)) == {}

    def test_clear_cache(self, tmp_path):
        save_cache(str(tmp_path), {"a": {}})
        clear_cache(str(tmp_path))
        assert load_cache(str(tmp_path)) == {}

    def test_load_corrupt_cache(self, tmp_path):
        cache_file = tmp_path / ".grading_cache.json"
        cache_file.write_text("not valid json")
        assert load_cache(str(tmp_path)) == {}


# ── Retry tests ─────────────────────────────────────────────────

from utils.retry import retry_api_call


class TestRetry:
    def test_success_first_try(self):
        result = retry_api_call(lambda: 42, max_retries=3)
        assert result == 42

    def test_retries_then_succeeds(self):
        call_count = {"n": 0}

        def flaky():
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise RuntimeError("fail")
            return "ok"

        assert retry_api_call(flaky, max_retries=3) == "ok"
        assert call_count["n"] == 3

    def test_exhausts_retries(self):
        def always_fail():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            retry_api_call(always_fail, max_retries=0)


# ── Extractor tests ────────────────────────────────────────────

from skills.file_extractor.extractor import (
    extract_zip,
    read_text_file,
    read_notebook,
    collect_submissions,
)


class TestExtractor:
    def test_extract_zip(self, tmp_path):
        # Create a test zip
        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("hello.py", "print('hello')")

        out_dir = extract_zip(str(zip_path), str(tmp_path / "out"))
        assert (Path(out_dir) / "hello.py").read_text() == "print('hello')"

    def test_zip_slip_rejected(self, tmp_path):
        zip_path = tmp_path / "evil.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("../../etc/passwd", "bad")

        with pytest.raises(ValueError, match="Unsafe path"):
            extract_zip(str(zip_path))

    def test_read_text_file(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("x = 1\n")
        assert read_text_file(str(f)) == "x = 1"

    def test_read_notebook(self, tmp_path):
        nb = {
            "cells": [
                {"cell_type": "code", "source": ["print(1)"]},
                {"cell_type": "markdown", "source": ["# Title"]},
            ]
        }
        f = tmp_path / "nb.ipynb"
        f.write_text(json.dumps(nb))
        text = read_notebook(str(f))
        assert "[Code]" in text
        assert "[Markdown]" in text

    def test_collect_skips_hidden_dirs(self, tmp_path):
        (tmp_path / "__MACOSX").mkdir()
        (tmp_path / "__MACOSX" / "junk.py").write_text("bad")
        (tmp_path / "real.py").write_text("good")
        subs = collect_submissions(str(tmp_path))
        assert len(subs) == 1
        assert subs[0]["filename"] == "real.py"


# ── Plagiarism tests ───────────────────────────────────────────

from skills.plagiarism_detector.plagiarism_agent import (
    _ngram_jaccard,
    check_plagiarism,
    apply_flags,
)


class TestPlagiarism:
    def test_identical_texts_high_jaccard(self):
        score = _ngram_jaccard("abcdefgh", "abcdefgh")
        assert score == 1.0

    def test_different_texts_low_jaccard(self):
        score = _ngram_jaccard("abcdefgh", "xyzwvuts")
        assert score < 0.1

    def test_short_text_returns_zero(self):
        assert _ngram_jaccard("ab", "ab") == 0.0

    def test_check_plagiarism_flags_identical(self):
        subs = [
            {"filename": "a.py", "content": "This is a long enough string for TF-IDF to work properly with cosine similarity"},
            {"filename": "b.py", "content": "This is a long enough string for TF-IDF to work properly with cosine similarity"},
        ]
        flags = check_plagiarism(subs)
        assert "a.py" in flags
        assert "b.py" in flags

    def test_check_plagiarism_no_flag_for_different(self):
        subs = [
            {"filename": "a.py", "content": "Python implementation of binary search algorithm with recursion"},
            {"filename": "b.py", "content": "JavaScript web server using Express framework for REST API endpoints"},
        ]
        flags = check_plagiarism(subs)
        assert len(flags) == 0

    def test_apply_flags_merges(self):
        results = [{"filename": "a.py", "marks": 80}]
        flags = {"a.py": ["Similar to b.py (90%)"]}
        updated = apply_flags(results, flags)
        assert updated[0]["plagiarism_flag"] == "Similar to b.py (90%)"

    def test_apply_flags_empty(self):
        results = [{"filename": "a.py", "marks": 80}]
        updated = apply_flags(results, {})
        assert updated[0]["plagiarism_flag"] == ""


# ── Grader JSON parsing test ───────────────────────────────────

from skills.grader.grader_agent import _parse_json


class TestGraderParsing:
    def test_parse_valid_json(self):
        raw = '{"name": "Alice", "id": "22F-1234", "marks": 85, "category_scores": {}, "deductions": "", "feedback": ""}'
        result = _parse_json(raw, "fallback.py")
        assert result["name"] == "Alice"
        assert result["marks"] == 85

    def test_parse_json_with_code_fences(self):
        raw = '```json\n{"name": "Bob", "id": "1", "marks": 70, "category_scores": {}, "deductions": "", "feedback": ""}\n```'
        result = _parse_json(raw, "fallback.py")
        assert result["name"] == "Bob"

    def test_parse_invalid_json_fallback(self):
        result = _parse_json("not json at all", "fallback.py")
        assert result["name"] == "fallback.py"
        assert result["marks"] == "Error"


# ── Excel writer tests ─────────────────────────────────────────

from skills.report_writer.excel_writer import write_results


class TestExcelWriter:
    def test_write_creates_file(self, tmp_path):
        results = [
            {
                "name": "Alice",
                "id": "001",
                "marks": 85,
                "category_scores": {"Correctness": 40, "Style": 20},
                "deductions": "-5: missing docs",
                "feedback": "Good work.",
                "plagiarism_flag": "",
            }
        ]
        out = str(tmp_path / "report.xlsx")
        write_results(results, out)
        assert Path(out).exists()
        assert Path(out).stat().st_size > 0

    def test_write_handles_error_marks(self, tmp_path):
        results = [
            {
                "name": "Bob",
                "id": "N/A",
                "marks": "Error",
                "category_scores": {},
                "deductions": "Grading failed",
                "feedback": "",
                "plagiarism_flag": "",
            }
        ]
        out = str(tmp_path / "report.xlsx")
        write_results(results, out)
        assert Path(out).exists()
