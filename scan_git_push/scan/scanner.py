"""Orchestration of the ``scan`` mode.

One project is processed at a time: probe the remote, refresh its mirror,
read every selected file from the branch tips, parse ``REQ-META`` markers and
write the result in a single transaction per project.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Sequence

from loguru import logger
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)

from scan_git_push.analysis.filters import matches_any
from scan_git_push.config import ConfigError, Settings
from scan_git_push.git.errors import GitAuthError, GitError
from scan_git_push.git.mirror import MirrorManager
from scan_git_push.git.runner import run_git
from scan_git_push.git.url import is_local_path, normalize_url, url_key
from scan_git_push.scan.database import ScanDatabase, ScanDatabaseError
from scan_git_push.scan.files import (
    BatchReader,
    BlobReadError,
    decode_blob,
    is_binary,
    is_lfs_pointer,
    last_commit_map,
    list_tree,
    sha256_blob,
    skip_reason,
)
from scan_git_push.scan.markers import (
    SCAN_PARSER_VERSION,
    MarkerError,
    Occurrence,
    ParseResult,
    format_occurrence,
    parse_markers,
)

EXIT_OK = 0
EXIT_CONFIG = 1
EXIT_REPO_UNAVAILABLE = 5
EXIT_DB = 9
EXIT_STRICT = 10
EXIT_PARTIAL = 11
EXIT_SCHEMA = 12

PROBE_TIMEOUT = 30.0
PROBE_ATTEMPTS = 3

_CREDENTIALS_RE = re.compile(
    r"(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*://)(?P<userinfo>[^/\s@]+)@"
)


def redact_credentials(value: str) -> str:
    """Remove ``user:password@`` from URLs before they reach logs or JSON."""
    return _CREDENTIALS_RE.sub(r"\g<scheme>***@", value)


# ---------------------------------------------------------------------------
# Options and results
# ---------------------------------------------------------------------------
@dataclass
class ScanOptions:
    """CLI overrides for the ``scan`` mode."""

    projects: list[str] | None = None
    branches: list[str] | None = None
    all_branches: bool = False
    include_extra: list[str] = field(default_factory=list)
    exclude_extra: list[str] = field(default_factory=list)
    max_file_size: int | None = None
    dry_run: bool = False
    force: bool = False
    strict: bool | None = None


@dataclass
class ScannedFile:
    """A file that passed all filters and must be stored."""

    path: str
    blob_hash: str
    size: int
    encoding: str
    commit_sha: str | None
    author: str | None
    committer: str | None
    changed: bool
    occurrences: list[Occurrence] = field(default_factory=list)


@dataclass
class BranchResult:
    name: str
    ok: bool = True
    files: list[ScannedFile] = field(default_factory=list)
    keep_paths: set[str] = field(default_factory=set)
    scanned: int = 0
    skipped: int = 0
    markers: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ProjectScanResult:
    project: str
    url_key: str
    status: str = "failed"
    failure_kind: str | None = None
    branches: list[str] = field(default_factory=list)
    files_scanned: int = 0
    files_skipped: int = 0
    markers_found: int = 0
    req_created: int = 0
    req_updated: int = 0
    point_created: int = 0
    point_archived: int = 0
    marker_errors: int = 0
    io_errors: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    duration: float = 0.0
    message: str | None = None

    @property
    def errors_count(self) -> int:
        return self.marker_errors + self.io_errors

    @property
    def processed(self) -> bool:
        return self.status in {"success", "partial"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "branches": self.branches,
            "status": self.status,
            "files_scanned": self.files_scanned,
            "files_skipped": self.files_skipped,
            "markers_found": self.markers_found,
            "req_created": self.req_created,
            "req_updated": self.req_updated,
            "point_created": self.point_created,
            "point_archived": self.point_archived,
            "errors": self.errors,
            "warnings": self.warnings,
            "message": self.message,
        }


@dataclass
class ScanOutcome:
    projects: list[ProjectScanResult]
    exit_code: int
    dry_run: bool = False

    @property
    def totals(self) -> dict[str, int]:
        return {
            "projects_total": len(self.projects),
            "projects_success": sum(1 for item in self.projects if item.status == "success"),
            "projects_partial": sum(1 for item in self.projects if item.status == "partial"),
            "projects_failed": sum(1 for item in self.projects if item.status == "failed"),
            "files_scanned": sum(item.files_scanned for item in self.projects),
            "files_skipped": sum(item.files_skipped for item in self.projects),
            "markers_found": sum(item.markers_found for item in self.projects),
            "errors_count": sum(item.errors_count for item in self.projects),
        }

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "projects": [item.to_dict() for item in self.projects],
            "totals": self.totals,
            "exit_code": self.exit_code,
            "dry_run": self.dry_run,
        }
        if self.exit_code != EXIT_OK:
            failed = [item for item in self.projects if item.status == "failed"]
            marker_errors = sum(item.marker_errors for item in self.projects)
            if failed:
                message = "; ".join(
                    f"{item.project}: {item.message or 'failed'}" for item in failed
                )
            elif marker_errors:
                message = f"{marker_errors} incomplete marker(s)"
            else:
                message = "one or more projects finished with warnings or errors"
            data["status"] = "error"
            data["error"] = message
        return data


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------
class Scanner:
    """Runs the ``scan`` mode for a list of projects."""

    def __init__(
        self,
        settings: Settings,
        options: ScanOptions,
        console: Console,
        verbose: bool = False,
        show_progress: bool = False,
    ) -> None:
        self.settings = settings
        self.options = options
        self.console = console
        self.verbose = verbose
        self.show_progress = show_progress and console.is_interactive
        self._progress_bar: Progress | None = None
        self._progress_task: TaskID | None = None

        scan = settings.scan
        self.dry_run = options.dry_run
        self.force = options.force
        self.strict = scan.strict if options.strict is None else options.strict
        self.include = [*scan.include, *options.include_extra]
        self.exclude = [*scan.exclude, *options.exclude_extra]
        self.exclude_secrets = list(scan.exclude_secrets)
        self.max_file_size = (
            options.max_file_size if options.max_file_size is not None else scan.max_file_size
        )
        self.branch_include = list(scan.branch_include)
        self.branch_exclude = list(scan.branch_exclude)
        self.mirror = MirrorManager(settings.mirror_dir, scan.mirror_filter)
        self.db = ScanDatabase(settings.scan_db_path)

    # ------------------------------------------------------------------ setup
    def prepare_database(self) -> None:
        """Create ``scan.db`` or validate the existing schema."""
        if self.dry_run:
            self.db.validate()
            return
        self.db.initialize(create=True)

    def _sync_include_patterns(self) -> None:
        """Align ``include_stat`` rows with ``SCAN.INCLUDE`` for all projects."""
        try:
            with self.db.transaction() as conn:
                self.db.sync_include_patterns(conn, self.settings.scan.include)
        except sqlite3.Error as exc:
            raise ScanDatabaseError(f"cannot synchronize include patterns: {exc}") from exc

    def resolve_projects(self) -> list[str]:
        raw = list(self.options.projects or self.settings.scan.git_projects)
        if not raw:
            raise ConfigError(
                "no projects configured. Set SCAN.GIT_PROJECTS or pass --project."
            )

        seen: set[str] = set()
        unique: list[str] = []
        for item in raw:
            key = url_key(item, self.settings.base_dir)
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        if not unique:
            raise ConfigError("the project list is empty")
        return unique

    # -------------------------------------------------------------------- run
    def run(self) -> ScanOutcome:
        projects = self.resolve_projects()
        self.prepare_database()
        if not self.dry_run:
            self._sync_include_patterns()
        logger.info("Scanning {} project(s)", len(projects))

        results: list[ProjectScanResult] = []
        if self.show_progress:
            with Progress(
                TextColumn("{task.description}", style="bold cyan", markup=False),
                BarColumn(),
                MofNCompleteColumn(),
                TimeElapsedColumn(),
                console=self.console,
            ) as progress:
                self._progress_bar = progress
                self._progress_task = progress.add_task("scan", total=len(projects))
                try:
                    for index, ref in enumerate(projects, start=1):
                        self._set_progress(f"Project {index}/{len(projects)}: {ref}")
                        results.append(self._scan_project(ref))
                        progress.advance(self._progress_task)
                        progress.refresh()
                finally:
                    self._progress_bar = None
                    self._progress_task = None
        else:
            for index, ref in enumerate(projects, start=1):
                self.console.print(
                    f"[bold cyan]Project {index}/{len(projects)}:[/bold cyan] {ref}"
                )
                results.append(self._scan_project(ref))

        exit_code = self._exit_code(results)
        self._gc_hashes(results)
        outcome = ScanOutcome(results, exit_code, self.dry_run)
        self._print_summary(outcome)
        return outcome

    def _set_progress(self, description: str) -> None:
        """Update the interactive progress bar, when one is active."""
        if self._progress_bar is not None and self._progress_task is not None:
            self._progress_bar.update(self._progress_task, description=description)

    # ---------------------------------------------------------------- project
    def _scan_project(self, ref: str) -> ProjectScanResult:
        base_dir = self.settings.base_dir
        display = normalize_url(ref, base_dir)
        result = ProjectScanResult(project=display, url_key=url_key(ref, base_dir))
        project_log = logger.bind(url_key=result.url_key)
        started = time.monotonic()

        project_id: int | None = None
        run_id: int | None = None

        try:
            probe_url = normalize_url(ref, base_dir) if is_local_path(ref) else ref
            self._probe(probe_url)
        except GitAuthError as exc:
            result.failure_kind = "access"
            result.status = "failed"
            result.message = redact_credentials(f"authentication failed: {exc}")
            result.errors.append(
                {"project": display, "type": "auth", "error": result.message}
            )
            result.io_errors += 1
            result.duration = time.monotonic() - started
            self._print_error(display, "access", result.message)
            project_log.error("Cannot access {}: {}", display, result.message)
            return result
        except (GitError, OSError) as exc:
            result.failure_kind = "access"
            result.status = "failed"
            result.message = redact_credentials(str(exc))
            result.errors.append(
                {"project": display, "type": "access", "error": result.message}
            )
            result.io_errors += 1
            result.duration = time.monotonic() - started
            self._print_error(display, "access", result.message)
            project_log.error("Cannot access {}: {}", display, result.message)
            return result

        if not self.dry_run:
            try:
                with self.db.transaction() as conn:
                    project_id = self.db.ensure_project(conn, ref, base_dir)
                    active = self.db.resolve_active_run(conn, project_id, self.force)
                    if active is not None:
                        result.failure_kind = "busy"
                        result.status = "failed"
                        result.message = (
                            f"another scan is running since {active['started_at']} "
                            "(use --force to override)"
                        )
                        result.io_errors += 1
                        result.errors.append(
                            {"project": display, "type": "busy", "error": result.message}
                        )
                        result.duration = time.monotonic() - started
                        self._print_error(display, "busy", result.message)
                        project_log.warning("{}: {}", display, result.message)
                        return result
                    run_id = self.db.create_run(conn, project_id)
            except ScanDatabaseError:
                raise
            except sqlite3.Error as exc:
                raise ScanDatabaseError(f"cannot prepare {self.db.db_path}: {exc}") from exc

        try:
            mirror_path = self.mirror.ensure(ref, base_dir=base_dir, update=True)
        except GitAuthError as exc:
            result.failure_kind = "access"
            result.status = "failed"
            result.message = redact_credentials(f"authentication failed: {exc}")
            result.errors.append(
                {"project": display, "type": "auth", "error": result.message}
            )
            result.io_errors += 1
            if run_id is not None:
                self.db.fail_run(run_id, result.message)
            result.duration = time.monotonic() - started
            self._print_error(display, "access", result.message)
            project_log.error("Cannot clone/fetch {}: {}", display, result.message)
            return result
        except (GitError, OSError) as exc:
            result.failure_kind = "access"
            result.status = "failed"
            result.message = redact_credentials(str(exc))
            result.errors.append(
                {"project": display, "type": "access", "error": result.message}
            )
            result.io_errors += 1
            if run_id is not None:
                self.db.fail_run(run_id, result.message)
            result.duration = time.monotonic() - started
            self._print_error(display, "access", result.message)
            project_log.error("Cannot clone/fetch {}: {}", display, result.message)
            return result

        try:
            selected, branches_warnings = self._select_branches(mirror_path)
        except (GitError, OSError) as exc:
            result.failure_kind = "access"
            result.status = "failed"
            result.message = redact_credentials(str(exc))
            result.errors.append(
                {"project": display, "type": "git", "error": result.message}
            )
            result.io_errors += 1
            if run_id is not None:
                self.db.fail_run(run_id, result.message)
            result.duration = time.monotonic() - started
            self._print_error(display, "branches", result.message)
            return result

        result.branches = selected
        for warning in branches_warnings:
            result.warnings.append(warning)
            self._print_warning(display, warning)
            project_log.warning(warning)

        branch_results: list[BranchResult] = []
        for branch in selected:
            branch_result = self._scan_branch(
                mirror_path, branch, project_id, display, project_log
            )
            branch_results.append(branch_result)
            result.files_scanned += branch_result.scanned
            result.files_skipped += branch_result.skipped
            result.markers_found += branch_result.markers
            result.errors.extend(branch_result.errors)
            result.warnings.extend(branch_result.warnings)
            result.io_errors += sum(
                1 for error in branch_result.errors if error.get("type") != "marker"
            )
            result.marker_errors += sum(
                1 for error in branch_result.errors if error.get("type") == "marker"
            )

        if not selected:
            result.warnings.append("no branches selected; nothing to scan")
            self._print_warning(display, "no branches selected; nothing to scan")

        if not self.dry_run and project_id is not None and run_id is not None:
            result.status = "success" if all(item.ok for item in branch_results) else "partial"
            if result.io_errors:
                result.status = "partial"
            try:
                self._write_project(
                    project_id, run_id, ref, branch_results, result, project_log
                )
            except sqlite3.Error as exc:
                self.db.fail_run(run_id, str(exc))
                raise ScanDatabaseError(f"cannot write project {display}: {exc}") from exc
        else:
            result.status = "success" if all(item.ok for item in branch_results) else "partial"
            if result.io_errors:
                result.status = "partial"

        result.duration = time.monotonic() - started
        return result

    # ---------------------------------------------------------------- probing
    def _probe(self, display: str) -> None:
        last_error: GitError | None = None
        for attempt in range(PROBE_ATTEMPTS):
            try:
                run_git(["ls-remote", "--heads", display], timeout=PROBE_TIMEOUT)
                return
            except GitError as exc:
                last_error = exc
                if attempt + 1 < PROBE_ATTEMPTS:
                    time.sleep(2**attempt)
        if last_error is not None:
            raise last_error

    # --------------------------------------------------------------- branches
    def _select_branches(self, mirror_path: Path) -> tuple[list[str], list[str]]:
        available = self.mirror.branches(mirror_path, raise_on_error=True)
        warnings: list[str] = []
        scan = self.settings.scan

        if self.options.branches:
            requested = list(self.options.branches)
            missing = [name for name in requested if name not in available]
            for name in missing:
                warnings.append(f"branch not found: {name}")
            selected = [name for name in requested if name in available]
        elif self.options.all_branches:
            selected = list(available)
        elif scan.branches == "all":
            selected = list(available)
        elif scan.branches == "default":
            default = self.mirror.default_branch(mirror_path)
            selected = [default] if default and default in available else []
            if default and default not in available:
                warnings.append(f"default branch not found: {default}")
        else:
            requested = [str(name).strip() for name in scan.branches if str(name).strip()]
            missing = [name for name in requested if name not in available]
            for name in missing:
                warnings.append(f"branch not found: {name}")
            selected = [name for name in requested if name in available]

        if self.branch_include:
            selected = [name for name in selected if matches_any(name, self.branch_include)]
        if self.branch_exclude:
            selected = [name for name in selected if not matches_any(name, self.branch_exclude)]

        # Deterministic order is required for name-conflict resolution.
        return sorted(set(selected)), warnings

    @property
    def _delete_missing_branches(self) -> bool:
        if self.options.branches:
            return False
        return self.options.all_branches or self.settings.scan.branches == "all"

    # ----------------------------------------------------------------- branch
    def _scan_branch(
        self,
        mirror_path: Path,
        branch: str,
        project_id: int | None,
        display: str,
        project_log: Any,
    ) -> BranchResult:
        branch_result = BranchResult(name=branch)
        pending_commits: set[str] = set()
        previous: dict[str, dict[str, Any]] = {}
        if not self.dry_run and project_id is not None:
            previous = self.db.get_snapshots(project_id, branch)

        try:
            entries = list_tree(mirror_path, branch)
        except GitError as exc:
            branch_result.ok = False
            branch_result.errors.append(
                {
                    "type": "branch",
                    "branch": branch,
                    "message": f"cannot list tree: {exc}",
                }
            )
            self._print_error(display, f"{branch}: cannot list tree", str(exc))
            project_log.error("{}: cannot list tree of {}: {}", display, branch, exc)
            return branch_result

        reader: BatchReader | None = None
        try:
            for entry in entries:
                self._set_progress(
                    f"{display} [{branch}] {entry.path} | "
                    f"markers: {branch_result.markers} "
                    f"errors: {len(branch_result.errors)}"
                )
                reason = skip_reason(
                    entry,
                    self.include,
                    self.exclude,
                    self.exclude_secrets,
                    self.max_file_size,
                )
                if reason is not None:
                    branch_result.skipped += 1
                    if reason == "not included":
                        project_log.debug("{} {}: {}", display, entry.path, reason)
                    else:
                        self._print_skip(display, entry.path, reason)
                        project_log.debug("{} {}: {}", display, entry.path, reason)
                    continue

                data: bytes | None = None
                last_error: BlobReadError | None = None
                for _attempt in range(2):
                    try:
                        if reader is None:
                            reader = BatchReader(mirror_path)
                        data = reader.read(entry.sha)
                        break
                    except (BlobReadError, OSError) as exc:
                        last_error = (
                            exc
                            if isinstance(exc, BlobReadError)
                            else BlobReadError(str(exc))
                        )
                        if reader is not None:
                            reader.close()
                            reader = None

                if data is None:
                    branch_result.ok = False
                    branch_result.skipped += 1
                    branch_result.errors.append(
                        {
                            "type": "blob",
                            "branch": branch,
                            "file": entry.path,
                            "message": str(last_error or "cannot read blob"),
                        }
                    )
                    self._print_error(
                        display, f"{branch}:{entry.path}", str(last_error or "cannot read blob")
                    )
                    project_log.error(
                        "{} {}:{}: {}", display, branch, entry.path, last_error
                    )
                    continue

                blob_hash = sha256_blob(data)
                size = len(data)
                previous_row = previous.get(entry.path)

                if (
                    previous_row is not None
                    and previous_row["blob_hash"] == blob_hash
                    and previous_row["parser_version"] == SCAN_PARSER_VERSION
                ):
                    branch_result.scanned += 1
                    branch_result.keep_paths.add(entry.path)
                    branch_result.markers += int(previous_row.get("marker_count") or 0)
                    # Strict mode must still report invalid markers in unchanged
                    # files, so those files are parsed without touching the DB.
                    if self.strict:
                        decoded = decode_blob(
                            data,
                            self.settings.scan.encoding,
                            self.settings.scan.fallback_encoding or None,
                        )
                        if decoded is not None:
                            parsed = parse_markers(decoded.text, entry.path)
                            self._record_marker_diagnostics(
                                branch_result,
                                parsed,
                                display,
                                branch,
                                project_log,
                            )
                    # Unchanged files keep the commit metadata from the previous
                    # snapshot; the history walk is limited to changed paths.
                    previous_sha = previous_row.get("commit_sha")
                    if previous_sha is None:
                        pending_commits.add(entry.path)
                    branch_result.files.append(
                        ScannedFile(
                            path=entry.path,
                            blob_hash=blob_hash,
                            size=size,
                            encoding=previous_row["encoding"],
                            commit_sha=previous_sha,
                            author=previous_row.get("author"),
                            committer=previous_row.get("committer"),
                            changed=False,
                        )
                    )
                    continue

                if is_binary(data):
                    branch_result.skipped += 1
                    self._print_skip(display, entry.path, "binary")
                    project_log.debug("{} {}: binary", display, entry.path)
                    continue

                if is_lfs_pointer(data):
                    branch_result.skipped += 1
                    self._print_skip(display, entry.path, "lfs pointer")
                    project_log.debug("{} {}: lfs pointer", display, entry.path)
                    continue

                decoded = decode_blob(
                    data,
                    self.settings.scan.encoding,
                    self.settings.scan.fallback_encoding or None,
                )
                if decoded is None:
                    branch_result.skipped += 1
                    self._print_skip(display, entry.path, "encoding")
                    project_log.debug("{} {}: encoding", display, entry.path)
                    continue

                parsed: ParseResult = parse_markers(decoded.text, entry.path)
                self._record_marker_diagnostics(
                    branch_result, parsed, display, branch, project_log
                )

                branch_result.scanned += 1
                branch_result.markers += len(parsed.occurrences)
                branch_result.keep_paths.add(entry.path)

                for occurrence in parsed.occurrences:
                    self._print_ok(display, branch, entry.path, occurrence)
                    project_log.info(
                        "{} {}:{} {}", display, entry.path, occurrence.line_no,
                        format_occurrence(occurrence),
                    )

                if not parsed.occurrences and not parsed.errors:
                    if self.verbose:
                        self._print_info(display, entry.path, "no REQ-META markers")
                    else:
                        project_log.debug("{} {}: no REQ-META markers", display, entry.path)

                pending_commits.add(entry.path)
                branch_result.files.append(
                    ScannedFile(
                        path=entry.path,
                        blob_hash=blob_hash,
                        size=size,
                        encoding=decoded.encoding,
                        commit_sha=None,
                        author=None,
                        committer=None,
                        changed=True,
                        occurrences=parsed.occurrences,
                    )
                )
        finally:
            if reader is not None:
                reader.close()

        if pending_commits:
            try:
                commits = last_commit_map(
                    mirror_path, branch, sorted(pending_commits)
                )
            except GitError as exc:
                message = redact_credentials(f"cannot read commit history: {exc}")
                branch_result.ok = False
                branch_result.errors.append(
                    {"type": "commit-history", "branch": branch, "message": message}
                )
                self._print_error(display, f"{branch}: commit history", message)
                project_log.error(
                    "{}: cannot read commit history of {}: {}",
                    display,
                    branch,
                    message,
                )
            else:
                for item in branch_result.files:
                    if item.commit_sha is not None:
                        continue
                    info = commits.get(item.path)
                    if info is not None:
                        item.commit_sha = info.sha
                        item.author = info.author
                        item.committer = info.committer

        return branch_result

    # -------------------------------------------------------------------- write
    def _record_marker_diagnostics(
        self,
        branch_result: BranchResult,
        parsed: ParseResult,
        display: str,
        branch: str,
        project_log: Any,
    ) -> None:
        for error in parsed.errors:
            issue = error.to_dict()
            issue["type"] = "marker"
            issue["branch"] = branch
            branch_result.errors.append(issue)
            self._print_marker_error(display, error)
            project_log.warning("{} {}: {}", display, branch, error.describe())
        for warning in parsed.warnings:
            message = warning.describe()
            branch_result.warnings.append(message)
            self._print_warning(display, message)
            project_log.debug("{}: {}", display, message)

    def _write_project(
        self,
        project_id: int,
        run_id: int,
        ref: str,
        branch_results: Sequence[BranchResult],
        result: ProjectScanResult,
        project_log: Any,
    ) -> None:
        created_reqs: set[int] = set()
        updated_reqs: set[int] = set()
        created_points: set[int] = set()
        conflicts: list[str] = []
        name_origins: dict[int, tuple[str, str]] = {}

        with self.db.transaction() as conn:
            for branch_result in branch_results:
                for scanned in branch_result.files:
                    self.db.upsert_file_hash(conn, scanned.blob_hash, scanned.size)
                    file_id = self.db.upsert_snapshot(
                        conn,
                        project_id=project_id,
                        branch=branch_result.name,
                        path=scanned.path,
                        blob_hash=scanned.blob_hash,
                        commit_sha=scanned.commit_sha,
                        author=scanned.author,
                        committer=scanned.committer,
                        encoding=scanned.encoding,
                        parser_version=SCAN_PARSER_VERSION,
                    )
                    if not scanned.changed:
                        continue

                    occurrences: list[tuple[int, int, str]] = []
                    for occurrence in scanned.occurrences:
                        req_id, created, old_name, new_name = self.db.upsert_req(
                            conn, occurrence.code, occurrence.name
                        )
                        if created:
                            created_reqs.add(req_id)
                        if old_name is not None and new_name is not None:
                            updated_reqs.add(req_id)
                            current = f"{branch_result.name}:{scanned.path}"
                            previous = name_origins.get(req_id)
                            previous_label = (
                                f"{previous[0]}:{previous[1]}"
                                if previous is not None
                                else "previous scan"
                            )
                            conflicts.append(
                                f"name conflict id={occurrence.code}: "
                                f'"{old_name}" ({previous_label}) vs '
                                f'"{new_name}" ({current})'
                            )
                        if occurrence.name:
                            name_origins[req_id] = (branch_result.name, scanned.path)
                        point_id, point_created = self.db.upsert_point(
                            conn, req_id, occurrence.point
                        )
                        if point_created:
                            created_points.add(point_id)
                        occurrences.append(
                            (point_id, occurrence.line_no, occurrence.raw_marker)
                        )
                    self.db.replace_occurrences(conn, file_id, occurrences)

                if branch_result.ok:
                    self.db.reconcile_branch(
                        conn,
                        project_id,
                        branch_result.name,
                        branch_result.keep_paths,
                    )

            if self._delete_missing_branches:
                self.db.delete_missing_branches(
                    conn, project_id, [item.name for item in branch_results]
                )

            successful_branches = [
                item.name for item in branch_results if item.ok
            ]
            self.db.ensure_include_patterns(
                conn,
                project_id,
                successful_branches,
                self.settings.scan.include,
            )

            archived_points, _archived_reqs = self.db.recompute_statuses(conn)

            self.db.recompute_include_stats(
                conn, project_id, successful_branches
            )

            self.db.finish_run(
                conn,
                run_id,
                status=result.status,
                branches=result.branches,
                files_scanned=result.files_scanned,
                files_skipped=result.files_skipped,
                markers_found=result.markers_found,
                errors_count=result.errors_count,
                message=result.message
                or ("dry run" if self.dry_run else None),
            )

        result.req_created = len(created_reqs)
        result.req_updated = len(updated_reqs)
        result.point_created = len(created_points)
        result.point_archived = archived_points

        for conflict in conflicts:
            result.warnings.append(conflict)
            self._print_warning(result.project, conflict)
            project_log.warning(conflict)

    # ---------------------------------------------------------------------- gc
    def _gc_hashes(self, results: Sequence[ProjectScanResult]) -> None:
        if self.dry_run or not self.settings.scan.gc_hashes:
            return
        if any(item.status == "failed" for item in results):
            return
        try:
            with self.db.transaction() as conn:
                removed = self.db.gc_hashes(conn)
            if removed:
                logger.info("Removed {} unused file hash(es)", removed)
        except sqlite3.Error as exc:
            logger.warning("GC of file hashes failed: {}", exc)

    # ------------------------------------------------------------------- codes
    def _exit_code(self, results: Sequence[ProjectScanResult]) -> int:
        if not results:
            return EXIT_CONFIG
        processed = [item for item in results if item.processed]
        if not processed:
            if all(item.failure_kind == "access" for item in results):
                return EXIT_REPO_UNAVAILABLE
            return EXIT_PARTIAL
        if any(item.status != "success" for item in results):
            return EXIT_PARTIAL
        if self.strict and any(item.marker_errors for item in results):
            return EXIT_STRICT
        return EXIT_OK

    # ---------------------------------------------------------------- printing
    def _print_summary(self, outcome: ScanOutcome) -> None:
        for project in outcome.projects:
            self.console.print()
            self.console.print(f"[bold]=== {project.project} ===[/bold]")
            self.console.print(f"Branches:       {', '.join(project.branches) or '-'}")
            self.console.print(f"Files scanned:  {project.files_scanned}")
            self.console.print(f"Files skipped:  {project.files_skipped}")
            self.console.print(f"Markers found:  {project.markers_found}")
            self.console.print(f"REQ created:    {project.req_created}")
            self.console.print(f"REQ updated:    {project.req_updated}")
            self.console.print(f"POINT created:  {project.point_created}")
            self.console.print(f"POINT archived: {project.point_archived}")
            self.console.print(f"Errors:         {project.errors_count}")
            self.console.print(f"Duration:       {_format_duration(project.duration)}")
            self.console.print(f"Status:         {project.status}")

        totals = outcome.totals
        self.console.print()
        self.console.print("[bold]--- Total ---[/bold]")
        self.console.print(
            f"Projects:       {totals['projects_total']} "
            f"(success: {totals['projects_success']}, "
            f"partial: {totals['projects_partial']}, "
            f"failed: {totals['projects_failed']})"
        )
        self.console.print(f"Files scanned:  {totals['files_scanned']}")
        self.console.print(f"Files skipped:  {totals['files_skipped']}")
        self.console.print(f"Markers found:  {totals['markers_found']}")
        self.console.print(f"Errors:         {totals['errors_count']}")
        self.console.print(
            "Duration:       "
            f"{_format_duration(sum(item.duration for item in outcome.projects))}"
        )
        self.console.print(f"Exit code:      {outcome.exit_code}")
        if outcome.dry_run:
            self.console.print("[dim]Dry run: the database was not modified.[/dim]")

    def _print_ok(
        self, project: str, branch: str, path: str, occurrence: Occurrence
    ) -> None:
        self.console.print(
            f"[green][OK][/green]    {project}  {branch}:{path}:{occurrence.line_no}  "
            f"{format_occurrence(occurrence)}"
        )

    def _print_marker_error(self, project: str, error: MarkerError) -> None:
        self.console.print(f"[red][ERROR][/red] {project}  {error.describe()}")

    def _print_error(self, project: str, location: str, message: str) -> None:
        self.console.print(f"[red][ERROR][/red] {project}  {location}  {message}")

    def _print_warning(self, project: str, message: str) -> None:
        self.console.print(f"[yellow][WARN][/yellow]  {project}  {message}")

    def _print_skip(self, project: str, path: str, reason: str) -> None:
        self.console.print(f"[dim][SKIP][/dim]  {project}  {path}  {reason}")

    def _print_info(self, project: str, path: str, message: str) -> None:
        self.console.print(f"[INFO]  {project}  {path}  {message}")


# ---------------------------------------------------------------------------
def _format_duration(seconds: float) -> str:
    return str(timedelta(seconds=int(seconds)))


def json_payload(outcome: ScanOutcome) -> str:
    """Serialize the outcome exactly as required by ``dop_tz01.md`` (11.4)."""
    return json.dumps(outcome.to_dict(), ensure_ascii=False, indent=2)
