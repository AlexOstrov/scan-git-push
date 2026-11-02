"""Single entry point for building console, Markdown and HTML reports."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

from scan_git_push.analyzer import AnalysisResult
from scan_git_push.reporters.console import ConsoleReporter
from scan_git_push.reporters.html import HTMLReporter
from scan_git_push.reporters.markdown import MarkdownReporter
from scan_git_push.reporters.summary import ProjectSection, SummaryReporter


@dataclass
class ProjectReport:
    """Generated artifacts for a single project."""

    name: str
    url: str
    markdown: Path
    html: Path
    commits: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "project": self.url,
            "name": self.name,
            "commits": self.commits,
            "markdown": str(self.markdown),
            "html": str(self.html),
        }


@dataclass
class SummaryReport:
    """Generated cross-project summary."""

    markdown: Path
    html: Path

    def to_dict(self) -> dict[str, str]:
        return {"markdown": str(self.markdown), "html": str(self.html)}


class ReportService:
    """Generates reports for one or many projects from analysis results."""

    def __init__(self, console: Console, output_dir: Path) -> None:
        self.console = console
        self.output_dir = Path(output_dir)
        self.console_reporter = ConsoleReporter(console)
        self.markdown_reporter = MarkdownReporter()
        self.html_reporter = HTMLReporter()
        self.summary_reporter = SummaryReporter()

    def generate_project(
        self,
        name: str,
        url: str,
        results: list[AnalysisResult],
        days: int,
        print_console: bool = True,
    ) -> ProjectReport:
        if print_console:
            self.console_reporter.report(results, name, project_url=url)

        markdown = self.markdown_reporter.generate(
            results=results,
            repo_name=name,
            days=days,
            output_dir=self.output_dir,
            project_url=url,
        )
        html = self.html_reporter.generate(
            results=results,
            repo_name=name,
            days=days,
            output_dir=self.output_dir,
            project_url=url,
        )
        return ProjectReport(
            name=name, url=url, markdown=markdown, html=html, commits=len(results)
        )

    def generate_summary(
        self, sections: list[ProjectSection], days: int
    ) -> SummaryReport:
        markdown, html = self.summary_reporter.generate(
            sections, days=days, output_dir=self.output_dir
        )
        return SummaryReport(markdown=markdown, html=html)
