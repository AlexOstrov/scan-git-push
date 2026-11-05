"""Integration tests for the ``scan`` mode on temporary git repositories."""

from __future__ import annotations

import io
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

from scan_git_push.cli import app, main
from scan_git_push.config import Settings
from scan_git_push.database import DatabaseManager
from scan_git_push.git.errors import GitError
from scan_git_push.scan.database import ScanDatabase
from scan_git_push.scan.scanner import (
    ScanOptions,
    Scanner,
    redact_credentials,
)


# ---------------------------------------------------------------------------
# Repository helpers
# ---------------------------------------------------------------------------
def git(args: list[str], cwd: Path, input_bytes: bytes | None = None) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        input=input_bytes,
        capture_output=True,
        check=True,
    )
    return completed.stdout.decode("utf-8", "replace")


def write_text(repo: Path, path: str, text: str, encoding: str = "utf-8") -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(text.encode(encoding))


def commit(repo: Path, message: str) -> None:
    git(["add", "-A"], repo)
    git(["commit", "-m", message], repo)


def make_repo(tmp_path: Path, name: str = "repo") -> Path:
    repo = tmp_path / name
    repo.mkdir()
    git(["init", "-b", "main"], repo)
    git(["config", "user.email", "test@example.com"], repo)
    git(["config", "user.name", "Tester"], repo)
    git(["config", "core.autocrlf", "false"], repo)

    write_text(
        repo,
        "docs/req.md",
        "\n".join(
            [
                "# Requirements",
                '<!-- REQ-META id="REQ_1" point="1.1" name="Первое требование" -->',
                '<!-- REQ-META id="REQ_1" point="1.1" name="Первое требование" -->',
                '<!-- REQ-META id="REQ_2" point="2.1" name="Другое имя" -->',
                '<!-- REQ-META id="REQ_7" point="7.1" name="Удаляемое" -->',
                '<!-- REQ-META id="REQ_3" name="Без пункта" -->',
                '<!-- REQ-META id="REQ 4" point="3.1" -->',
                "<!-- TODO: обычный комментарий -->",
                '<!-- "ID_REQ" = "REQ_9" "POINT" = "9.9" -->',
            ]
        )
        + "\n",
    )
    write_text(
        repo,
        "docs/second.txt",
        '<!-- REQ-META id="REQ_1" point="1.1" -->\n'
        '<!-- REQ-META id="REQ_2" point="2.2" name="Второй пункт" -->\n',
    )
    write_text(
        repo,
        "docs/notes.md",
        '<!-- REQ-META id="REQ_1" point="1.1" -->\n',
    )
    write_text(repo, "src/code.py", 'MARKER = "<!-- REQ-META id=\\"REQ_8\\" point=\\"8.1\\" -->"\n')
    write_text(repo, "docs/secret.md", '<!-- REQ-META id="REQ_SECRET" point="9.1" -->\n')
    write_text(repo, "vendor/skip.md", '<!-- REQ-META id="REQ_VENDOR" point="9.2" -->\n')
    write_text(
        repo,
        "docs/cp1251.txt",
        '<!-- REQ-META id="REQ_5" point="5.1" name="Требование" -->\n',
        encoding="cp1251",
    )
    write_text(
        repo,
        "docs/lfs.md",
        "version https://git-lfs.github.com/spec/v1\n"
        "oid sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef\n"
        "size 123\n",
    )
    (repo / "docs" / "binary.md").write_bytes(b"\x00\x01\x02")
    write_text(repo, "docs/large.md", "x" * 5000)
    commit(repo, "initial")

    git(["checkout", "-b", "feature"], repo)
    write_text(
        repo,
        "docs/feature.md",
        '<!-- REQ-META id="REQ_1" point="1.1" -->\n'
        '<!-- REQ-META id="REQ_6" point="6.1" name="Ветка" -->\n',
    )
    commit(repo, "feature")
    git(["checkout", "main"], repo)

    # Symlink and submodule are created through the index to work on Windows.
    link_blob = git(["hash-object", "-w", "--stdin"], repo, input_bytes=b"docs/req.md").strip()
    git(
        ["update-index", "--add", "--cacheinfo", f"120000,{link_blob},docs/link.md"],
        repo,
    )
    head_sha = git(["rev-parse", "HEAD"], repo).strip()
    git(
        ["update-index", "--add", "--cacheinfo", f"160000,{head_sha},docs/submodule.md"],
        repo,
    )
    git(["commit", "-m", "links"], repo)
    return repo


