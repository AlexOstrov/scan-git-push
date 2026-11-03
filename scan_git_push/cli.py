"""CLI entry point: multi-project analysis over local git mirrors."""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import typer
from loguru import logger
from pydantic import ValidationError
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
)

from scan_git_push import __version__
from scan_git_push.analysis.collector import CommitCollector, ProjectCommits
from scan_git_push.analysis.linker import RequirementLinker
from scan_git_push.analysis.results import results_from_rows
from scan_git_push.analyzer import AnalysisResult, CommitAnalyzer
from scan_git_push.config import ConfigError, Settings
from scan_git_push.database import DatabaseManager, DatabaseError, SchemaVersionError
from scan_git_push.git.errors import GitAuthError, GitError, GitUnavailableError
from scan_git_push.git.mirror import MirrorManager
from scan_git_push.git.runner import ensure_git_available
from scan_git_push.git.url import repo_name, url_key
from scan_git_push.llm.registry import ModelOption, ModelRegistry
from scan_git_push.memory.manager import MemoryManager
from scan_git_push.reporters.requirements import RequirementReporter
from scan_git_push.reporters.service import ProjectReport, ReportService
from scan_git_push.reporters.summary import ProjectSection
from scan_git_push.scan.database import (
    ScanDatabase,
    ScanDatabaseError,
    ScanSchemaVersionError,
)
from scan_git_push.scan.scanner import (
    ScanOptions,
    Scanner,
    json_payload as scan_json_payload,
)

app = typer.Typer(
    name="scan-git-push",
    help="Analyze git push changes with LLM-powered commit summaries.",
    add_completion=False,
    invoke_without_command=True,
)

EXIT_OK = 0
EXIT_CONFIG = 1
EXIT_AUTH = 2
EXIT_NO_MODEL = 3
EXIT_ARGS = 4
EXIT_REPO_UNAVAILABLE = 5
EXIT_MISSING_KEY = 8
EXIT_DB = 9
EXIT_STRICT = 10
EXIT_PARTIAL = 11
EXIT_SCHEMA = 12


@dataclass
class RunState:
    """Mutable state accumulated during a run."""

    sections: list[ProjectSection] = field(default_factory=list)
    reports: list[ProjectReport] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    analyzed_now: int = 0
    auth_failures: int = 0
    failed_projects: int = 0
    analysis_failures: int = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_console(json_output: bool) -> Console:
    """Send human-readable output to stderr when JSON is requested."""
    return Console(stderr=json_output)


def _load_settings(config_path: Optional[str]) -> Settings:
    try:
        return Settings.from_file(config_path or "config.json")
    except FileNotFoundError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=EXIT_CONFIG) from exc
    except (ConfigError, ValidationError, ValueError) as exc:
        typer.echo(f"Error: invalid configuration: {exc}", err=True)
        raise typer.Exit(code=EXIT_CONFIG) from exc


def _setup_logging(settings: Settings) -> None:
    path = settings.analysis_log_path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            path,
            level="DEBUG",
            rotation="10 MB",
            retention="30 days",
            encoding="utf-8",
            enqueue=False,
        )
    except OSError as exc:  # logging must never break the run
        logger.warning("Cannot create log file {}: {}", path, exc)


def _setup_scan_logging(settings: Settings, verbose: bool) -> None:
    """Configure ``scan`` logging: file DEBUG always, console only with --verbose."""
    logger.remove()
    logger.configure(extra={"url_key": "-"})
    fmt = "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {extra[url_key]} | {message}"
    if verbose:
        logger.add(sys.stderr, level="DEBUG", format=fmt)

    path = settings.scan_log_path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            path,
            level="DEBUG",
            rotation="10 MB",
            retention="30 days",
            encoding="utf-8",
            format=fmt,
            enqueue=False,
        )
    except OSError as exc:  # logging must never break the run
        logger.warning("Cannot create log file {}: {}", path, exc)


def _open_database(settings: Settings) -> DatabaseManager:
    db = DatabaseManager(settings.analysis_db_path)
    try:
        db.initialize()
    except SchemaVersionError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=EXIT_SCHEMA) from exc
    except (DatabaseError, sqlite3.Error) as exc:
        typer.echo(f"Error: database failure: {exc}", err=True)
        raise typer.Exit(code=EXIT_DB) from exc
    return db


