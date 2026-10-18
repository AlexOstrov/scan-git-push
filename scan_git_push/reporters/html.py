"""Interactive HTML report generator using Jinja2 (self-contained, no CDN)."""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

from loguru import logger

from scan_git_push.analyzer import AnalysisResult

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Git Push Analysis: {{ repo_name }}</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0d1117; color: #c9d1d9; }
.container { display: flex; height: 100vh; }
.sidebar { width: 300px; background: #161b22; border-right: 1px solid #30363d; overflow-y: auto; padding: 16px; }
.sidebar h2 { color: #58a6ff; margin-bottom: 12px; font-size: 14px; }
.commit-item { padding: 8px 12px; margin-bottom: 4px; border-radius: 6px; cursor: pointer; font-size: 12px; transition: background 0.2s; }
.commit-item:hover { background: #21262d; }
.commit-item.active { background: #1f6feb33; border-left: 3px solid #1f6feb; }
.commit-item .sha { color: #d29922; font-family: monospace; }
.commit-item .author { color: #58a6ff; }
.commit-item .date { color: #8b949e; }
.main { flex: 1; overflow-y: auto; padding: 24px; }
.commit-detail { max-width: 900px; }
.commit-detail h3 { color: #58a6ff; margin-bottom: 8px; }
.commit-detail .meta { color: #8b949e; margin-bottom: 16px; font-size: 13px; }
.file-list { margin-bottom: 16px; }
.file-item { padding: 6px 12px; background: #161b22; border-radius: 4px; margin-bottom: 4px; font-family: monospace; font-size: 12px; }
.summary-panel { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; white-space: pre-wrap; line-height: 1.6; }
.summary-panel h4 { color: #3fb950; margin-bottom: 8px; }
.stats { display: flex; gap: 16px; margin-bottom: 24px; }
.stat-card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px 24px; text-align: center; }
.stat-card .num { font-size: 24px; font-weight: bold; color: #58a6ff; }
.stat-card .label { font-size: 12px; color: #8b949e; margin-top: 4px; }
.chunk-badge { display: inline-block; background: #1f6feb33; color: #58a6ff; padding: 2px 8px; border-radius: 12px; font-size: 11px; margin-left: 8px; }
</style>
</head>
<body>
<div class="container">
  <div class="sidebar">
    <h2>{{ repo_name }} ({{ days }} days)</h2>
    {% for r in results %}
    <div class="commit-item" data-sha="{{ r.sha }}" onclick="showCommit('{{ r.sha }}')">
      <span class="sha">{{ r.sha[:8] }}</span>
      <span class="author">{{ r.author }}</span>
      <br>
      <span class="date">{{ r.date[:10] }}</span>
    </div>
    {% endfor %}
  </div>
  <div class="main">
    <div class="stats">
      <div class="stat-card"><div class="num">{{ results|length }}</div><div class="label">Commits</div></div>
      <div class="stat-card"><div class="num">{{ total_files }}</div><div class="label">Files</div></div>
      <div class="stat-card"><div class="num">{{ total_chunks }}</div><div class="label">LLM Chunks</div></div>
    </div>
    {% for r in results %}
    <div class="commit-detail" id="detail-{{ r.sha }}" style="display: none;">
      <h3>{{ r.sha[:8] }} — {{ r.author }}</h3>
      <div class="meta">{{ r.date[:10] }}</div>
      <p><strong>Message:</strong> {{ r.message.splitlines()[0] }}</p>
      {% if r.files %}
      <div class="file-list">
        {% for f in r.files %}
        <div class="file-item">{{ f }}</div>
        {% endfor %}
      </div>
      {% endif %}
      {% if r.summary %}
      <div class="summary-panel">
        <h4>Summary {% if r.chunks_analyzed > 1 %}<span class="chunk-badge">{{ r.chunks_analyzed }} chunks</span>{% endif %}</h4>
        {{ r.summary }}
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
  document.getElementById('detail-' + sha).style.display = 'block';
  document.querySelector('[data-sha="' + sha + '"]').classList.add('active');
}
// Show first commit by default
document.querySelector('.commit-item')?.click();
</script>
</body>
</html>
"""


class HTMLReporter:
    """Generates a self-contained interactive HTML report."""

    def generate(
        self,
        results: list[AnalysisResult],
        repo_name: str,
        days: int,
        output_dir: Path,
    ) -> Path:
        """Write the HTML report and return the file path."""
        from jinja2 import Template

        date_str = datetime.now().strftime("%Y%m%d")
        filename = output_dir / f"{repo_name}_{date_str}.html"

        template = Template(HTML_TEMPLATE)
        content = template.render(
            results=results,
            repo_name=repo_name,
            days=days,
            total_files=sum(len(r.files) for r in results),
            total_chunks=sum(r.chunks_analyzed for r in results),
        )

        filename.parent.mkdir(parents=True, exist_ok=True)
        filename.write_text(content, encoding="utf-8")
        logger.info(f"HTML report saved: {filename}")
        return filename
