"""Tests for the CLI: commands, exit codes and JSON output."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from scan_git_push.cli import (
    EXIT_ARGS,
    EXIT_CONFIG,
    EXIT_OK,
    EXIT_SCHEMA,
    _resolve_projects,
    app,
)
from scan_git_push.config import Settings
from scan_git_push.database import DatabaseManager
from scan_git_push.git.models import CommitData, FileChange
from scan_git_push.llm.registry import ModelOption

runner = CliRunner()


def _fake_select(model_option: ModelOption | None = None, provider=None):
    option = model_option or ModelOption("fake", "fake-model")

    def _select(registry, console, model_spec, non_interactive):
        return option, provider

    return _select


def _patch_llm(fake_provider):
    return patch("scan_git_push.cli._select_model", _fake_select(provider=fake_provider))


class TestResolveProjects:
    def test_projects_are_deduplicated(self, settings: Settings, git_repo: Path) -> None:
        refs = _resolve_projects(
            settings, [str(git_repo), str(git_repo) + "/", str(git_repo)]
        )
        assert len(refs) == 1

    def test_no_projects_is_an_error(self, tmp_path: Path) -> None:
        import typer

        path = tmp_path / "config.json"
        path.write_text(json.dumps({"SCAN": {"GIT_PROJECTS": []}}), encoding="utf-8")
        settings = Settings.from_file(path)
        with pytest.raises(typer.Exit) as exc:
            _resolve_projects(settings, None)
        assert exc.value.exit_code == EXIT_CONFIG


class TestAnalyze:
    def test_full_run_writes_reports_and_db(
        self, config_file: Path, git_repo: Path, fake_llm
    ) -> None:
        with _patch_llm(fake_llm):
            result = runner.invoke(
                app,
                [
                    "main",
                    "--config",
                    str(config_file),
                    "--days",
                    "30",
                    "--json",
                    "--non-interactive",
                ],
            )

        assert result.exit_code == EXIT_OK, result.output
        payload = json.loads(result.output[result.output.index("{") :])
        assert payload["days"] == 30
        assert payload["model"] == {"provider": "fake", "model": "fake-model"}
        assert payload["analyzed_now"] == 3
        assert len(payload["projects"]) == 1

        project = payload["projects"][0]
        assert project["commits"]
        assert project["reports"]["markdown"].endswith(".md")
        assert Path(project["reports"]["markdown"]).exists()
        assert Path(project["reports"]["html"]).exists()
        assert payload["summary"]["markdown"].endswith("_summary_" + _today() + ".md")

        db = DatabaseManager(config_file.parent / "db" / "analysis.db")
        assert db.count_commits() == 3

    def test_second_run_is_deduplicated(
        self, config_file: Path, git_repo: Path, fake_llm
    ) -> None:
        args = [
            "analyze",
            "--config",
            str(config_file),
            "--days",
            "30",
            "--json",
            "--non-interactive",
        ]
        with _patch_llm(fake_llm):
            first = runner.invoke(app, args)
            second = runner.invoke(app, args)

        assert first.exit_code == EXIT_OK, first.output
        assert second.exit_code == EXIT_OK, second.output
        payload = json.loads(second.output[second.output.index("{") :])
        assert payload["analyzed_now"] == 0
        # The report is rebuilt from the database even without new commits.
        assert payload["projects"][0]["commits"]

    def test_reanalyze_flag(
        self, config_file: Path, git_repo: Path, fake_llm
    ) -> None:
        args = [
            "analyze",
            "--config",
            str(config_file),
            "--days",
            "30",
            "--json",
            "--non-interactive",
        ]
        with _patch_llm(fake_llm):
            runner.invoke(app, args)
            result = runner.invoke(app, [*args, "--reanalyze"])

        assert result.exit_code == EXIT_OK, result.output
        payload = json.loads(result.output[result.output.index("{") :])
        assert payload["analyzed_now"] == 3

    def test_project_option_overrides_config(
        self, config_file: Path, git_repo: Path, tmp_path: Path, fake_llm
    ) -> None:
        other = tmp_path / "other"
        other.mkdir()
        import subprocess

        subprocess.run(["git", "init", "-b", "main"], cwd=other, check=True)
        subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=other, check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=other, check=True)
        (other / "x.txt").write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=other, check=True)
        subprocess.run(["git", "commit", "-m", "only"], cwd=other, check=True)

        with _patch_llm(fake_llm):
            result = runner.invoke(
                app,
                [
                    "analyze",
                    "--config",
                    str(config_file),
                    "--project",
                    str(other),
                    "--days",
                    "30",
                    "--json",
                ],
            )
        assert result.exit_code == EXIT_OK, result.output
        payload = json.loads(result.output[result.output.index("{") :])
        assert len(payload["projects"]) == 1
        assert payload["analyzed_now"] == 1

    def test_missing_config(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["main", "--config", str(tmp_path / "nope.json")])
        assert result.exit_code == EXIT_CONFIG

    def test_invalid_days(self, config_file: Path) -> None:
        result = runner.invoke(app, ["main", "--config", str(config_file), "--days", "0"])
        assert result.exit_code == EXIT_ARGS

    def test_incompatible_schema(self, config_file: Path) -> None:
        db_path = config_file.parent / "db" / "analysis.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        conn.executescript(
            "CREATE TABLE schema_version (version INTEGER NOT NULL);"
            "INSERT INTO schema_version VALUES (2);"
        )
        conn.commit()
        conn.close()

        result = runner.invoke(app, ["main", "--config", str(config_file)])
        assert result.exit_code == EXIT_SCHEMA


class TestReportFromDb:
    def _seed(self, config_file: Path) -> None:
        settings = Settings.from_file(config_file)
        db = DatabaseManager(settings.analysis_db_path)
        db.initialize()
        project_id = db.ensure_project(settings.scan.git_projects[0])
        commit = CommitData(
            sha="abc123def456",
            author="testuser",
            date=datetime.now(timezone.utc),
            message="Stored commit",
            files=[FileChange(path="test.py", additions=5, deletions=2)],
        )
        db.save_commit_analysis(
            project_id=project_id,
            commit=commit,
            summary="Stored summary",
            chunks_count=1,
        )

    def test_empty_database(self, config_file: Path) -> None:
        settings = Settings.from_file(config_file)
        DatabaseManager(settings.analysis_db_path).initialize()

        result = runner.invoke(
            app, ["report-from-db", "--config", str(config_file), "--days", "30"]
        )
        assert result.exit_code == EXIT_OK
        assert "No projects found in database" in result.output

    def test_with_data(self, config_file: Path) -> None:
        self._seed(config_file)
        result = runner.invoke(
            app,
            [
                "report-from-db",
                "--config",
                str(config_file),
                "--days",
                "30",
                "--json",
            ],
        )
        assert result.exit_code == EXIT_OK, result.output
        payload = json.loads(result.output[result.output.index("{") :])
        assert payload["model"] == {}
        commits = payload["projects"][0]["commits"]
        assert commits[0]["sha"] == "abc123def456"
        assert commits[0]["summary"] == "Stored summary"
        assert Path(payload["projects"][0]["reports"]["markdown"]).exists()

    def test_unknown_project_is_skipped(self, config_file: Path) -> None:
        settings = Settings.from_file(config_file)
        DatabaseManager(settings.analysis_db_path).initialize()

        result = runner.invoke(
            app,
            [
                "report-from-db",
                "--config",
                str(config_file),
                "--project",
                "https://github.com/unknown/repo",
            ],
        )
        assert result.exit_code == EXIT_OK
        assert "not found in DB" in result.output


def _today() -> str:
    return datetime.now().strftime("%Y%m%d")