def make_config(
    tmp_path: Path,
    projects: list[str],
    *,
    max_file_size: int = 2000,
    exclude: list[str] | None = None,
    exclude_secrets: list[str] | None = None,
    db_path: str = "db/scan.db",
    branches: str | list[str] = "all",
    branch_include: list[str] | None = None,
    branch_exclude: list[str] | None = None,
    log_path: str = "logs/scan.log",
) -> Path:
    secrets = tmp_path / ".secrets"
    secrets.write_text("MISTRAL_API_KEY=test\n", encoding="utf-8")
    data = {
        "SECRETS_PATH": str(secrets),
        "SCAN": {
            "GIT_PROJECTS": projects,
            "DB_PATH": db_path,
            "MIRROR_DIR": "db/repos/",
            "MIRROR_FILTER": "none",
            "BRANCHES": branches,
            "BRANCH_INCLUDE": branch_include or [],
            "BRANCH_EXCLUDE": branch_exclude or [],
            "MAX_FILE_SIZE": max_file_size,
            "EXCLUDE": exclude or [],
            "EXCLUDE_SECRETS": exclude_secrets or [],
            "LOG_PATH": log_path,
        },
        "ANALYSIS": {"LINK_REQUIREMENTS": False},
        "LLM": {"NON_INTERACTIVE": True},
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def run_scanner(
    config: Path, options: ScanOptions | None = None
) -> tuple[object, str]:
    settings = Settings.from_file(config)
    stream = io.StringIO()
    console = Console(file=stream, width=200, no_color=True)
    scanner = Scanner(settings, options or ScanOptions(), console)
    outcome = scanner.run()
    return outcome, stream.getvalue()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path)