def _generate_requirements_report(
    settings: Settings,
    console: Console,
    projects: Optional[list[str]],
    json_output: bool,
) -> None:
    """Build the HTML report of requirements and sections from ``scan.db``."""
    project_keys = (
        [url_key(ref, settings.base_dir) for ref in projects] if projects else None
    )
    db = ScanDatabase(settings.scan_db_path)
    try:
        db.validate()
        rows = db.requirement_report(project_keys)
    except ScanSchemaVersionError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=EXIT_SCHEMA) from exc
    except (ScanDatabaseError, sqlite3.Error) as exc:
        typer.echo(f"Error: scan database failure: {exc}", err=True)
        raise typer.Exit(code=EXIT_DB) from exc

    reporter = RequirementReporter()
    path, stats = reporter.generate(rows, settings.output_dir)

    if json_output:
        payload = {
            "exit_code": EXIT_OK,
            **stats,
            "projects": project_keys or [],
        }
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    console.print(f"Requirements: {stats['requirements']}")
    console.print(f"Sections:     {stats['sections']}")
    console.print(f"  HTML:     [green]{path}[/green]")
    console.print("\n[bold green]Done![/bold green]")


def _resolve_projects(settings: Settings, projects: Optional[list[str]]) -> list[str]:
    raw = list(projects or settings.scan.git_projects)
    if not raw:
        typer.echo(
            "Error: no projects configured. Set SCAN.GIT_PROJECTS or pass --project.",
            err=True,
        )
        raise typer.Exit(code=EXIT_CONFIG)

    seen: set[str] = set()
    unique: list[str] = []
    for item in raw:
        key = url_key(item, settings.base_dir)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _select_model(
    registry: ModelRegistry,
    console: Console,
    model_spec: Optional[str],
    non_interactive: bool,
) -> tuple[ModelOption, Any]:
    """Resolve the provider/model and verify that it is usable."""
    try:
        option = registry.resolve(model_spec)
        if option is None and (non_interactive or registry.llm.non_interactive):
            option = registry.default_option()
        if option is None:
            options = registry.discover()
            if not options:
                console.print(
                    "[red]Error: no LLM models available. Start Ollama or set "
                    "MISTRAL_API_KEY.[/red]"
                )
                raise typer.Exit(code=EXIT_NO_MODEL)
            console.print("\n[bold cyan]Available LLM models:[/bold cyan]\n")
            for index, item in enumerate(options, start=1):
                console.print(f"  {index}. {item.label}")
            console.print()
            choice = typer.prompt("Select model number", type=int)
            if choice < 1 or choice > len(options):
                console.print("[red]Invalid selection.[/red]")
                raise typer.Exit(code=EXIT_ARGS)
            option = options[choice - 1]
    except ValueError as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(code=EXIT_ARGS) from exc

    try:
        provider = registry.build(option)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        console.print(
            f"[red]Error: MISTRAL_API_KEY is required for the mistral provider "
            f"({registry.settings.secrets_path}): {exc}[/red]"
        )
        raise typer.Exit(code=EXIT_MISSING_KEY) from exc

    if not provider.is_available():
        console.print(
            f"[red]Error: model {option.spec} is not available. "
            f"Check the provider endpoint or start Ollama.[/red]"
        )
        raise typer.Exit(code=EXIT_NO_MODEL)

    console.print(
        f"  Selected model: [bold]{option.provider}[/bold] / [bold]{option.model}[/bold]\n"
    )
    return option, provider


def _json_payload(
    days: int,
    option: ModelOption,
    state: RunState,
    summary: Optional[dict[str, str]],
    exit_code: int,
) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "days": days,
        "model": {"provider": option.provider, "model": option.model},
        "projects": [
            {
                "project": section.url,
                "name": section.name,
                "commits": [
                    {
                        "sha": result.sha,
                        "author": result.author,
                        "date": result.date,
                        "message": result.message,
                        "summary": result.summary,
                        "files": [
                            {
                                "path": file.path,
                                "status": file.status,
                                "additions": file.additions,
                                "deletions": file.deletions,
                                "is_binary": file.is_binary,
                            }
                            for file in result.file_changes
                        ],
                        "additions": result.additions,
                        "deletions": result.deletions,
                        "chunks": result.chunks_analyzed,
                        "requirements": result.requirement_labels,
                    }
                    for result in section.results
                ],
                "reports": next(
                    (
                        report.to_dict()
                        for report in state.reports
                        if report.url == section.url
                    ),
                    {},
                ),
            }
            for section in state.sections
        ],
        "summary": summary or {},
        "errors": state.errors,
        "analyzed_now": state.analyzed_now,
        "exit_code": exit_code,
    }


