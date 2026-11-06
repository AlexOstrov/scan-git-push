"""HTML report for requirements and their sections (``scan.db``).

The report is built from the current tip state of every scanned project:
requirements are global, so the same requirement id may appear in several
projects and is reported once with all its sections and occurrences.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from jinja2 import Environment
from loguru import logger

from scan_git_push import __version__

REQUIREMENTS_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Отчет по требованиям и разделам</title>
<style>
:root {
  --bg: #0d1117;
  --panel: #161b22;
  --border: #30363d;
  --text: #c9d1d9;
  --muted: #8b949e;
  --accent: #58a6ff;
  --green: #3fb950;
  --yellow: #d29922;
  --purple: #bc8cff;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.6;
  padding: 24px;
}
.container { max-width: 1100px; margin: 0 auto; }
h1 { color: var(--accent); font-size: 1.9em; margin-bottom: 4px; }
.generated { color: var(--muted); font-size: 0.85em; margin-bottom: 24px; }
.req {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 18px 20px;
  margin-bottom: 20px;
}
.req > h2 { color: var(--green); font-size: 1.15em; margin-bottom: 12px; }
.section { margin: 0 0 18px 0; padding-left: 14px; border-left: 3px solid var(--purple); }
.section:last-child { margin-bottom: 0; }
.section > h3 { color: var(--purple); font-size: 1em; margin-bottom: 8px; }
.occurrence { margin: 0 0 10px 0; padding-left: 10px; }
.occurrence:last-child { margin-bottom: 0; }
.occurrence div { font-size: 0.9em; }
.occurrence .label { color: var(--muted); }
.occurrence a { color: var(--accent); text-decoration: none; word-break: break-all; }
.occurrence code {
  background: #21262d;
  border-radius: 4px;
  padding: 1px 5px;
  font-size: 0.9em;
  word-break: break-all;
}
.raw {
  margin-top: 6px;
  background: #010409;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 8px 10px;
  color: var(--yellow);
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 0.82em;
  white-space: pre-wrap;
  word-break: break-all;
}
.divider { color: var(--border); margin: 10px 0; user-select: none; }
.totals {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 18px 20px;
  margin-top: 28px;
}
.totals h2 { color: var(--accent); font-size: 1.1em; margin-bottom: 8px; }
.totals p { font-size: 0.95em; }
.include-stats {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 18px 20px;
  margin-top: 20px;
}
.include-stats > h2 { color: var(--accent); font-size: 1.1em; margin-bottom: 12px; }
.include-project { margin-bottom: 18px; }
.include-project:last-child { margin-bottom: 0; }
.include-project > h3 {
  color: var(--green);
  font-size: 1em;
  margin-bottom: 8px;
  word-break: break-all;
}
.include-project table { width: 100%; border-collapse: collapse; font-size: 0.9em; }
.include-project th,
.include-project td {
  border: 1px solid var(--border);
  padding: 6px 8px;
  text-align: left;
}
.include-project th { background: #21262d; color: var(--muted); font-weight: 600; }
.include-project td:not(:first-child),
.include-project th:not(:first-child) { text-align: right; }
.empty { color: var(--muted); }
.footer { color: var(--muted); font-size: 0.8em; text-align: center; margin-top: 28px; }
</style>
</head>
<body>
<div class="container">
<h1>Отчет по требованиям и разделам</h1>
<p class="generated">Сформирован: {{ generated_at }}</p>

{% if not requirements %}
<p class="empty">Требования не найдены.</p>
{% endif %}

{% for req in requirements %}
<section class="req">
  <h2>ID требования: {{ req.code }} {{ req.name }}</h2>
  {% for section in req.sections %}
  <div class="section">
    <h3>Раздел: {{ section.point }}</h3>
    {% for occ in section.occurrences %}
    {% if not loop.first %}<div class="divider">---</div>{% endif %}
    <div class="occurrence">
      <div><span class="label">Проект:</span> <a href="{{ occ.project_url }}">{{ occ.project_url }}</a>, ветка: <code>{{ occ.branch }}</code>, коммит: <code>{{ occ.commit_sha or "—" }}</code></div>
      <div><span class="label">Файл:</span> <code>{{ occ.path }}</code>, номер строки: {{ occ.line_no }}</div>
      <div><span class="label">Коммитер:</span> {{ occ.committer or "—" }}</div>
      <pre class="raw">{{ occ.raw_marker }}</pre>
    </div>
    {% endfor %}
  </div>
  {% endfor %}
</section>
{% endfor %}

<div class="totals">
  <h2>Итого:</h2>
  <p>Всего количество требований - {{ total_requirements }}</p>
  <p>Всего количество разделов - {{ total_sections }}</p>
</div>

{% if include_stats %}
<div class="include-stats">
  <h2>Сводная статистика по количеству найденных файлов и маркеров в них</h2>
  {% for project in include_stats %}
  <div class="include-project">
    <h3>Проект: <a href="{{ project.project_url }}">{{ project.project_url }}</a>, автор: {{ project.author or "—" }}</h3>
    <table>
      <thead>
        <tr>
          <th>Шаблон файла</th>
          <th>Количество найденных файлов</th>
          <th>Количество найденных маркеров</th>
        </tr>
      </thead>
      <tbody>
        {% for item in project.patterns %}
        <tr>
          <td><code>{{ item.pattern }}</code></td>
          <td>{{ item.files_count }}</td>
          <td>{{ item.markers_count }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% endfor %}
</div>
{% endif %}

<p class="footer">scan-git-push v{{ version }}</p>
</div>
</body>
</html>
"""

