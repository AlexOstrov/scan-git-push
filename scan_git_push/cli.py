"""CLI entry point for scan-git-push."""

from __future__ import annotations

import typer
from rich.console import Console

app = typer.Typer(
    name="scan-git-push",
    help="Analyze git push changes with LLM-powered commit summaries.",
    add_completion=False,
)
console = Console()


@app.command()
def main() -> None:
    """Main entry point - placeholder for now."""
    console.print("[bold green]scan-git-push[/bold green] v0.1.0")
    console.print("CLI implementation in progress...")


if __name__ == "__main__":
    app()
