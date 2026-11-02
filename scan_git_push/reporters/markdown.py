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
        project_url: str = "",
    ) -> Path:
        """Write the Markdown report and return the file path."""
        date_str = datetime.now().strftime("%Y%m%d")
        filename = Path(output_dir) / f"{repo_name}_{date_str}.md"

        lines: list[str] = [
            f"# Git Push Analysis: {repo_name}",
            "",
            f"**Project:** {project_url or repo_name}  ",
            f"**Period:** last {days} days (committer date)  ",
            f"**Generated:** {datetime.now().isoformat(timespec='seconds')}  ",
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
        lines = [
            f"## {result.sha[:8]} — {result.author} ({result.date[:10]})",
            "",
            f"**Message:** {result.subject}",
            "",
        ]

        if result.file_changes:
            lines.append("**Files changed:**")
            lines.append("")
            lines.append("| File | Status | +/- |")
            lines.append("|------|--------|-----|")
            for file in result.file_changes:
                delta = "binary" if file.is_binary else f"+{file.additions} -{file.deletions}"
                lines.append(f"| `{file.path}` | {file.status} | {delta} |")
            lines.append("")
        elif result.files:
            lines.append("**Files changed:**")
            for path in result.files:
                lines.append(f"- `{path}`")
            lines.append("")

        if result.requirements:
            labels = ", ".join(f"`{label}`" for label in result.requirement_labels)
            lines.append(f"**Affected requirements:** {labels}")
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
        total_files = sum(len(r.file_changes or r.files) for r in results)
        total_chunks = sum(r.chunks_analyzed for r in results)
        total_additions = sum(r.additions for r in results)
        total_deletions = sum(r.deletions for r in results)
        requirements = sorted(
            {label for result in results for label in result.requirement_labels}
        )

        lines = [
            "## Overall Summary",
            "",
            f"- **Commits analyzed:** {len(results)}",
            f"- **Files changed:** {total_files}",
            f"- **Lines:** +{total_additions} / -{total_deletions}",
            f"- **LLM chunks used:** {total_chunks}",
        ]
        if requirements:
            lines.append(f"- **Affected requirements:** {', '.join(requirements)}")
        lines.append("")
        return lines