_NUMBER_RE = re.compile(r"(\d+)")


def _natural_key(value: str) -> tuple[Any, ...]:
    """Sort ``5.2`` before ``5.10`` instead of lexicographically."""
    parts = _NUMBER_RE.split(value or "")
    return tuple(int(part) if part.isdigit() else part.lower() for part in parts)


def group_requirements(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group occurrence rows into requirements and their sections."""
    grouped: dict[str, dict[str, Any]] = {}

    for row in rows:
        code = str(row["code"])
        requirement = grouped.setdefault(
            code,
            {
                "code": code,
                "name": row.get("name") or "Требование без наименования",
                "sections": {},
            },
        )
        point = str(row["point"])
        requirement["sections"].setdefault(point, []).append(dict(row))

    requirements: list[dict[str, Any]] = []
    for requirement in grouped.values():
        sections = [
            {"point": point, "occurrences": occurrences}
            for point, occurrences in sorted(
                requirement["sections"].items(), key=lambda item: _natural_key(item[0])
            )
        ]
        requirements.append(
            {
                "code": requirement["code"],
                "name": requirement["name"],
                "sections": sections,
            }
        )

    requirements.sort(key=lambda item: _natural_key(item["code"]))
    return requirements


def group_include_stats(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group per-pattern counters into projects for the final report table."""
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        url = str(row["project_url"])
        project = grouped.setdefault(
            url,
            {
                "project_url": url,
                "author": row.get("author") or None,
                "patterns": [],
            },
        )
        if project["author"] is None:
            project["author"] = row.get("author") or None
        project["patterns"].append(
            {
                "position": int(row.get("position") or 0),
                "pattern": str(row["pattern"]),
                "files_count": int(row.get("files_count") or 0),
                "markers_count": int(row.get("markers_count") or 0),
            }
        )

    projects = list(grouped.values())
    for project in projects:
        project["patterns"].sort(
            key=lambda item: (item["position"], item["pattern"])
        )
        for item in project["patterns"]:
            item.pop("position", None)
    return projects


class RequirementReporter:
    """Generates a self-contained HTML report for ``report-from-db --req``."""

    def __init__(self) -> None:
        self.env = Environment(autoescape=True)

    def generate(
        self,
        rows: Iterable[dict[str, Any]],
        output_dir: Path,
        date: datetime | None = None,
        *,
        include_rows: Iterable[dict[str, Any]] | None = None,
    ) -> tuple[Path, dict[str, Any]]:
        """Write the report and return ``(path, stats)``."""
        generated = date or datetime.now()
        requirements = group_requirements(rows)
        include_stats = group_include_stats(include_rows or [])
        total_requirements = len(requirements)
        total_sections = sum(len(item["sections"]) for item in requirements)

        template = self.env.from_string(REQUIREMENTS_TEMPLATE)
        content = template.render(
            requirements=requirements,
            include_stats=include_stats,
            generated_at=generated.strftime("%Y-%m-%d %H:%M:%S"),
            total_requirements=total_requirements,
            total_sections=total_sections,
            version=__version__,
        )

        filename = Path(output_dir) / f"requirements_{generated.strftime('%Y%m%d')}.html"
        filename.parent.mkdir(parents=True, exist_ok=True)
        filename.write_text(content, encoding="utf-8")
        logger.info("Requirements report saved: {}", filename)

        return filename, {
            "report": str(filename),
            "requirements": total_requirements,
            "sections": total_sections,
            "include_stats": include_stats,
            "generated_at": generated.isoformat(timespec="seconds"),
        }
