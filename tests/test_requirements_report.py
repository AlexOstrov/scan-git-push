"""Tests for the requirements/sections HTML report (``report-from-db --req``)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scan_git_push.cli import app
from scan_git_push.config import Settings
from scan_git_push.git.url import normalize_url, url_key
from scan_git_push.reporters.requirements import (
    RequirementReporter,
    group_requirements,
)
from scan_git_push.scan.database import ScanDatabase
from tests.test_scan import make_config, make_repo, run_scanner


@pytest.fixture
def req_repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path)


@pytest.fixture
def req_config(tmp_path: Path, req_repo: Path) -> Path:
    return make_config(
        tmp_path,
        [str(req_repo)],
        exclude=["vendor/**"],
        exclude_secrets=["secret.md"],
    )


def _row(
    *,
    code: str = "TR_3",
    name: str = "Требование 3",
    point: str = "5.5.1",
    project_url: str = "https://github.com/owner/repo",
    branch: str = "main",
    commit_sha: str = "abc123",
    path: str = "docs/req.md",
    line_no: int = 10,
    committer: str | None = "Committer",
    raw_marker: str = '<!-- REQ-META id="TR_3" point="5.5.1" -->',
) -> dict[str, object]:
    return {
        "code": code,
        "name": name,
        "point": point,
        "project_url": project_url,
        "branch": branch,
        "commit_sha": commit_sha,
        "path": path,
        "line_no": line_no,
        "committer": committer,
        "raw_marker": raw_marker,
    }


class TestGrouping:
    def test_groups_by_requirement_and_section(self) -> None:
        rows = [
            _row(point="5.10"),
            _row(point="5.2", line_no=2),
            _row(point="5.2", line_no=3),
            _row(code="TR_1", name="Первый", point="1.1"),
        ]
        grouped = group_requirements(rows)
        assert [item["code"] for item in grouped] == ["TR_1", "TR_3"]
        sections = grouped[1]["sections"]
        # Natural order: 5.2 before 5.10.
        assert [item["point"] for item in sections] == ["5.2", "5.10"]
        assert len(sections[0]["occurrences"]) == 2

    def test_empty_name_gets_default(self) -> None:
        grouped = group_requirements([_row(name="")])
        assert grouped[0]["name"] == "Требование без наименования"


class TestReporter:
    def test_html_contains_template_sections(self, tmp_path: Path) -> None:
        rows = [
            _row(
                point="5.5.1",
                raw_marker='<!-- REQ-META id="TR_3" point="5.5.1." -->',
            ),
            _row(
                point="5.5.1",
                project_url="https://github.com/owner/second",
                branch="dev",
                commit_sha="def456",
                path="docs/other.md",
                line_no=42,
                committer="Other Committer",
                raw_marker='<!-- REQ-META id="TR_3" point="5.5.1" -->',
            ),
            _row(code="TR_1", name="Первый", point="1.1"),
        ]
        path, stats = RequirementReporter().generate(rows, tmp_path)

        assert path.parent == tmp_path
        assert stats["requirements"] == 2
        assert stats["sections"] == 2
        html = path.read_text(encoding="utf-8")
        assert "Отчет по требованиям и разделам" in html
        assert "ID требования: TR_3 Требование 3" in html
        assert "Раздел: 5.5.1" in html
        assert "Проект:" in html
        assert "ветка: <code>dev</code>" in html
        assert "коммит: <code>def456</code>" in html
        assert "docs/other.md" in html
        assert "номер строки: 42" in html
        assert "Коммитер:" in html
        assert "Other Committer" in html
        assert "Всего количество требований - 2" in html
        assert "Всего количество разделов - 2" in html
        # Raw markers are shown as text, not interpreted as HTML.
        assert "&lt;!-- REQ-META" in html

    def test_empty_rows_still_render_totals(self, tmp_path: Path) -> None:
        path, stats = RequirementReporter().generate([], tmp_path)
        html = path.read_text(encoding="utf-8")
        assert stats["requirements"] == 0
        assert stats["sections"] == 0
        assert "Требования не найдены." in html
        assert "Всего количество требований - 0" in html
        assert "Всего количество разделов - 0" in html


class TestCli:
    def test_report_from_db_req_json(self, req_config: Path, req_repo: Path) -> None:
        runner = CliRunner()
        scan_result = runner.invoke(app, ["scan", "--config", str(req_config), "--json"])
        assert scan_result.exit_code == 0

        result = runner.invoke(
            app,
            ["report-from-db", "--config", str(req_config), "--req", "--json"],
        )
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["exit_code"] == 0
        assert payload["requirements"] > 0
        assert payload["sections"] > 0
        report = Path(payload["report"])
        assert report.exists()
        html = report.read_text(encoding="utf-8")
        assert "Отчет по требованиям и разделам" in html
        assert "ID требования: REQ_1" in html
        assert '&lt;!-- REQ-META id=&#34;REQ_1&#34; point=&#34;1.1&#34;' in html

    def test_report_from_db_req_does_not_need_analysis_db(
        self, req_config: Path, req_repo: Path
    ) -> None:
        runner = CliRunner()
        assert runner.invoke(app, ["scan", "--config", str(req_config)]).exit_code == 0
        settings = Settings.from_file(req_config)
        assert not settings.analysis_db_path.exists()

        result = runner.invoke(
            app, ["report-from-db", "--config", str(req_config), "--req"]
        )
        assert result.exit_code == 0
        assert "Requirements:" in result.output
        assert not settings.analysis_db_path.exists()

    def test_report_from_db_req_filters_by_project(self, tmp_path: Path) -> None:
        first = make_repo(tmp_path, "repo-a")
        second = make_repo(tmp_path, "repo-b")
        config = make_config(tmp_path, [str(first), str(second)])
        outcome, _ = run_scanner(config)
        assert outcome.exit_code == 0

        settings = Settings.from_file(config)
        db = ScanDatabase(settings.scan_db_path)
        all_rows = db.requirement_report()
        first_rows = db.requirement_report([url_key(str(first), settings.base_dir)])
        assert {row["project_url"] for row in all_rows} == {
            normalize_url(str(first), settings.base_dir),
            normalize_url(str(second), settings.base_dir),
        }
        assert {row["project_url"] for row in first_rows} == {
            normalize_url(str(first), settings.base_dir)
        }

        result = CliRunner().invoke(
            app,
            [
                "report-from-db",
                "--config",
                str(config),
                "--req",
                "--project",
                str(first),
                "--json",
            ],
        )
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert len(payload["projects"]) == 1
        html = Path(payload["report"]).read_text(encoding="utf-8")
        assert normalize_url(str(first), settings.base_dir) in html
        assert normalize_url(str(second), settings.base_dir) not in html

    def test_report_from_db_req_without_scan_db(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path)
        config = make_config(tmp_path, [str(repo)])
        result = CliRunner().invoke(
            app, ["report-from-db", "--config", str(config), "--req", "--json"]
        )
        assert result.exit_code == 9
