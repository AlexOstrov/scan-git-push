# scan-git-push

LLLM-powered analysis of git push changes for a configurable GitHub repository.

## Features

- Analyzes commits from the last N days
- Supports Ollama (local) and Groq (online, free tier) LLM providers
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
  "LLM_PROVIDER": "groq"
}
```

Secrets file (`.secrets`) must contain `GITHUB_TOKEN` and `GROQ_API_KEY` (see `user-guide.html`).

## Usage

```bash
python -m scan_git_push
```

## Testing

```bash
pytest
```