@pytest.fixture
def config(tmp_path: Path, repo: Path) -> Path:
    return make_config(
        tmp_path,
        [str(repo)],
        exclude=["vendor/**"],
        exclude_secrets=["secret.md"],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestSchemaAndInitialScan:
    def test_first_scan_populates_database(self, config: Path, repo: Path) -> None:
        outcome, output = run_scanner(config)
        assert outcome.exit_code == 0
        assert len(outcome.projects) == 1
        project = outcome.projects[0]
        assert project.status == "success"
        assert project.branches == ["feature", "main"]
        assert project.files_scanned > 0
        assert project.files_skipped >= 6
        assert project.marker_errors == 4  # two invalid markers on both branches
        assert project.io_errors == 0

        db = ScanDatabase(Settings.from_file(config).scan_db_path)
        assert db.schema_version() == 3
        occurrences = db.occurrences(project.project)
        points = {(item["code"], item["point"]) for item in occurrences}
        assert ("REQ_1", "1.1") in points
        assert ("REQ_2", "2.1") in points
        assert ("REQ_2", "2.2") in points
        assert ("REQ_5", "5.1") in points
        assert ("REQ_6", "6.1") in points
        assert ("REQ_7", "7.1") in points
        assert not any(code.startswith("REQ_SECRET") for code, _ in points)
        assert not any(code.startswith("REQ_VENDOR") for code, _ in points)
        assert not any(code.startswith("REQ_8") for code, _ in points)

        # Same (id, point) in one file on two lines and in other files is allowed.
        req1 = [item for item in occurrences if item["code"] == "REQ_1"]
        assert len(req1) >= 6

        # Skip diagnostics are printed.
        assert "binary" in output
        assert "lfs pointer" in output
        assert "symlink" in output
        assert "submodule" in output
        assert "too large" in output
        assert "secret pattern" in output
        assert "excluded pattern" in output
        assert "legacy marker format" in output

        # Paths are stored in POSIX form regardless of the host OS.
        with db.connect(readonly=True) as conn:
            paths = {row[0] for row in conn.execute("SELECT path FROM file_snapshot")}
        assert paths and all("\\" not in path for path in paths)

        # Contents are not stored: only 64-char hashes.
        with db.connect(readonly=True) as conn:
            hashes = [row[0] for row in conn.execute("SELECT hash FROM file_hash")]
            assert hashes and all(len(value) == 64 for value in hashes)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(file_hash)")}
            assert "content" not in columns

    def test_point_trailing_dot_is_normalized(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        write_text(
            repo,
            "docs/point.md",
            '<!-- REQ-META id="TR_3" point="5.5.1." name="Требование 3" -->\n',
        )
        commit(repo, "trailing point")
        config = make_config(
            tmp_path,
            [str(repo)],
            exclude=["vendor/**"],
            exclude_secrets=["secret.md"],
        )

        outcome, _ = run_scanner(config)
        assert outcome.exit_code == 0
        db = ScanDatabase(Settings.from_file(config).scan_db_path)
        occurrences = db.occurrences(outcome.projects[0].project)
        points = {(item["code"], item["point"]) for item in occurrences}
        assert ("TR_3", "5.5.1") in points
        assert not any(point.endswith(".") for _, point in points)

    def test_encoding_and_commit_metadata(self, config: Path) -> None:
        outcome, _ = run_scanner(config)
        db = ScanDatabase(Settings.from_file(config).scan_db_path)
        with db.connect(readonly=True) as conn:
            rows = {
                row["path"]: row
                for row in conn.execute(
                    "SELECT path, encoding, commit_sha, author, committer FROM file_snapshot"
                )
            }
        assert rows["docs/cp1251.txt"]["encoding"] == "cp1251"
        assert rows["docs/req.md"]["encoding"] == "utf-8"
        assert rows["docs/req.md"]["commit_sha"]
        assert rows["docs/req.md"]["author"] == "Tester"
        assert rows["docs/req.md"]["committer"] == "Tester"
        assert outcome.projects[0].markers_found > 0

    def test_name_conflict_warning_includes_values_and_paths(self, config: Path) -> None:
        outcome, output = run_scanner(config)
        assert outcome.exit_code == 0
        conflicts = [
            warning
            for warning in outcome.projects[0].warnings
            if warning.startswith("name conflict id=REQ_2")
        ]
        assert conflicts
        assert any(
            "Другое имя" in warning and "Второй пункт" in warning
            for warning in conflicts
        )
        assert any(
            "docs/req.md" in warning and "docs/second.txt" in warning
            for warning in conflicts
        )
        assert "name conflict id=REQ_2" in output

    def test_second_run_is_idempotent(self, config: Path) -> None:
        first, _ = run_scanner(config)
        db = ScanDatabase(Settings.from_file(config).scan_db_path)
        counts_first = {
            table: db.count(table)
            for table in ("req", "point", "point_content", "file_hash", "file_snapshot")
        }

        second, _ = run_scanner(config)
        counts_second = {
            table: db.count(table)
            for table in ("req", "point", "point_content", "file_hash", "file_snapshot")
        }
        assert counts_first == counts_second
        assert second.projects[0].req_created == 0
        assert second.projects[0].point_created == 0
        assert second.projects[0].files_scanned == first.projects[0].files_scanned
        assert second.projects[0].markers_found == first.projects[0].markers_found
        assert db.count("scan_run") == 2


class TestReconciliation:
    def test_removed_marker_archives_point_and_req(self, config: Path, repo: Path) -> None:
        main_only = ScanOptions(branches=["main"])
        run_scanner(config, main_only)
        db = ScanDatabase(Settings.from_file(config).scan_db_path)

        write_text(repo, "docs/req.md", "# Requirements\n")
        commit(repo, "remove markers")
        outcome, _ = run_scanner(config, main_only)
        assert outcome.exit_code == 0

        with db.connect(readonly=True) as conn:
            req = conn.execute("SELECT id, status FROM req WHERE code = 'REQ_7'").fetchone()
            assert req is not None and req["status"] == 0
            point = conn.execute(
                "SELECT status FROM point WHERE req_id = ? AND point = '7.1'", (req["id"],)
            ).fetchone()
            assert point is not None and point["status"] == 0

        write_text(
            repo, "docs/req.md", '<!-- REQ-META id="REQ_7" point="7.1" -->\n'
        )
        commit(repo, "restore marker")
        run_scanner(config, main_only)
        with db.connect(readonly=True) as conn:
            assert conn.execute(
                "SELECT status FROM req WHERE code = 'REQ_7'"
            ).fetchone()["status"] == 1
            assert conn.execute(
                "SELECT status FROM point WHERE point = '7.1'"
            ).fetchone()["status"] == 1

    def test_deleted_file_snapshot_is_removed(self, config: Path, repo: Path) -> None:
        main_only = ScanOptions(branches=["main"])
        run_scanner(config, main_only)
        db = ScanDatabase(Settings.from_file(config).scan_db_path)
        with db.connect(readonly=True) as conn:
            old_hash = conn.execute(
                "SELECT blob_hash FROM file_snapshot WHERE path = 'docs/second.txt'"
            ).fetchone()[0]
        (repo / "docs" / "second.txt").unlink()
        commit(repo, "delete file")
        run_scanner(config, main_only)
        with db.connect(readonly=True) as conn:
            paths = {
                row[0] for row in conn.execute("SELECT path FROM file_snapshot")
            }
            hashes = {row[0] for row in conn.execute("SELECT hash FROM file_hash")}
        assert "docs/second.txt" not in paths
        assert "docs/req.md" in paths
        assert old_hash not in hashes

    def test_deleted_branch_snapshots_are_removed(self, config: Path, repo: Path) -> None:
        run_scanner(config)
        git(["branch", "-D", "feature"], repo)
        run_scanner(config)
        db = ScanDatabase(Settings.from_file(config).scan_db_path)
        with db.connect(readonly=True) as conn:
            branches = {
                row[0] for row in conn.execute("SELECT DISTINCT branch FROM file_snapshot")
            }
        assert branches == {"main"}


class TestMultipleProjects:
    def test_same_req_in_two_projects_is_shared(self, tmp_path: Path) -> None:
        first = make_repo(tmp_path, "repo-a")
        second = make_repo(tmp_path, "repo-b")
        config = make_config(tmp_path, [str(first), str(second)])
        outcome, _ = run_scanner(config)
        assert outcome.exit_code == 0

        db = ScanDatabase(Settings.from_file(config).scan_db_path)
        with db.connect(readonly=True) as conn:
            rows = conn.execute(
                "SELECT r.code, r.id, COUNT(DISTINCT fs.git_project_id) AS projects "
                "FROM req r "
                "JOIN point p ON p.req_id = r.id "
                "JOIN point_content pc ON pc.point_id = p.id "
                "JOIN file_snapshot fs ON fs.id = pc.file_id "
                "WHERE r.code = 'REQ_1' "
                "GROUP BY r.id, r.code"
            ).fetchall()
        # Requirement ids are global: both projects contribute occurrences to
        # the same requirement row.
        assert len(rows) == 1
        assert rows[0]["projects"] == 2

    def test_unreachable_project_does_not_stop_others(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path, "repo-a")
        missing = str(tmp_path / "missing-repo")
        config = make_config(tmp_path, [missing, str(repo)])
        outcome, _ = run_scanner(config)
        assert outcome.exit_code == 11
        assert [item.status for item in outcome.projects] == ["failed", "success"]

    def test_all_projects_unreachable_returns_5(self, tmp_path: Path) -> None:
        config = make_config(tmp_path, [str(tmp_path / "missing-a"), str(tmp_path / "missing-b")])
        outcome, _ = run_scanner(config)
        assert outcome.exit_code == 5
        assert all(item.status == "failed" for item in outcome.projects)


class TestDryRunAndStrict:
    def test_dry_run_does_not_create_database(self, config: Path) -> None:
        settings = Settings.from_file(config)
        assert not settings.scan_db_path.exists()
        outcome, _ = run_scanner(config, ScanOptions(dry_run=True))
        assert outcome.exit_code == 0
        assert outcome.dry_run is True
        assert not settings.scan_db_path.exists()
        assert outcome.projects[0].files_scanned > 0

    def test_strict_returns_10_for_incomplete_markers(self, config: Path) -> None:
        lenient, _ = run_scanner(config)
        assert lenient.exit_code == 0
        strict, _ = run_scanner(config, ScanOptions(strict=True))
        assert strict.exit_code == 10
        assert strict.projects[0].marker_errors > 0

    def test_include_overrides_config(self, config: Path) -> None:
        outcome, _ = run_scanner(config, ScanOptions(include_extra=["**/*.py"]))
        assert outcome.exit_code == 0
        db = ScanDatabase(Settings.from_file(config).scan_db_path)
        with db.connect(readonly=True) as conn:
            paths = {row[0] for row in conn.execute("SELECT path FROM file_snapshot")}
        assert "src/code.py" in paths


class TestCli:
    runner = CliRunner()

    def test_scan_json(self, config: Path) -> None:
        result = self.runner.invoke(app, ["scan", "--config", str(config), "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["exit_code"] == 0
        assert payload["totals"]["projects_total"] == 1
        assert payload["projects"][0]["status"] == "success"

    def test_scan_alias(self, config: Path) -> None:
        with pytest.raises(SystemExit) as exc_info:
            main(["--scan", "--config", str(config), "--json"])
        assert exc_info.value.code == 0

    def test_cli_strict(self, config: Path) -> None:
        result = self.runner.invoke(
            app, ["scan", "--config", str(config), "--strict", "--json"]
        )
        assert result.exit_code == 10
        payload = json.loads(result.stdout)
        assert payload["exit_code"] == 10
        assert payload["status"] == "error"

    def test_cli_all_projects_unreachable(self, tmp_path: Path) -> None:
        config = make_config(tmp_path, [str(tmp_path / "missing")])
        result = self.runner.invoke(app, ["scan", "--config", str(config), "--json"])
        assert result.exit_code == 5
        payload = json.loads(result.stdout)
        assert payload["projects"][0]["status"] == "failed"

    def test_cli_incompatible_schema(self, config: Path) -> None:
        settings = Settings.from_file(config)
        db = ScanDatabase(settings.scan_db_path)
        db.initialize()
        with db.transaction() as conn:
            conn.execute("UPDATE schema_version SET version = 1")

        result = self.runner.invoke(app, ["scan", "--config", str(config), "--json"])
        assert result.exit_code == 12
        payload = json.loads(result.stdout)
        assert payload["exit_code"] == 12
        assert "schema version" in payload["error"]
        assert "delete the database" in payload["error"]

    def test_cli_database_failure(self, config: Path) -> None:
        settings = Settings.from_file(config)
        settings.scan_db_path.parent.mkdir(parents=True, exist_ok=True)
        settings.scan_db_path.mkdir()
        result = self.runner.invoke(app, ["scan", "--config", str(config), "--json"])
        assert result.exit_code == 9
        payload = json.loads(result.stdout)
        assert payload["exit_code"] == 9

    def test_cli_empty_project_list_is_config_error(self, tmp_path: Path) -> None:
        config = make_config(tmp_path, [])
        result = self.runner.invoke(app, ["scan", "--config", str(config), "--json"])
        assert result.exit_code == 1


class TestProjectSelection:
    def test_explicit_project_replaces_config(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path, "repo-a")
        other = make_repo(tmp_path, "repo-b")
        config = make_config(tmp_path, [str(other)])
        outcome, _ = run_scanner(
            config, ScanOptions(projects=[str(repo)])
        )
        assert outcome.exit_code == 0
        db = ScanDatabase(Settings.from_file(config).scan_db_path)
        with db.connect(readonly=True) as conn:
            urls = {row[0] for row in conn.execute("SELECT url FROM git_project")}
        assert any("repo-a" in url for url in urls)
        assert not any("repo-b" in url for url in urls)

    def test_explicit_missing_branch_is_warning(self, config: Path) -> None:
        outcome, output = run_scanner(config, ScanOptions(branches=["nope"]))
        assert outcome.exit_code == 0
        assert outcome.projects[0].status == "success"
        assert "branch not found: nope" in outcome.projects[0].warnings
        assert "no branches selected" in output


class TestBranchSelection:
    def test_config_default_branch(self, tmp_path: Path, repo: Path) -> None:
        config = make_config(tmp_path, [str(repo)], branches="default")
        outcome, _ = run_scanner(config)
        assert outcome.exit_code == 0
        assert outcome.projects[0].branches == ["main"]
        db = ScanDatabase(Settings.from_file(config).scan_db_path)
        with db.connect(readonly=True) as conn:
            branches = {
                row[0] for row in conn.execute("SELECT DISTINCT branch FROM file_snapshot")
            }
        assert branches == {"main"}

    def test_config_branch_list(self, tmp_path: Path, repo: Path) -> None:
        config = make_config(tmp_path, [str(repo)], branches=["feature"])
        outcome, _ = run_scanner(config)
        assert outcome.exit_code == 0
        assert outcome.projects[0].branches == ["feature"]
        db = ScanDatabase(Settings.from_file(config).scan_db_path)
        with db.connect(readonly=True) as conn:
            paths = {row[0] for row in conn.execute("SELECT path FROM file_snapshot")}
        assert "docs/feature.md" in paths

    def test_branch_include(self, tmp_path: Path, repo: Path) -> None:
        config = make_config(tmp_path, [str(repo)], branch_include=["feat*"])
        outcome, _ = run_scanner(config)
        assert outcome.exit_code == 0
        assert outcome.projects[0].branches == ["feature"]

    def test_branch_exclude(self, tmp_path: Path, repo: Path) -> None:
        config = make_config(tmp_path, [str(repo)], branch_exclude=["feat*"])
        outcome, _ = run_scanner(config)
        assert outcome.exit_code == 0
        assert outcome.projects[0].branches == ["main"]

    def test_cli_flag_overrides_config_branch_list(self, tmp_path: Path, repo: Path) -> None:
        config = make_config(tmp_path, [str(repo)], branches=["feature"])
        outcome, _ = run_scanner(config, ScanOptions(all_branches=True))
        assert outcome.exit_code == 0
        assert outcome.projects[0].branches == ["feature", "main"]


class TestRunGuard:
    def test_active_run_blocks_scan_until_forced(self, config: Path, repo: Path) -> None:
        settings = Settings.from_file(config)
        db = ScanDatabase(settings.scan_db_path)
        db.initialize()
        with db.transaction() as conn:
            project_id = db.ensure_project(conn, str(repo))
            db.create_run(conn, project_id)

        blocked, _ = run_scanner(config)
        assert blocked.exit_code == 11
        assert blocked.projects[0].status == "failed"
        assert "another scan is running" in (blocked.projects[0].message or "")

        forced, _ = run_scanner(config, ScanOptions(force=True))
        assert forced.exit_code == 0
        assert forced.projects[0].status == "success"
        with db.connect(readonly=True) as conn:
            statuses = [
                row[0]
                for row in conn.execute("SELECT status FROM scan_run ORDER BY id")
            ]
        assert statuses == ["failed", "success"]


class TestLogging:
    def test_scan_writes_log_file(self, tmp_path: Path, repo: Path) -> None:
        config = make_config(tmp_path, [str(repo)], log_path="logs/custom-scan.log")
        result = TestCli.runner.invoke(
            app, ["scan", "--config", str(config), "--json"]
        )
        assert result.exit_code == 0, result.output

        log_path = Settings.from_file(config).scan_log_path
        assert log_path.exists()
        text = log_path.read_text(encoding="utf-8")
        assert "Scanning 1 project(s)" in text
        assert repo.name in text


class TestProgress:
    def test_progress_bar_shows_project_branch_and_file(self, config: Path) -> None:
        settings = Settings.from_file(config)
        stream = io.StringIO()
        console = Console(file=stream, force_terminal=True, width=160)
        scanner = Scanner(
            settings,
            ScanOptions(branches=["main"]),
            console,
            show_progress=True,
        )
        outcome = scanner.run()
        assert outcome.exit_code == 0
        text = stream.getvalue()
        assert "1/1" in text
        assert "[main]" in text
        assert "docs/req.md" in text

    def test_console_without_tty_disables_progress(self, config: Path) -> None:
        settings = Settings.from_file(config)
        stream = io.StringIO()
        console = Console(file=stream, width=160, no_color=True)
        scanner = Scanner(
            settings,
            ScanOptions(branches=["main"]),
            console,
            show_progress=True,
        )
        assert scanner.show_progress is False
        scanner.run()
        assert "\x1b[" not in stream.getvalue()


class TestCommitHistory:
    def test_history_failure_marks_project_partial(
        self, config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(*_args: object, **_kwargs: object) -> None:
            raise GitError("history unavailable")

        monkeypatch.setattr("scan_git_push.scan.scanner.last_commit_map", boom)
        outcome, _ = run_scanner(config, ScanOptions(branches=["main"]))
        assert outcome.exit_code == 11
        project = outcome.projects[0]
        assert project.status == "partial"
        assert any(error.get("type") == "commit-history" for error in project.errors)


class TestAnalysisIntegration:
    def test_scan_does_not_create_analysis_db(self, config: Path) -> None:
        settings = Settings.from_file(config)
        run_scanner(config)
        assert settings.scan_db_path.exists()
        assert not settings.analysis_db_path.exists()

    def test_attach_join_on_shared_url_key(self, config: Path, repo: Path) -> None:
        run_scanner(config)
        settings = Settings.from_file(config)

        analysis = DatabaseManager(settings.analysis_db_path)
        analysis.initialize()
        analysis.ensure_project(str(repo))

        with sqlite3.connect(str(settings.analysis_db_path)) as conn:
            conn.execute("ATTACH DATABASE ? AS scan", (str(settings.scan_db_path),))
            rows = conn.execute(
                "SELECT req.code, project.url "
                "FROM scan.req AS req "
                "JOIN scan.point AS point ON point.req_id = req.id "
                "JOIN scan.point_content AS pc ON pc.point_id = point.id "
                "JOIN scan.file_snapshot AS fs ON fs.id = pc.file_id "
                "JOIN scan.git_project AS scan_project "
                "  ON scan_project.id = fs.git_project_id "
                "JOIN main.git_project AS project "
                "  ON project.url_key = scan_project.url_key"
            ).fetchall()
        assert rows
        assert any(code == "REQ_1" for code, _ in rows)


class TestSecurity:
    def test_credentials_are_redacted_from_errors(self) -> None:
        message = (
            "fatal: Authentication failed for "
            "'https://user:secret@example.com/a.git'"
        )
        redacted = redact_credentials(message)
        assert "secret" not in redacted
        assert "user" not in redacted
        assert "https://***@example.com/a.git" in redacted