# ---------------------------------------------------------------------------
# Analysis run
# ---------------------------------------------------------------------------
def run_analysis(
    config_path: Optional[str],
    projects: Optional[list[str]],
    days: Optional[int],
    model_spec: Optional[str],
    non_interactive: bool,
    json_output: bool,
    reanalyze: bool,
) -> None:
    """Shared implementation of ``main`` / ``analyze``."""
    console = _make_console(json_output)
    console.print(f"[bold green]scan-git-push[/bold green] v{__version__}\n")

    settings = _load_settings(config_path)
    _setup_logging(settings)

    try:
        ensure_git_available()
    except GitUnavailableError as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(code=EXIT_CONFIG) from exc

    report_days = days if days is not None else settings.analysis.git_day
    if report_days < 1 or report_days > 365:
        console.print("[red]Error: --days must be between 1 and 365[/red]")
        raise typer.Exit(code=EXIT_ARGS)

    project_refs = _resolve_projects(settings, projects)
    db = _open_database(settings)

    registry = ModelRegistry(settings)
    option, provider = _select_model(registry, console, model_spec, non_interactive)

    mirror = MirrorManager(settings.mirror_dir, settings.scan.mirror_filter)
    collector = CommitCollector(mirror, settings.analysis)
    analyzer = CommitAnalyzer(
        llm_provider=provider,
        max_chunk_chars=settings.analysis.max_diff_chars_per_chunk,
        max_patch_chars_per_file=settings.analysis.max_patch_chars_per_file,
        max_retries=settings.analysis.max_chunk_retries,
        backoff_factor=settings.analysis.backoff_factor,
    )
    linker = RequirementLinker(
        settings.scan_db_path if settings.analysis.link_requirements else None
    )
    report_service = ReportService(console, settings.output_dir)

    state = RunState()
    progress_enabled = not json_output

    for ref in project_refs:
        display = ref
        try:
            project = collector.collect(ref, base_dir=settings.base_dir, days=report_days)
            display = project.display_url
            project_id = db.ensure_project(ref, base_dir=settings.base_dir)
        except GitAuthError as exc:
            state.auth_failures += 1
            state.failed_projects += 1
            state.errors.append(
                {"project": display, "type": "auth", "error": str(exc)}
            )
            console.print(f"[red]Auth error for {display}: {exc}[/red]")
            continue
        except (GitError, OSError) as exc:
            state.failed_projects += 1
            state.errors.append(
                {"project": display, "type": "git", "error": str(exc)}
            )
            console.print(f"[red]Cannot access {display}: {exc}[/red]")
            continue
        except sqlite3.Error as exc:
            console.print(f"[red]Database failure: {exc}[/red]")
            raise typer.Exit(code=EXIT_DB) from exc

        commits = project.commits
        if not reanalyze:
            commits = db.filter_unanalyzed(commits, project_id)
        skipped = len(project.commits) - len(commits)
        console.print(
            f"[bold]{project.name}[/bold]: {len(project.commits)} commit(s) in period, "
            f"{len(commits)} to analyze"
            + (f", {skipped} already analyzed" if skipped else "")
        )

        completed: list[AnalysisResult] = []
        if commits:
            progress = Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                console=console,
                disable=not progress_enabled,
            )
            with progress:
                task = progress.add_task("Analyzing commits...", total=len(commits))
                for commit in commits:
                    progress.update(task, description=f"Analyzing {commit.sha[:8]}...")
                    try:
                        collector.attach_patches(project, commit)
                        result = analyzer.analyze_commit(commit)
                        db.save_commit_analysis(
                            project_id=project_id,
                            commit=commit,
                            summary=result.summary,
                            chunks_count=result.chunks_analyzed,
                            provider=option.provider,
                            model=option.model,
                            prompt_version=settings.analysis.prompt_version,
                        )
                        result.project = project.display_url
                        result.project_name = project.name
                        completed.append(result)
                        state.analyzed_now += 1
                    except Exception as exc:  # one bad commit must not stop the run
                        state.analysis_failures += 1
                        logger.exception("Failed to analyze commit {}", commit.sha[:8])
                        console.print(
                            f"  [yellow]Warning: skipped commit {commit.sha[:8]}: {exc}[/yellow]"
                        )
                    progress.advance(task)

        # Reports always come from the database, so a re-run without new commits
        # still produces a complete report for the period.
        rows = db.get_analyses([project_id], report_days)
        results = results_from_rows(rows)
        for result in results:
            result.project = project.display_url
            result.project_name = project.name
        linker.attach(project.key, results)

        report = report_service.generate_project(
            project.name, project.display_url, results, report_days
        )
        state.reports.append(report)
        state.sections.append(
            ProjectSection(name=project.name, url=project.display_url, results=results)
        )
        console.print(
            f"  Markdown: [green]{report.markdown}[/green]\n"
            f"  HTML:     [green]{report.html}[/green]\n"
        )

    if not state.sections:
        console.print("[red]Error: no project could be processed.[/red]")
        raise typer.Exit(
            code=EXIT_AUTH if state.auth_failures else EXIT_REPO_UNAVAILABLE
        )

    summary = report_service.generate_summary(state.sections, report_days)
    console.print(f"  Summary:  [green]{summary.markdown}[/green]")

    deleted = db.cleanup_old_records(settings.analysis.db_retention_days)
    if deleted:
        console.print(
            f"  [dim]Cleaned up {deleted} old record(s) "
            f"(older than {settings.analysis.db_retention_days} days)[/dim]"
        )

    if state.analyzed_now:
        memory = MemoryManager(settings.memory_dir)
        memory.ensure_files()
        memory.append_changelog(
            f"Analyzed {state.analyzed_now} commit(s) in {len(state.sections)} project(s) "
            f"using {option.provider}/{option.model}"
        )

    exit_code = EXIT_PARTIAL if (state.failed_projects or state.analysis_failures) else EXIT_OK
    payload = _json_payload(report_days, option, state, summary.to_dict(), exit_code)

    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        console.print("\n[bold green]Done![/bold green]")

    if exit_code != EXIT_OK:
        raise typer.Exit(code=exit_code)


