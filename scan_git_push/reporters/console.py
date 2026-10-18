"""Console report formatter using Rich."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from scan_git_push.analyzer import AnalysisResult


class ConsoleReporter:
    """Outputs analysis results to the terminal with Rich formatting."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    def report(self, results: list[AnalysisResult], repo_name: str) -> None:
        """Print all commit analyses to the console."""
        self.console.print(f"\n[bold cyan]═══ {repo_name} ═══[/bold cyan]\n")

        for result in results:
            self._print_commit(result)

        self._print_summary(results)

    def _print_commit(self, result: AnalysisResult) -> None:
        """Print a single commit analysis."""
        # Header with SHA and author
        header = Text()
        header.append(f"{result.sha[:8]} ", style="bold yellow")
        header.append(f"{result.author} ", style="cyan")
        header.append(f"({result.date[:10]})", style="dim")

        self.console.print(header)
        self.console.print(f"  [dim]{result.message.splitlines()[0]}[/dim]")

        # Files table
        if result.files:
            table = Table(show_header=True, header_style="bold magenta", box=None, pad_edge=False)
            table.add_column("File", style="white")
            for f in result.files:
                table.add_row(f)
            self.console.print(table)

        # Summary
        if result.summary:
            self.console.print(Panel(
                result.summary,
                title="[bold]Summary[/bold]",
                border_style="green",
                padding=(0, 1),
            ))

        if result.chunks_analyzed > 1:
            self.console.print(
                f"  [dim](analyzed in {result.chunks_analyzed} chunks)[/dim]"
            )

        self.console.print()

    def _print_summary(self, results: list[AnalysisResult]) -> None:
        """Print overall statistics."""
        total_files = sum(len(r.files) for r in results)
        total_chunks = sum(r.chunks_analyzed for r in results)

        self.console.print("\n[bold cyan]═══ Overall Summary ═══[/bold cyan]")
        self.console.print(f"  Commits analyzed: [bold]{len(results)}[/bold]")
        self.console.print(f"  Files changed:     [bold]{total_files}[/bold]")
        self.console.print(f"  LLM chunks used:   [bold]{total_chunks}[/bold]")
        self.console.print()
