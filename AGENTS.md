# AGENTS.md

## Project

Python 3.11+ CLI that analyzes the last `ANALYSIS.GIT_DAY` commits of one or more git projects using local bare mirrors and an LLM provider (Ollama or Mistral AI). Outputs console (Rich), Markdown and self-contained interactive HTML reports. A separate `scan` mode (see `dop_tz01.md`) extracts `REQ-META` markers into `db/scan.db`.

## Run

```bash
python -m scan_git_push                       # analyze projects from SCAN.GIT_PROJECTS
python -m scan_git_push analyze --days 14 --model mistral/mistral-small-latest
python -m scan_git_push report-from-db --days 30
python -m scan_git_push scan                  # REQ-META markers -> db/scan.db (no LLM)
```

Requires:
- Python 3.11+ and git >= 2.30 in `PATH`
- Secrets file at `d:\projects\fullstack-demo\.secrets`; `MISTRAL_API_KEY` is required only for the `mistral` provider (`GITHUB_TOKEN` is optional)
- At least one LLM provider: Mistral (`https://api.mistral.ai/v1`) or Ollama at `http://localhost:11434`

Configuration: `config.json` with nested `SCAN` / `ANALYSIS` / `LLM` sections (validated via Pydantic Settings). Relative paths resolve against the config directory.

## Architecture

```
scan_git_push/
├── cli.py                    # Typer: main/analyze, report-from-db
├── config.py                 # SCAN/ANALYSIS/LLM + legacy flat-config upgrade
├── database.py               # analysis.db v1 + legacy migration
├── analyzer.py               # prompts, chunk analysis, summary merge
├── git/
│   ├── url.py                # normalize_url, url_key, repo_name
│   ├── runner.py             # subprocess git, error classification
│   ├── mirror.py             # MirrorManager (clone/fetch, partial clone)
│   ├── rev.py                # log/numstat/patch parsing
│   ├── models.py             # CommitData, FileChange
│   └── errors.py
├── analysis/
│   ├── collector.py          # commits per project
│   ├── chunker.py            # build_chunks, split_hunks
│   ├── filters.py            # glob ** and EXCLUDE
│   ├── linker.py             # scan.db requirements link
│   └── results.py            # DB rows -> AnalysisResult
├── llm/
│   ├── base.py
│   ├── ollama.py
│   ├── openai_compatible.py
│   ├── mistral.py
│   └── registry.py           # discovery, cache, resolve/build
├── reporters/
│   ├── console.py
│   ├── markdown.py
│   ├── html.py
│   ├── summary.py
│   └── service.py
├── scan/                     # scan mode: db/scan.db, REQ-META markers
│   ├── database.py
│   ├── markers.py
│   ├── files.py
│   └── scanner.py
└── memory/manager.py
```

## Key Conventions

- **No PyGithub / GitHub API**: commits and patches are read from bare mirrors in `SCAN.MIRROR_DIR`; `PyGithub`, `github_models` and `groq` must not return.
- **Mirrors**: one mirror per project, path `<repo>-<sha256(url_key)[:10]>.git`; `MIRROR_FILTER=auto` enables `--filter=blob:none` for remote URLs and full clones for local paths.
- **Commit window**: committer date (`%cI`, `git log --since`), `--no-merges`; `ANALYSIS.BRANCHES=all` (default) deduplicates SHAs via git itself.
- **Dedup key**: `(git_project_id, sha)`; `url_key` is shared with `scan.db` so both databases can be joined.
- **Reports** are always rebuilt from the database for the period, so a re-run without new commits still produces a complete report. One report per project plus `_summary_{date}`.
- **Chunking**: `MAX_PATCH_CHARS_PER_FILE` per file (hunk-boundary truncation), `MAX_DIFF_CHARS_PER_CHUNK` per chunk, oversized files split by hunks.
- **ANALYSIS.EXCLUDE**: lock files, `*.min.js`, `*.min.css`, `*.map`, `dist/`, `build/`, `vendor/`, `node_modules/`, `.git/`.
- **LLM providers**: only `ollama` and `mistral`. Discovery is lazy; Mistral model list is cached in `db/llm_models.json`. HTTP 429 counts as available.
- **Token validation**: `MISTRAL_API_KEY` → exit code `8` when mistral is selected; Ollama works without secrets.
- **Exit codes**: `0` ok, `1` config, `2` git auth, `3` no model, `4` args, `5` no project available, `8` missing key, `9` DB, `10` strict marker errors, `11` partial, `12` schema.
- **HTML reports**: fully self-contained (no CDN, works offline), Jinja2 `autoescape=True`.
- **DB PRAGMAs**: `foreign_keys=ON`, `journal_mode=WAL`, `synchronous=NORMAL` (before the transaction), `busy_timeout=5000`.
- **Tests**: use temporary git repositories (`tests/conftest.py`), never network mocks of PyGithub.

## Artifact Memory

On session start, read `docs/memory/STATE.md` — do not re-analyze the project. Plans live in `docs/SPRINTS.md`.

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
- `{repo}_{date}.html` — interactive HTML
- `_summary_{date}.md` / `.html` — cross-project summary