# ---------------------------------------------------------------------------
# Scan run
# ---------------------------------------------------------------------------
def run_scan(
    config_path: Optional[str],
    projects: Optional[list[str]],
    branches: Optional[list[str]],
    all_branches: bool,
    include_extra: Optional[list[str]],
    exclude_extra: Optional[list[str]],
    max_file_size: Optional[int],
    dry_run: bool,
    force: bool,
    strict: Optional[bool],
    json_output: bool,
    no_color: bool,
    verbose: bool,
) -> None:
    """Shared implementation of ``scan`` and the deprecated ``--scan`` alias."""
    console = Console(stderr=json_output, no_color=no_color)
    console.print(f"[bold green]scan-git-push[/bold green] (scan) v{__version__}\n")

    if max_file_size is not None and max_file_size < 0:
        console.print("[red]Error: --max-file-size must be >= 0[/red]")
        raise typer.Exit(code=EXIT_ARGS)

    try:
        settings = _load_settings(config_path)
        ensure_git_available()
    except typer.Exit as exc:
        if json_output:
            typer.echo(
                json.dumps(
                    {"status": "error", "error": "configuration error", "exit_code": exc.exit_code},
                    ensure_ascii=False,
                    indent=2,
                )
            )
        raise
    except GitUnavailableError as exc:
        if json_output:
            typer.echo(
                json.dumps(
                    {"status": "error", "error": str(exc), "exit_code": EXIT_CONFIG},
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(code=EXIT_CONFIG) from exc

    _setup_scan_logging(settings, verbose)

    options = ScanOptions(
        projects=projects,
        branches=branches,
        all_branches=all_branches,
        include_extra=list(include_extra or []),
        exclude_extra=list(exclude_extra or []),
        max_file_size=max_file_size,
        dry_run=dry_run,
        force=force,
        strict=strict,
    )
    scanner = Scanner(
        settings,
        options,
        console,
        verbose=verbose,
        show_progress=not json_output,
    )

    try:
        outcome = scanner.run()
    except (ConfigError, ValueError) as exc:
        if json_output:
            typer.echo(
                json.dumps(
                    {"status": "error", "error": str(exc), "exit_code": EXIT_CONFIG},
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(code=EXIT_CONFIG) from exc
    except ScanSchemaVersionError as exc:
        if json_output:
            typer.echo(
                json.dumps(
                    {"status": "error", "error": str(exc), "exit_code": EXIT_SCHEMA},
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(code=EXIT_SCHEMA) from exc
    except (ScanDatabaseError, sqlite3.Error) as exc:
        if json_output:
            typer.echo(
                json.dumps(
                    {"status": "error", "error": f"database failure: {exc}", "exit_code": EXIT_DB},
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            console.print(f"[red]Error: database failure: {exc}[/red]")
        raise typer.Exit(code=EXIT_DB) from exc

    if json_output:
        typer.echo(scan_json_payload(outcome))
    else:
        console.print(
            "\n[bold green]Done![/bold green] "
            "[dim]scan.db is up to date[/dim]"
        )

    raise typer.Exit(code=outcome.exit_code)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
@app.callback(invoke_without_command=True)
def _default(
    ctx: typer.Context,
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config.json"),
) -> None:
    """Run analysis when no subcommand is given."""
    if ctx.invoked_subcommand is None:
        run_analysis(
            config_path=config_path,
            projects=None,
            days=None,
            model_spec=None,
            non_interactive=False,
            json_output=False,
            reanalyze=False,
        )


@app.command()
def main(
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config.json"),
    projects: Optional[list[str]] = typer.Option(
        None, "--project", "-p", help="Project URL/path (repeatable); overrides SCAN.GIT_PROJECTS"
    ),
    days: Optional[int] = typer.Option(None, "--days", "-d", help="Days to look back"),
    model_spec: Optional[str] = typer.Option(
        None, "--model", "-m", help="Model as provider/model (ollama/..., mistral/...)"
    ),
    non_interactive: bool = typer.Option(
        False, "--non-interactive", help="Never prompt; use configured default model"
    ),
    json_output: bool = typer.Option(False, "--json", help="Print a machine-readable JSON result"),
    reanalyze: bool = typer.Option(False, "--reanalyze", help="Re-analyze commits already in the DB"),
) -> None:
    """Analyze commits from one or more projects (alias: analyze)."""
    run_analysis(
        config_path, projects, days, model_spec, non_interactive, json_output, reanalyze
    )


@app.command()
def analyze(
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config.json"),
    projects: Optional[list[str]] = typer.Option(
        None, "--project", "-p", help="Project URL/path (repeatable); overrides SCAN.GIT_PROJECTS"
    ),
    days: Optional[int] = typer.Option(None, "--days", "-d", help="Days to look back"),
    model_spec: Optional[str] = typer.Option(
        None, "--model", "-m", help="Model as provider/model (ollama/..., mistral/...)"
    ),
    non_interactive: bool = typer.Option(
        False, "--non-interactive", help="Never prompt; use configured default model"
    ),
    json_output: bool = typer.Option(False, "--json", help="Print a machine-readable JSON result"),
    reanalyze: bool = typer.Option(False, "--reanalyze", help="Re-analyze commits already in the DB"),
) -> None:
    """Analyze commits from one or more projects."""
    run_analysis(
        config_path, projects, days, model_spec, non_interactive, json_output, reanalyze
    )


@app.command("report-from-db")
def report_from_db(
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config.json"),
    projects: Optional[list[str]] = typer.Option(
        None, "--project", "-p", help="Project URL/path (repeatable); default: all projects in DB"
    ),
    days: Optional[int] = typer.Option(None, "--days", "-d", help="Days to look back"),
    req: bool = typer.Option(
        False,
        "--req",
        help="Build an HTML report of requirements and sections from scan.db",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print a machine-readable JSON result"),
) -> None:
    """Generate reports from the database without calling the LLM."""
    console = _make_console(json_output)
    console.print(f"[bold green]scan-git-push[/bold green] (report from DB) v{__version__}\n")

    settings = _load_settings(config_path)
    _setup_logging(settings)

    if req:
        _generate_requirements_report(settings, console, projects, json_output)
        return

    report_days = days if days is not None else settings.analysis.git_day
    if report_days < 1 or report_days > 365:
        console.print("[red]Error: --days must be between 1 and 365[/red]")
        raise typer.Exit(code=EXIT_ARGS)

    db = _open_database(settings)
    known = db.list_projects()

    selected: list[dict[str, Any]] = []
    if projects:
        for ref in projects:
            key = url_key(ref, settings.base_dir)
            match = next((item for item in known if item["url_key"] == key), None)
            if match is None:
                console.print(f"[yellow]Project not found in DB: {ref}[/yellow]")
                continue
            selected.append(match)
    else:
        selected = known

    if not selected:
        console.print("[yellow]No projects found in database.[/yellow]")
        raise typer.Exit(code=EXIT_OK)

    report_service = ReportService(console, settings.output_dir)
    linker = RequirementLinker(
        settings.scan_db_path if settings.analysis.link_requirements else None
    )

    state = RunState()
    for project in selected:
        rows = db.get_analyses([project["id"]], report_days)
        results = results_from_rows(rows)
        for result in results:
            result.project = project["url"]
            result.project_name = repo_name(project["url"], settings.base_dir)
        linker.attach(project["url_key"], results)
        if not results:
            console.print(
                f"[yellow]{project['url']}: no analyzed commits in the period.[/yellow]"
            )

        name = repo_name(project["url"], settings.base_dir)
        report = report_service.generate_project(
            name, project["url"], results, report_days
        )
        state.reports.append(report)
        state.sections.append(
            ProjectSection(name=name, url=project["url"], results=results)
        )
        console.print(
            f"  Markdown: [green]{report.markdown}[/green]\n"
            f"  HTML:     [green]{report.html}[/green]\n"
        )

    summary = report_service.generate_summary(state.sections, report_days)
    console.print(f"  Summary:  [green]{summary.markdown}[/green]")

    if json_output:
        option = ModelOption(settings.llm.provider, settings.llm.model or "")
        payload = _json_payload(
            report_days, option, state, summary.to_dict(), EXIT_OK
        )
        payload["model"] = {}
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        console.print("\n[bold green]Done![/bold green]")


@app.command()
def scan(
    config_path: Optional[str] = typer.Option(None, "--config", "-c", help="Path to config.json"),
    projects: Optional[list[str]] = typer.Option(
        None, "--project", "-p", help="Project URL/path (repeatable); overrides SCAN.GIT_PROJECTS"
    ),
    branches: Optional[list[str]] = typer.Option(
        None, "--branch", "-b", help="Branch to scan (repeatable)"
    ),
    all_branches: bool = typer.Option(
        False, "--all-branches", help="Scan every branch (SCAN.BRANCHES=all)"
    ),
    include_extra: Optional[list[str]] = typer.Option(
        None, "--include", help="Additional include glob (repeatable)"
    ),
    exclude_extra: Optional[list[str]] = typer.Option(
        None, "--exclude", help="Additional exclude glob (repeatable)"
    ),
    max_file_size: Optional[int] = typer.Option(
        None, "--max-file-size", help="Override SCAN.MAX_FILE_SIZE in bytes"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Full scan without writing to the database"
    ),
    force: bool = typer.Option(
        False, "--force", help="Ignore an active running scan and mark it failed"
    ),
    strict: bool = typer.Option(
        False, "--strict", help="Incomplete markers produce exit code 10"
    ),
    json_output: bool = typer.Option(False, "--json", help="Print a machine-readable JSON result"),
    no_color: bool = typer.Option(False, "--no-color", help="Disable colors"),
    verbose: bool = typer.Option(False, "--verbose", help="DEBUG output to the console"),
) -> None:
    """Scan repository files for REQ-META markers into scan.db."""
    run_scan(
        config_path,
        projects,
        branches,
        all_branches,
        include_extra,
        exclude_extra,
        max_file_size,
        dry_run,
        force,
        strict or None,
        json_output,
        no_color,
        verbose,
    )


def main(argv: Optional[list[str]] = None) -> None:
    """Entry point that also understands the deprecated ``--scan`` alias."""
    args = list(sys.argv[1:] if argv is None else argv)
    if "--scan" in args:
        args = ["scan" if item == "--scan" else item for item in args]
    app(args)


if __name__ == "__main__":
    main()
