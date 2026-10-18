"""Markdown report generator."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from loguru import logger

from scan_git_push.analyzer import AnalysisResult


class MarkdownReporter:
    """Generates a Markdown report file."""

    def generate(
        self,
        results: list[AnalysisResult],
        repo_name: str,
        days: int,
        output_dir: Path,
    ) -> Path:
        """Write the Markdown report and return the file path."""
        date_str = datetime.now().strftime("%Y%m%d")
        filename = output_dir / f"{repo_name}_{date_str}.md"

        lines: list[str] = [
            f"# Git Push Analysis: {repo_name}",
            "",
            f"**Period:** last {days} days  ",
            f"**Generated:** {datetime.now().isoformat()}  ",
            f"**Commits analyzed:** {len(results)}",
            "",
            "---",
            "",
        ]

        for result in results:
            lines.extend(self._commit_to_md(result))

        lines.extend(self._overall_to_md(results))

        filename.parent.mkdir(parents=True, exist_ok=True)
        filename.write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"Markdown report saved: {filename}")
        return filename

    def _commit_to_md(self, result: AnalysisResult) -> list[str]:
        """Convert a single commit analysis to Markdown."""
        lines = [
            f"## {result.sha[:8]} — {result.author} ({result.date[:10]})",
            "",
            f"**Message:** {result.message.splitlines()[0]}",
            "",
        ]

        if result.files:
            lines.append("**Files changed:**")
            for f in result.files:
                lines.append(f"- `{f}`")
            lines.append("")

        if result.summary:
            lines.append(result.summary)
            lines.append("")

        if result.chunks_analyzed > 1:
            lines.append(f"*Analyzed in {result.chunks_analyzed} chunks*")
            lines.append("")

        lines.append("---")
        lines.append("")
        return lines

    def _overall_to_md(self, results: list[AnalysisResult]) -> list[str]:
        """Generate overall summary section."""
        total_files = sum(len(r.files) for r in results)
        total_chunks = sum(r.chunks_analyzed for r in results)

        return [
            "## Overall Summary",
            "",
            f"- **Commits analyzed:** {len(results)}",
            f"- **Files changed:** {total_files}",
            f"- **LLM chunks used:** {total_chunks}",
            "",
        ]
