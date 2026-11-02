"""Tests for the report generators."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from scan_git_push.analyzer import AnalysisResult, RequirementRef
from scan_git_push.git.models import FileChange
from scan_git_push.reporters.console import ConsoleReporter
from scan_git_push.reporters.html import HTMLReporter
from scan_git_push.reporters.markdown import MarkdownReporter
from scan_git_push.reporters.service import ReportService
from scan_git_push.reporters.summary import ProjectSection, SummaryReporter


def _result(**overrides) -> AnalysisResult:
    data = dict(
        sha="abc123def456",
        author="testuser",
        date="2026-10-18T12:00:00+00:00",
        message="Test commit message\n\nBody",
        files=["test_file.py"],
        summary="Test summary",
        chunks_analyzed=1,
        file_changes=[
            FileChange(path="test_file.py", status="modified", additions=10, deletions=5)
        ],
        additions=10,
        deletions=5,
    )
    data.update(overrides)
    return AnalysisResult(**data)


class TestConsoleReporter:
    def test_report(self) -> None:
        console = MagicMock()
        ConsoleReporter(console=console).report([_result()], "test-repo", "https://x/y")
        console.print.assert_called()
        printed = " ".join(str(call.args[0]) for call in console.print.call_args_list if call.args)
        assert "test-repo" in printed
        assert "https://x/y" in printed

    def test_empty_report(self) -> None:
        console = MagicMock()
        ConsoleReporter(console=console).report([], "test-repo")
        printed = " ".join(str(call.args[0]) for call in console.print.call_args_list if call.args)
        assert "No analyzed commits" in printed

    def test_requirements_are_shown(self) -> None:
        console = MagicMock()
        result = _result(requirements=[RequirementRef(code="REQ_1", point="1.1")])
        ConsoleReporter(console=console).report([result], "repo")
        printed = " ".join(str(call.args[0]) for call in console.print.call_args_list if call.args)
        assert "REQ_1" in printed


class TestMarkdownReporter:
    def test_generate(self, tmp_path: Path) -> None:
        path = MarkdownReporter().generate([_result()], "test-repo", 7, tmp_path, "https://x/y")
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "test-repo" in content
        assert "abc123" in content
        assert "Test summary" in content
        assert "+10 -5" in content
        assert "https://x/y" in content

    def test_requirements_and_chunks(self, tmp_path: Path) -> None:
        result = _result(
            chunks_analyzed=3,
            requirements=[RequirementRef(code="REQ_1", point="1.1")],
        )
        path = MarkdownReporter().generate([result], "repo", 7, tmp_path)
        content = path.read_text(encoding="utf-8")
        assert "REQ_1 п.1.1" in content
        assert "3 chunks" in content


class TestHTMLReporter:
    def test_generate(self, tmp_path: Path) -> None:
        path = HTMLReporter().generate([_result()], "test-repo", 7, tmp_path)
        content = path.read_text(encoding="utf-8")
        assert "<html" in content.lower()
        assert "<style>" in content
        assert "abc123" in content
        assert "cdn" not in content.lower()

    def test_values_are_escaped(self, tmp_path: Path) -> None:
        result = _result(summary="<script>alert(1)</script>", author="<b>evil</b>")
        path = HTMLReporter().generate([result], "repo", 7, tmp_path)
        content = path.read_text(encoding="utf-8")
        assert "<script>alert(1)</script>" not in content
        assert "&lt;script&gt;" in content

    def test_binary_file(self, tmp_path: Path) -> None:
        result = _result(file_changes=[FileChange(path="logo.png", is_binary=True)])
        content = HTMLReporter().generate([result], "repo", 7, tmp_path).read_text(
            encoding="utf-8"
        )
        assert "logo.png" in content
        assert "binary" in content


class TestSummaryReporter:
    def test_generate(self, tmp_path: Path) -> None:
        sections = [
            ProjectSection("one", "https://x/one", [_result()]),
            ProjectSection("two", "https://x/two", []),
        ]
        markdown, html = SummaryReporter().generate(sections, 7, tmp_path)

        md = markdown.read_text(encoding="utf-8")
        assert "| one |" in md or "| [one]" in md
        assert "No analyzed commits." in md

        page = html.read_text(encoding="utf-8")
        assert "one" in page and "two" in page
        assert "project-filter" in page


class TestReportService:
    def test_generate_project_and_summary(self, tmp_path: Path) -> None:
        service = ReportService(MagicMock(), tmp_path)
        report = service.generate_project("repo", "https://x/repo", [_result()], 7)
        assert report.commits == 1
        assert report.markdown.exists()
        assert report.html.exists()

        summary = service.generate_summary(
            [ProjectSection("repo", "https://x/repo", [_result()])], 7
        )
        assert summary.markdown.exists()
        assert summary.html.exists()
        assert summary.to_dict()["markdown"].endswith(".md")
