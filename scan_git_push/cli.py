"""CLI entry point for scan-git-push with model selection and progress tracking."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from loguru import logger

from scan_git_push.analyzer import CommitAnalyzer, AnalysisResult
from scan_git_push.config import Settings
from scan_git_push.github_client import GitHubClient
from scan_git_push.llm.ollama import OllamaProvider
from scan_git_push.llm.github_models import GitHubModelsProvider
from scan_git_push.llm.groq import GroqProvider
from scan_git_push.memory.manager import MemoryManager
from scan_git_push.reporters.console import ConsoleReporter
from scan_git_push.reporters.markdown import MarkdownReporter
from scan_git_push.reporters.html import HTMLReporter

app = typer.Typer(
    name="scan-git-push",
    help="Analyze git push changes with LLM-powered commit summaries.",
    add_completion=False,
)
console = Console()


def _extract_repo_name(url: str) -> str:
    """Extract repository name from GitHub URL."""
    return url.rstrip("/").split("/")[-1].removesuffix(".git")


def _check_secrets(settings: Settings) -> None:
    """Validate secrets file exists and contains required tokens."""
    secrets_path = Path(settings.secrets_path)
    if not secrets_path.exists():
        console.print(f"[red]Error: Secrets file not found: {secrets_path}[/red]")
        raise typer.Exit(code=1)

    # GITHUB_TOKEN is always required to read the repository.
    try:
        token = settings.github_token
        if not token:
            raise ValueError("GITHUB_TOKEN is empty")
    except (KeyError, ValueError) as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(code=2)

    # GITHUB_MODELS_TOKEN / GROQ_API_KEY are required only when their provider is selected.
    if settings.llm_provider == "github_models":
        _require_github_models_token(settings)
    elif settings.llm_provider == "groq":
        _require_groq_token(settings)


def _require_github_models_token(settings: Settings) -> str:
    """Validate that GITHUB_MODELS_TOKEN exists and is non-empty.

    Returns the token on success, or exits with a clear error (code 6).
    """
    try:
        token = settings.github_models_token
    except (FileNotFoundError, KeyError, ValueError) as exc:
        console.print(
            f"[red]Error: GITHUB_MODELS_TOKEN is missing from the secrets file "
            f"({settings.secrets_path}).\n"
            f"Add it as: GITHUB_MODELS_TOKEN=<your_github_token>[/red]"
        )
        raise typer.Exit(code=6) from exc

    if not token:
        console.print("[red]Error: GITHUB_MODELS_TOKEN is empty in the secrets file.[/red]")
        raise typer.Exit(code=6)

    return token


def _try_github_models_token(settings: Settings) -> str | None:
    """Return GITHUB_MODELS_TOKEN if present, otherwise None (best effort)."""
    try:
        return settings.github_models_token or None
    except (FileNotFoundError, KeyError, ValueError):
        return None


def _require_groq_token(settings: Settings) -> str:
    """Validate that GROQ_API_KEY exists and is non-empty.

    Returns the token on success, or exits with a clear error (code 7).
    """
    try:
        token = settings.groq_api_key
    except (FileNotFoundError, KeyError, ValueError) as exc:
        console.print(
            f"[red]Error: GROQ_API_KEY is missing from the secrets file "
            f"({settings.secrets_path}).\n"
            f"Add it as: GROQ_API_KEY=<your_groq_key>[/red]"
        )
        raise typer.Exit(code=7) from exc

    if not token:
        console.print("[red]Error: GROQ_API_KEY is empty in the secrets file.[/red]")
        raise typer.Exit(code=7)

    return token


def _try_groq_token(settings: Settings) -> str | None:
    """Return GROQ_API_KEY if present, otherwise None (best effort)."""
    try:
        return settings.groq_api_key or None
    except (FileNotFoundError, KeyError, ValueError):
        return None


def _prompt_model_selection(
    ollama_models: list[str],
    gh_models: list[str],
    groq_models: list[str] | None = None,
) -> tuple[str, str]:
    """Prompt user to select a model. Returns (provider, model_name)."""
    console.print("\n[bold cyan]Available LLM models:[/bold cyan]\n")

    options: list[tuple[str, str, str]] = []

    for m in ollama_models:
        options.append(("ollama", m, f"[Ollama] {m}"))
    for m in gh_models:
        options.append(("github_models", m, f"[GitHub Models] {m}"))
    for m in groq_models or []:
        options.append(("groq", m, f"[Groq] {m}"))

    if not options:
        console.print("[red]Error: No models available from any provider.[/red]")
        raise typer.Exit(code=3)

    for i, (_, _, label) in enumerate(options, start=1):
        console.print(f"  {i}. {label}")

    console.print()
    choice = typer.prompt("Select model number", type=int)

    if choice < 1 or choice > len(options):
        console.print("[red]Invalid selection.[/red]")
        raise typer.Exit(code=4)

    provider_name, model_name, _ = options[choice - 1]
    return provider_name, model_name


@app.command()
def main(
    config_path: Optional[str] = typer.Option(
        None, "--config", "-c", help="Path to config.json"
    ),
) -> None:
    """Analyze git push changes with LLM-powered commit summaries."""
    console.print("[bold green]scan-git-push[/bold green] v0.1.0\n")

    # Load configuration
    try:
        settings = Settings.from_file(config_path or "config.json")
    except FileNotFoundError as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(code=1)

    if not settings.git_project:
        console.print("[red]Error: GIT_PROJECT is not set in config[/red]")
        raise typer.Exit(code=1)

    # Validate secrets
    _check_secrets(settings)

    # Check provider availability. Both providers are queried best-effort so the
    # user can switch between them at runtime by picking any listed model.
    console.print("[bold]Checking LLM provider availability...[/bold]\n")

    ollama_models: list[str] = []
    gh_models: list[str] = []

    # Ollama (local) — no token required.
    ollama_provider = OllamaProvider(model="", base_url=settings.ollama_base_url)
    if ollama_provider.is_available():
        ollama_models = ollama_provider.list_models()
        console.print(
            f"  [green]Ollama[/green] available at {settings.ollama_base_url} "
            f"({len(ollama_models)} model(s))"
        )
    else:
        console.print(
            f"  [dim]Ollama[/dim] not available at {settings.ollama_base_url} — skipped"
        )

    # GitHub Models API — token required when selected via config, optional otherwise.
    gh_models_token: str | None
    if settings.llm_provider == "github_models":
        gh_models_token = _require_github_models_token(settings)
    else:
        gh_models_token = _try_github_models_token(settings)

    if gh_models_token:
        gh_provider = GitHubModelsProvider(
            model=settings.gh_models_model,
            endpoint=settings.gh_models_endpoint,
            token=gh_models_token,
        )
        if gh_provider.is_available():
            gh_models = gh_provider.list_models()
            console.print(
                f"  [green]GitHub Models API[/green] available ({len(gh_models)} model(s))"
            )
        else:
            console.print("  [dim]GitHub Models API[/dim] not reachable — skipped")
    else:
        console.print("  [dim]GitHub Models API[/dim] skipped (no GITHUB_MODELS_TOKEN)")

    # Groq — token required when selected via config, optional otherwise.
    groq_models: list[str] = []
    if settings.llm_provider == "groq":
        groq_token = _require_groq_token(settings)
    else:
        groq_token = _try_groq_token(settings)

    if groq_token:
        groq_provider = GroqProvider(
            model=settings.groq_model,
            endpoint=settings.groq_endpoint,
            token=groq_token,
        )
        if groq_provider.is_available():
            groq_models = groq_provider.list_models()
            console.print(f"  [green]Groq[/green] available ({len(groq_models)} model(s))")
        else:
            console.print("  [dim]Groq[/dim] not reachable — skipped")
    else:
        console.print("  [dim]Groq[/dim] skipped (no GROQ_API_KEY)")

    if not ollama_models and not gh_models and not groq_models:
        console.print("[red]Error: No LLM models available from any provider.[/red]")
        raise typer.Exit(code=3)

    # Model selection (provider is chosen here, at runtime)
    provider_name, model_name = _prompt_model_selection(
        ollama_models, gh_models, groq_models
    )
    console.print(f"\n  Selected: [bold]{provider_name}[/bold] / [bold]{model_name}[/bold]\n")

    # Create LLM provider instance
    if provider_name == "ollama":
        llm = OllamaProvider(
            model=model_name,
            base_url=settings.ollama_base_url,
            max_retries=settings.max_retries,
            backoff_factor=settings.backoff_factor,
        )
    elif provider_name == "github_models":
        llm = GitHubModelsProvider(
            model=model_name,
            endpoint=settings.gh_models_endpoint,
            token=settings.github_models_token,
            max_retries=settings.max_retries,
            backoff_factor=settings.backoff_factor,
        )
    else:
        llm = GroqProvider(
            model=model_name,
            endpoint=settings.groq_endpoint,
            token=settings.groq_api_key,
            max_retries=settings.max_retries,
            backoff_factor=settings.backoff_factor,
        )

    # Fetch commits
    console.print(f"[bold]Fetching commits from {settings.git_project} (last {settings.git_days} days)...[/bold]\n")

    gh_client = GitHubClient(
        token=settings.github_token,
        repo_url=settings.git_project,
        max_retries=settings.max_retries,
        backoff_factor=settings.backoff_factor,
        timeout=settings.http_timeout,
    )

    try:
        commits = gh_client.get_commits(days=settings.git_days)
    except Exception as exc:
        console.print(f"[red]Error fetching commits: {exc}[/red]")
        raise typer.Exit(code=5)

    if not commits:
        console.print("[yellow]No commits found in the specified period.[/yellow]")
        raise typer.Exit(code=0)

    console.print(f"  Found [bold]{len(commits)}[/bold] commits to analyze.\n")

    # Analyze commits
    analyzer = CommitAnalyzer(
        llm_provider=llm,
        max_chunk_chars=settings.max_diff_chars_per_chunk,
        max_retries=settings.max_retries,
        backoff_factor=settings.backoff_factor,
    )

    results: list[AnalysisResult] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Analyzing commits...", total=len(commits))

        for commit in commits:
            progress.update(task, description=f"Analyzing {commit.sha[:8]}...")
            try:
                result = analyzer.analyze_commit(commit)
                results.append(result)
            except Exception as exc:
                logger.error(f"Failed to analyze commit {commit.sha[:8]}: {exc}")
                console.print(f"  [yellow]Warning: Skipped commit {commit.sha[:8]}: {exc}[/yellow]")
            progress.advance(task)

    # Generate reports
    repo_name = _extract_repo_name(settings.git_project)

    console.print("\n[bold]Generating reports...[/bold]\n")

    # Console output
    console_reporter = ConsoleReporter(console)
    console_reporter.report(results, repo_name)

    # Markdown report
    md_path = MarkdownReporter().generate(
        results=results,
        repo_name=repo_name,
        days=settings.git_days,
        output_dir=settings.output_dir,
    )
    console.print(f"  Markdown: [green]{md_path}[/green]")

    # HTML report
    html_path = HTMLReporter().generate(
        results=results,
        repo_name=repo_name,
        days=settings.git_days,
        output_dir=settings.output_dir,
    )
    console.print(f"  HTML:     [green]{html_path}[/green]")

    # Update artifact memory
    memory_mgr = MemoryManager(settings.memory_dir)
    memory_mgr.ensure_files()
    memory_mgr.append_changelog(
        f"Analyzed {len(results)} commits from {repo_name} using {provider_name}/{model_name}"
    )

    console.print("\n[bold green]Done![/bold green]")


if __name__ == "__main__":
    app()
