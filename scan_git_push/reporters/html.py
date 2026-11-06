"""Interactive, self-contained HTML report generator (no CDN dependencies)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from jinja2 import Environment
from loguru import logger

from scan_git_push.analyzer import AnalysisResult
from scan_git_push.reporters.naming import report_stem

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Git Push Analysis: {{ repo_name }}</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0d1117; color: #c9d1d9; }
.container { display: flex; height: 100vh; }
.sidebar { width: 320px; background: #161b22; border-right: 1px solid #30363d; overflow-y: auto; padding: 16px; }
.sidebar h2 { color: #58a6ff; margin-bottom: 4px; font-size: 15px; }
.sidebar .project { color: #8b949e; font-size: 11px; margin-bottom: 12px; word-break: break-all; }
.commit-item { padding: 8px 12px; margin-bottom: 4px; border-radius: 6px; cursor: pointer; font-size: 12px; transition: background 0.2s; border-left: 3px solid transparent; }
.commit-item:hover { background: #21262d; }
.commit-item.active { background: #1f6feb33; border-left-color: #1f6feb; }
.commit-item .sha { color: #d29922; font-family: monospace; }
.commit-item .author { color: #58a6ff; margin-left: 6px; }
.commit-item .date { color: #8b949e; }
.main { flex: 1; overflow-y: auto; padding: 24px; }
.commit-detail { max-width: 1000px; }
.commit-detail h3 { color: #58a6ff; margin-bottom: 8px; }
.commit-detail .meta { color: #8b949e; margin-bottom: 16px; font-size: 13px; }
.file-table { width: 100%; border-collapse: collapse; margin-bottom: 16px; font-size: 12px; }
.file-table th { text-align: left; color: #8b949e; border-bottom: 1px solid #30363d; padding: 6px 8px; font-weight: 500; }
.file-table td { padding: 5px 8px; border-bottom: 1px solid #21262d; font-family: monospace; }
.file-table .status { color: #8b949e; }
.file-table .delta { text-align: right; color: #3fb950; white-space: nowrap; }
.summary-panel { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; white-space: pre-wrap; line-height: 1.6; margin-bottom: 12px; }
.summary-panel h4 { color: #3fb950; margin-bottom: 8px; }
.stats { display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }
.stat-card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px 24px; text-align: center; }
.stat-card .num { font-size: 24px; font-weight: bold; color: #58a6ff; }
.stat-card .label { font-size: 12px; color: #8b949e; margin-top: 4px; }
.badge { display: inline-block; background: #8957e533; color: #d2a8ff; padding: 2px 8px; border-radius: 12px; font-size: 11px; margin: 0 6px 6px 0; }
.chunk-badge { display: inline-block; background: #1f6feb33; color: #58a6ff; padding: 2px 8px; border-radius: 12px; font-size: 11px; margin-left: 8px; }
.empty { color: #8b949e; }
</style>
</head>
<body>
<div class="container">
  <div class="sidebar">
    <h2>{{ repo_name }} — {{ days }} d</h2>
    <div class="project">{{ project_url }}</div>
    {% for r in results %}
    <div class="commit-item" data-sha="{{ r.sha }}" onclick="showCommit('{{ r.sha }}')">
      <span class="sha">{{ r.sha[:8] }}</span><span class="author">{{ r.author }}</span><br>
      <span class="date">{{ r.date[:10] }}</span>
    </div>
    {% endfor %}
  </div>
  <div class="main">
    <div class="stats">
      <div class="stat-card"><div class="num">{{ results|length }}</div><div class="label">Commits</div></div>
      <div class="stat-card"><div class="num">{{ total_files }}</div><div class="label">Files</div></div>
      <div class="stat-card"><div class="num">+{{ total_additions }}</div><div class="label">Added lines</div></div>
      <div class="stat-card"><div class="num">-{{ total_deletions }}</div><div class="label">Deleted lines</div></div>
      <div class="stat-card"><div class="num">{{ total_chunks }}</div><div class="label">LLM chunks</div></div>
    </div>
    {% if not results %}
    <p class="empty">No analyzed commits in the selected period.</p>
    {% endif %}
    {% for r in results %}
    <div class="commit-detail" id="detail-{{ r.sha }}" style="display: none;">
      <h3>{{ r.subject }}</h3>
      <div class="meta">{{ r.sha }} · {{ r.author }} · {{ r.date }} · +{{ r.additions }} -{{ r.deletions }}</div>
      {% if r.requirements %}
      <div>
        {% for item in r.requirements %}<span class="badge">{{ item.label }}</span>{% endfor %}
      </div>
      {% endif %}
      {% if r.file_changes %}
      <table class="file-table">
        <thead><tr><th>File</th><th>Status</th><th class="delta">+/-</th></tr></thead>
        <tbody>
        {% for f in r.file_changes %}
          <tr>
            <td>{{ f.path }}</td>
            <td class="status">{{ f.status }}</td>
            <td class="delta">{% if f.is_binary %}binary{% else %}+{{ f.additions }} -{{ f.deletions }}{% endif %}</td>
          </tr>
        {% endfor %}
        </tbody>
      </table>
      {% endif %}
      {% if r.summary %}
      <div class="summary-panel">
        <h4>Summary {% if r.chunks_analyzed > 1 %}<span class="chunk-badge">{{ r.chunks_analyzed }} chunks</span>{% endif %}</h4>{{ r.summary }}
      </div>
      {% endif %}
    </div>
    {% endfor %}
  </div>
</div>
<script>
function showCommit(sha) {
  document.querySelectorAll('.commit-detail').forEach(el => el.style.display = 'none');
  document.querySelectorAll('.commit-item').forEach(el => el.classList.remove('active'));
  var detail = document.getElementById('detail-' + sha);
  if (detail) detail.style.display = 'block';
  var item = document.querySelector('[data-sha="' + sha + '"]');
  if (item) item.classList.add('active');
}
var first = document.querySelector('.commit-item');
if (first) first.click();
</script>
</body>
</html>
"""


class HTMLReporter:
    """Generates a self-contained interactive HTML report."""

    def __init__(self) -> None:
        self.env = Environment(autoescape=True)

    def generate(
        self,
        results: list[AnalysisResult],
        repo_name: str,
        days: int,
        output_dir: Path,
        project_url: str = "",
    ) -> Path:
        """Write the HTML report and return the file path."""
        date_str = datetime.now().strftime("%Y%m%d")
        stem = report_stem(repo_name, project_url)
        filename = Path(output_dir) / f"{stem}_{date_str}.html"

        template = self.env.from_string(HTML_TEMPLATE)
        content = template.render(
            results=results,
            repo_name=repo_name,
            project_url=project_url,
            days=days,
            total_files=sum(len(r.file_changes or r.files) for r in results),
            total_additions=sum(r.additions for r in results),
            total_deletions=sum(r.deletions for r in results),
            total_chunks=sum(r.chunks_analyzed for r in results),
        )

        filename.parent.mkdir(parents=True, exist_ok=True)
        filename.write_text(content, encoding="utf-8")
        logger.info("HTML report saved: {}", filename)
        return filename
