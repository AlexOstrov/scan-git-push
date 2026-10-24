# scan-git-push

LLM-powered analysis of git push changes for a configurable GitHub repository.

## Features

- Analyzes commits from the last N days
- Supports Ollama (local) and Mistral AI (online, free tier) LLM providers
- Supports report generation from database without LLM (`report-from-db` command)
- Outputs to console (Rich), Markdown, and self-contained interactive HTML
- Automatic diff chunking for large commits
- Retry logic with exponential backoff
- Artifact memory for session continuity

## Installation

```bash
python -m venv .venv
.venv\Scripts\activate  # Windows
pip install -e .
```

## Configuration

Edit `config.json`:

```json
{
  "GIT_PROJECT": "https://github.com/owner/repo",
  "GIT_DAY": 7,
  "SECRETS_PATH": "d:\\projects\\fullstack-demo\\.secrets",
  "LLM_PROVIDER": "mistral"
}
```

Secrets file (`.secrets`) must contain `GITHUB_TOKEN` and `MISTRAL_API_KEY` (see `user-guide.html`).

## Usage

```bash
python -m scan_git_push
```

## Testing

```bash
pytest
```
