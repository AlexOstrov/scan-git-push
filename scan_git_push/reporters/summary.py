"""Cross-project summary report (Markdown + self-contained HTML)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from jinja2 import Environment
from loguru import logger

from scan_git_push.analyzer import AnalysisResult


@dataclass
class ProjectSection:
    """One project in the summary report."""

    name: str
    url: str
    results: list[AnalysisResult] = field(default_factory=list)

    @property
    def commits(self) -> int:
        return len(self.results)

    @property
    def files(self) -> int:
        return sum(len(r.file_changes or r.files) for r in self.results)

    @property
    def additions(self) -> int:
        return sum(r.additions for r in self.results)

    @property
    def deletions(self) -> int:
        return sum(r.deletions for r in self.results)

    @property
    def requirements(self) -> list[str]:
        return sorted({label for r in self.results for label in r.requirement_labels})


SUMMARY_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Git Push Summary: {{ date_str }}</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0d1117; color: #c9d1d9; padding: 24px; }
h1 { color: #58a6ff; margin-bottom: 6px; }
.meta { color: #8b949e; font-size: 13px; margin-bottom: 20px; }
.controls { margin-bottom: 20px; }
select, button { background: #161b22; color: #c9d1d9; border: 1px solid #30363d; border-radius: 6px; padding: 8px 10px; font-size: 13px; }
table { width: 100%; border-collapse: collapse; margin-bottom: 32px; }
th { text-align: left; color: #8b949e; font-size: 12px; border-bottom: 1px solid #30363d; padding: 8px; }
td { padding: 8px; border-bottom: 1px solid #21262d; font-size: 13px; }
td.num { text-align: right; font-family: monospace; }
td.path { font-family: monospace; color: #8b949e; font-size: 11px; word-break: break-all; }
.badge { display: inline-block; background: #8957e533; color: #d2a8ff; padding: 2px 8px; border-radius: 12px; font-size: 11px; margin: 0 6px 6px 0; }
.empty { color: #8b949e; font-style: italic; }
h2 { color: #c9d1d9; font-size: 16px; margin-bottom: 10px; }
</style>
</head>
<body>
<h1>Git Push Analysis — Summary</h1>
<div class="meta">{{ projects|length }} project(s) · last {{ days }} days · generated {{ generated }}</div>
<div class="controls">
  <select id="project-filter" onchange="filterProjects()">
    <option value="">All projects</option>
    {% for p in projects %}<option value="{{ p.name }}">{{ p.name }}</option>{% endfor %}
  </select>
</div>
{% for p in projects %}
<section class="project" data-project="{{ p.name }}">
  <h2>{{ p.name }} <span class="empty">({{ p.commits }} commits, +{{ p.additions }} -{{ p.deletions }})</span></h2>
  <div class="meta">{{ p.url }}</div>
  {% if p.requirements %}<div>{% for item in p.requirements %}<span class="badge">{{ item }}</span>{% endfor %}</div>{% endif %}
  {% if p.results %}
  <table>
    <thead><tr><th>Commit</th><th>Author</th><th>Date</th><th class="num">Files</th><th class="num">+/-</th><th>Summary</th></tr></thead>
    <tbody>
    {% for r in p.results %}
      <tr>
        <td class="path">{{ r.sha[:8] }}</td>
        <td>{{ r.author }}</td>
        <td>{{ r.date[:10] }}</td>
        <td class="num">{{ r.file_changes|length }}</td>
        <td class="num">+{{ r.additions }} -{{ r.deletions }}</td>
        <td>{{ r.summary }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  {% else %}
  <p class="empty">No analyzed commits.</p>
  {% endif %}
</section>
{% endfor %}
<script>
function filterProjects() {
  var value = document.getElementById('project-filter').value;
  document.querySelectorAll('section.project').forEach(function(section) {
    section.style.display = (!value || section.dataset.project === value) ? 'block' : 'none';
  });
}
</script>
</body>
</html>
"""


class SummaryReporter:
    """Builds the cross-project index report."""

    def __init__(self) -> None:
        self.env = Environment(autoescape=True)

    def generate(
        self,
        projects: list[ProjectSection],
        days: int,
        output_dir: Path,
    ) -> tuple[Path, Path]:
        date_str = datetime.now().strftime("%Y%m%d")
        timestamp = datetime.now().isoformat(timespec="seconds")
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        md_path = output_dir / f"_summary_{date_str}.md"
        html_path = output_dir / f"_summary_{date_str}.html"

        md_path.write_text(
            self._render_markdown(projects, days, timestamp), encoding="utf-8"
        )
        template = self.env.from_string(SUMMARY_HTML)
        html_path.write_text(
            template.render(
                projects=projects, days=days, generated=timestamp, date_str=date_str
            ),
            encoding="utf-8",
        )
        logger.info("Summary reports saved: {} / {}", md_path, html_path)
        return md_path, html_path

    def _render_markdown(
        self, projects: list[ProjectSection], days: int, timestamp: str
    ) -> str:
        lines = [
            "# Git Push Analysis — Summary",
            "",
            f"**Period:** last {days} days  ",
            f"**Generated:** {timestamp}  ",
            f"**Projects:** {len(projects)}",
            "",
            "| Project | Commits | Files | Lines | Requirements |",
            "|---------|---------|-------|-------|--------------|",
        ]
        for project in projects:
            lines.append(
                f"| [{project.name}]({project.url}) | {project.commits} | {project.files} "
                f"| +{project.additions} / -{project.deletions} "
                f"| {', '.join(project.requirements) or '—'} |"
            )
        lines.append("")

        for project in projects:
            lines.extend([f"## {project.name}", "", project.url, ""])
            if not project.results:
                lines.extend(["No analyzed commits.", ""])
                continue
            for result in project.results:
                lines.extend(
                    [
                        f"### {result.sha[:8]} — {result.author} ({result.date[:10]})",
                        "",
                        f"**Message:** {result.subject}",
                        "",
                    ]
                )
                if result.summary:
                    lines.extend([result.summary, ""])
        return "\n".join(lines)
