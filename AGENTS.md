# AGENTS.md

## Project

Python 3.11+ CLI utility that analyzes git push changes for a configurable repository over N days, using an LLM provider (Ollama or GitHub Models API free tier). Outputs results to console (Rich), Markdown, and self-contained interactive HTML.

## Run

```bash
python -m scan_git_push
```

Requires:
- Secrets file at `d:\projects\fullstack-demo\.secrets` with `GITHUB_TOKEN` (always) and `GROQ_API_KEY` (required when `LLM_PROVIDER=groq`)
- At least one LLM provider: Ollama at `http://localhost:11434`, Groq (`https://api.groq.com/openai/v1`), or GitHub Models (retired 30.07.2026)

Configuration: `config.json` (validated via Pydantic Settings).

## Architecture

```
scan_git_push/
├── cli.py                # Typer CLI entry point
├── config.py             # Pydantic Settings validation
├── github_client.py      # PyGithub + retry logic
├── llm/
│   ├── base.py               # Provider abstraction
│   ├── ollama.py             # Ollama SDK provider
│   ├── openai_compatible.py # Generic OpenAI-compatible provider (httpx)
│   ├── github_models.py      # GitHub Models (retired 30.07.2026)
│   └── groq.py               # Groq (OpenAI-compatible)
├── analyzer.py           # Commit analysis + diff chunking
├── reporters/
│   ├── console.py        # Rich console output
│   ├── markdown.py       # Markdown report
│   └── html.py           # Interactive HTML (Jinja2, no CDN deps)
└── memory/
    └── manager.py        # docs/memory/ artifact management
```

## Key Conventions

- **LLM providers**: On startup all providers are probed best-effort; models from all available providers are offered in one interactive list (runtime switching). `LLM_PROVIDER` only sets the default and token requirement.
- **Token validation**: `GITHUB_MODELS_TOKEN` (exit code 6) and `GROQ_API_KEY` (exit code 7) are validated with a clear message when their provider is selected; if absent and provider is `ollama`, they are skipped gracefully.
- **OpenAI-compatible providers**: Implemented via plain `httpx` (`OpenAICompatibleProvider`) — no `openai` SDK dependency. Availability uses a minimal chat request; model listing falls back to a curated list.
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
