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

    def report(
        self,
        results: list[AnalysisResult],
        repo_name: str,
        project_url: str = "",
    ) -> None:
        """Print all commit analyses to the console."""
        self.console.print(f"\n[bold cyan]=== {repo_name} ===[/bold cyan]")
        if project_url:
            self.console.print(f"[dim]{project_url}[/dim]")
        self.console.print()

        if not results:
            self.console.print("[yellow]No analyzed commits in the selected period.[/yellow]")
            return

        for result in results:
            self._print_commit(result)

        self._print_summary(results)

    def _print_commit(self, result: AnalysisResult) -> None:
        header = Text()
        header.append(f"{result.sha[:8]} ", style="bold yellow")
        header.append(f"{result.author} ", style="cyan")
        header.append(f"({result.date[:10]})", style="dim")
        if result.additions or result.deletions:
            header.append(f"  +{result.additions} -{result.deletions}", style="green")

        self.console.print(header)
        self.console.print(f"  [dim]{result.subject}[/dim]")

        if result.file_changes:
            table = Table(
                show_header=True, header_style="bold magenta", box=None, pad_edge=False
            )
            table.add_column("File", style="white")
            table.add_column("Status", style="dim")
            table.add_column("+/-", justify="right", style="green")
            for file in result.file_changes:
                delta = "binary" if file.is_binary else f"+{file.additions} -{file.deletions}"
                table.add_row(file.path, file.status, delta)
            self.console.print(table)
        elif result.files:
            for path in result.files:
                self.console.print(f"  {path}")

        if result.requirements:
            labels = ", ".join(result.requirement_labels)
            self.console.print(f"  [magenta]Requirements:[/magenta] {labels}")

        if result.summary:
            self.console.print(
                Panel(
                    result.summary,
                    title="[bold]Summary[/bold]",
                    border_style="green",
                    padding=(0, 1),
                )
            )

        if result.chunks_analyzed > 1:
            self.console.print(
                f"  [dim](analyzed in {result.chunks_analyzed} chunks)[/dim]"
            )

        self.console.print()

    def _print_summary(self, results: list[AnalysisResult]) -> None:
        total_files = sum(len(r.file_changes or r.files) for r in results)
        total_chunks = sum(r.chunks_analyzed for r in results)
        total_additions = sum(r.additions for r in results)
        total_deletions = sum(r.deletions for r in results)
        requirements = {
            label for result in results for label in result.requirement_labels
        }

        self.console.print("\n[bold cyan]=== Overall Summary ===[/bold cyan]")
        self.console.print(f"  Commits analyzed: [bold]{len(results)}[/bold]")
        self.console.print(f"  Files changed:     [bold]{total_files}[/bold]")
        self.console.print(
            f"  Lines:             [green]+{total_additions}[/green] "
            f"[red]-{total_deletions}[/red]"
        )
        self.console.print(f"  LLM chunks used:   [bold]{total_chunks}[/bold]")
        if requirements:
            self.console.print(
                f"  Requirements:      [bold]{len(requirements)}[/bold]"
            )
        self.console.print()
