"""Tests for reporter modules."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scan_git_push.analyzer import AnalysisResult
from scan_git_push.reporters.console import ConsoleReporter
from scan_git_push.reporters.markdown import MarkdownReporter
from scan_git_push.reporters.html import HTMLReporter


class TestConsoleReporter:
    """Test ConsoleReporter class."""

    def test_report(self, sample_analysis_result) -> None:
        """Test console output generation."""
        mock_console = MagicMock()
        reporter = ConsoleReporter(console=mock_console)
        reporter.report([sample_analysis_result], "test-repo")
        mock_console.print.assert_called()


class TestMarkdownReporter:
    """Test MarkdownReporter class."""

    def test_generate(self, sample_analysis_result, tmp_path: Path) -> None:
        """Test Markdown report generation."""
        reporter = MarkdownReporter()
        output_path = reporter.generate(
            results=[sample_analysis_result],
            repo_name="test-repo",
            days=7,
            output_dir=tmp_path,
        )

        assert output_path.exists()
        content = output_path.read_text(encoding="utf-8")
        assert "test-repo" in content
        assert "abc123" in content
        assert "Test summary" in content


class TestHTMLReporter:
    """Test HTMLReporter class."""

    def test_generate(self, sample_analysis_result, tmp_path: Path) -> None:
        """Test HTML report generation."""
        reporter = HTMLReporter()
        output_path = reporter.generate(
            results=[sample_analysis_result],
            repo_name="test-repo",
            days=7,
            output_dir=tmp_path,
        )

        assert output_path.exists()
        content = output_path.read_text(encoding="utf-8")
        assert "test-repo" in content
        assert "abc123" in content
        assert "<html" in content.lower()
        assert "<style>" in content  # Self-contained styles
