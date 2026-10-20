# AGENTS.md

## Project

Python 3.11+ CLI utility that analyzes git push changes for a configurable repository over N days, using an LLM provider (Ollama or GitHub Models API free tier). Outputs results to console (Rich), Markdown, and self-contained interactive HTML.

## Run

```bash
python -m scan_git_push
```

Requires:
- Secrets file at `d:\projects\fullstack-demo\.secrets` with `GITHUB_TOKEN` (always) and `GITHUB_MODELS_TOKEN` (required when `LLM_PROVIDER=github_models`)
- At least one LLM provider: Ollama at `http://localhost:11434` or GitHub Models API (`https://models.github.ai/inference`, needs internet)

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

- **LLM providers**: On startup both providers are probed best-effort; models from all available providers are offered in one interactive list (runtime switching). `LLM_PROVIDER` only sets the default and token requirement.
- **Token validation**: `GITHUB_MODELS_TOKEN` is validated with a clear message and exit code 6 when `LLM_PROVIDER=github_models`; if absent and provider is `ollama`, GitHub Models is skipped gracefully.
- **GitHub Models listing**: `GitHubModelsProvider` checks availability via a minimal chat request and falls back to a curated `KNOWN_GITHUB_MODELS` list because the inference endpoint does not expose a JSON `/models` listing.
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
