"""Commit analyzer with diff chunking for large changes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from scan_git_push.github_client import CommitData
    from scan_git_push.llm.base import LLMProvider


SYSTEM_PROMPT = """Ты — аналитик кода. Проанализируй изменения в коммите и выдай структурированное саммари.

Формат ответа:
1. Краткое описание изменений (1-3 предложения)
2. Основные изменения по файлам
3. Потенциальные риски или проблемы
4. Общая оценка качества изменений (1-5)
"""


@dataclass
class ChunkResult:
    """Result of analyzing a single diff chunk."""

    chunk_index: int
    summary: str


@dataclass
class AnalysisResult:
    """Complete analysis result for a single commit."""

    sha: str
    author: str
    date: str
    message: str
    files: list[str] = field(default_factory=list)
    summary: str = ""
    chunks_analyzed: int = 0


class CommitAnalyzer:
    """Analyzes commits using an LLM provider with diff chunking."""

    def __init__(
        self,
        llm_provider: LLMProvider,
        max_chunk_chars: int = 12_000,
        max_retries: int = 3,
        backoff_factor: float = 2.0,
    ) -> None:
        self.llm = llm_provider
        self.max_chunk_chars = max_chunk_chars
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor

    def analyze_commit(self, commit: CommitData) -> AnalysisResult:
        """Analyze a single commit, chunking diff if it exceeds the limit."""
        diff_text = self._build_diff_text(commit)

        if len(diff_text) <= self.max_chunk_chars:
            # Small enough for a single request
            summary = self._analyze_single(commit, diff_text)
            chunks_analyzed = 1
        else:
            # Need to chunk and merge
            chunks = self._chunk_diff(commit)
            chunk_results: list[ChunkResult] = []
            for i, chunk in enumerate(chunks, start=1):
                logger.info(f"Analyzing chunk {i}/{len(chunks)} for commit {commit.sha[:8]}")
                chunk_summary = self._analyze_chunk(commit, chunk, i, len(chunks))
                chunk_results.append(ChunkResult(chunk_index=i, summary=chunk_summary))
            summary = self._merge_summaries(chunk_results)
            chunks_analyzed = len(chunks)

        return AnalysisResult(
            sha=commit.sha,
            author=commit.author,
            date=commit.date.isoformat(),
            message=commit.message,
            files=[f.filename for f in commit.files],
            summary=summary,
            chunks_analyzed=chunks_analyzed,
        )

    def _build_diff_text(self, commit: CommitData) -> str:
        """Build a unified diff text from commit files."""
        parts: list[str] = []
        for file in commit.files:
            if file.patch:
                parts.append(f"--- a/{file.filename}\n+++ b/{file.filename}\n{file.patch}")
        return "\n\n".join(parts)

    def _chunk_diff(self, commit: CommitData) -> list[str]:
        """Split the diff into chunks at file boundaries, respecting max size."""
        chunks: list[str] = []
        current_chunk: list[str] = []
        current_size = 0

        for file in commit.files:
            if not file.patch:
                continue

            file_diff = f"--- a/{file.filename}\n+++ b/{file.filename}\n{file.patch}"
            file_size = len(file_diff)

            # If adding this file would exceed the limit, start a new chunk
            if current_size + file_size > self.max_chunk_chars and current_chunk:
                chunks.append("\n\n".join(current_chunk))
                current_chunk = []
                current_size = 0

            # If a single file exceeds the limit, we still include it as its own chunk
            # (the LLM will receive it as-is; truncation happens at API level)
            current_chunk.append(file_diff)
            current_size += file_size

        if current_chunk:
            chunks.append("\n\n".join(current_chunk))

        return chunks

    def _analyze_single(self, commit: CommitData, diff_text: str) -> str:
        """Analyze a commit that fits in a single chunk."""
        prompt = self._build_prompt(commit, diff_text)
        response = self.llm.generate(prompt, system_prompt=SYSTEM_PROMPT)
        return response.text

    def _analyze_chunk(
        self,
        commit: CommitData,
        chunk: str,
        chunk_index: int,
        total_chunks: int,
    ) -> str:
        """Analyze a single chunk of a large commit."""
        prompt = (
            f"Это часть {chunk_index} из {total_chunks} коммита.\n\n"
            + self._build_prompt(commit, chunk)
        )
        response = self.llm.generate(prompt, system_prompt=SYSTEM_PROMPT)
        return response.text

    def _merge_summaries(self, chunk_results: list[ChunkResult]) -> str:
        """Merge per-chunk summaries into a single cohesive summary."""
        if len(chunk_results) == 1:
            return chunk_results[0].summary

        merged_input = "\n\n---\n\n".join(
            f"[Чась {r.chunk_index}]\n{r.summary}" for r in chunk_results
        )
        merge_prompt = (
            "Объедини следующие саммари частей одного коммита в единое целое. "
            "Устрани дублирование, сохрани структуру из 4 пунктов:\n\n"
            + merged_input
        )
        response = self.llm.generate(merge_prompt, system_prompt=SYSTEM_PROMPT)
        return response.text

    def _build_prompt(self, commit: CommitData, diff_text: str) -> str:
        """Build the analysis prompt for the LLM."""
        file_list = "\n".join(f"  - {f.filename} ({f.status})" for f in commit.files)

        return f"""Проанализируй изменения в коммите {commit.sha[:8]} ({commit.author}, {commit.date.strftime('%Y-%m-%d %H:%M')}):

Сообщение коммита:
{commit.message}

Изменённые файлы:
{file_list}

Patch/diff:
{diff_text}
"""
