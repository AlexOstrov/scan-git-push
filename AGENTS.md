# AGENTS.md

## Project

Python 3.11+ CLI utility that analyzes git push changes for a configurable repository over N days, using an LLM provider (Ollama or GitHub Models API free tier). Outputs results to console (Rich), Markdown, and self-contained interactive HTML.

## Run

```bash
python -m scan_git_push
```

Requires:
- Ollama running locally at `http://localhost:11434` (if using `ollama` provider), OR
- GitHub Models API access via token
- Secrets file at `d:\projects\fullstack-demo\.secrets` (INI format: `GITHUB_TOKEN`, `GITHUB_MODELS_TOKEN`)

Configuration: `config.json` (validated via Pydantic Settings).

## Architecture

```
scan_git_push/
├── cli.py                # Typer CLI entry point
├── config.py             # Pydantic Settings validation
├── github_client.py      # PyGithub + retry logic
├── llm/
│   ├── base.py           # Provider abstraction
│   ├── ollama.py         # Ollama SDK provider
│   └── github_models.py  # OpenAI-compatible GitHub Models provider
├── analyzer.py           # Commit analysis + diff chunking
├── reporters/
│   ├── console.py        # Rich console output
│   ├── markdown.py       # Markdown report
│   └── html.py           # Interactive HTML (Jinja2, no CDN deps)
└── memory/
    └── manager.py        # docs/memory/ artifact management
```

## Key Conventions

- **LLM providers**: Selected at runtime from available models; support both `ollama` and `github_models`
- **Diff chunking**: Splits on file boundaries at 12 000 chars (`MAX_DIFF_CHARS_PER_CHUNK`); merges per-chunk summaries
- **Retry policy**: 3 attempts, exponential backoff factor 2, 30 s HTTP timeout
- **Merge commits**: Excluded from analysis
- **HTML reports**: Must be fully self-contained (no CDN, works offline)

## Artifact Memory

On session start, read `docs/memory/STATE.md` — do not re-analyze the project.

| Event | Action |
|-------|--------|
| Session start | Read `docs/memory/STATE.md` |
| Feature completed | Update `FEATURES.md` + `STATE.md` |
| Architecture decision | Update `DECISIONS.md` + `STATE.md` |
| Task completed | Update "Current task" section in `STATE.md` |
| New task | Add to "Next steps" at session start |

Memory files: `docs/memory/{STATE,FEATURES,DECISIONS,CHANGELOG}.md`

## Reports

Generated to `reports/`:
- `{repo}_{date}.md` — Markdown report
- `{repo}_{date}.html` — Interactive HTML with commit navigation, diff highlighting, author/date filters
