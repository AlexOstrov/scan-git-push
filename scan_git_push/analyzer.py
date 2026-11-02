"""Commit analyzer with diff chunking and per-file patch limits."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from loguru import logger

from scan_git_push.analysis.chunker import build_chunks, describe_files
from scan_git_push.git.models import FileChange

if TYPE_CHECKING:
    from scan_git_push.git.models import CommitData
    from scan_git_push.llm.base import LLMProvider

PROMPT_VERSION = 1

SYSTEM_PROMPT = """Ты — аналитик кода. Проанализируй изменения в коммите и выдай структурированное саммари.

Формат ответа:
1. Краткое описание изменений (1-3 предложения)
2. Основные изменения по файлам
3. Потенциальные риски или проблемы
4. Общая оценка качества изменений (1-5)
"""

NO_PATCH_SUMMARY = (
    "Текстовый diff отсутствует: коммит содержит только бинарные файлы, "
    "удалённые файлы или изменения, исключённые настройкой ANALYSIS.EXCLUDE."
)


@dataclass
class ChunkResult:
    """Result of analyzing a single diff chunk."""

    chunk_index: int
    summary: str


@dataclass
class RequirementRef:
    """Requirement marker affected by a commit."""

    code: str
    point: str
    name: str = ""
    path: str = ""

    @property
    def label(self) -> str:
        return f"{self.code} п.{self.point}" if self.point else self.code


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
    file_changes: list[FileChange] = field(default_factory=list)
    additions: int = 0
    deletions: int = 0
    requirements: list[RequirementRef] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    project: str = ""
    project_name: str = ""

    @property
    def subject(self) -> str:
        return self.message.splitlines()[0] if self.message else ""

    @property
    def requirement_labels(self) -> list[str]:
        return [item.label for item in self.requirements]


class CommitAnalyzer:
    """Analyzes commits using an LLM provider with diff chunking."""

    def __init__(
        self,
        llm_provider: LLMProvider,
        max_chunk_chars: int = 12_000,
        max_patch_chars_per_file: int = 4_000,
        max_retries: int = 3,
        backoff_factor: float = 2.0,
    ) -> None:
        self.llm = llm_provider
        self.max_chunk_chars = max_chunk_chars
        self.max_patch_chars_per_file = max_patch_chars_per_file
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor

    def analyze_commit(self, commit: CommitData) -> AnalysisResult:
        """Analyze a single commit, chunking the diff when needed."""
        chunks = build_chunks(
            commit.files,
            max_chunk_chars=self.max_chunk_chars,
            max_patch_chars_per_file=self.max_patch_chars_per_file,
        )

        if not chunks:
            summary = NO_PATCH_SUMMARY
            chunks_analyzed = 0
        elif len(chunks) == 1:
            summary = self._analyze_single(commit, chunks[0].text)
            chunks_analyzed = 1
        else:
            chunk_results: list[ChunkResult] = []
            for chunk in chunks:
                logger.info(
                    "Analyzing chunk {}/{} for commit {}",
                    chunk.index + 1,
                    len(chunks),
                    commit.sha[:8],
                )
                text = self._analyze_chunk(
                    commit, chunk.text, chunk.index + 1, len(chunks)
                )
                chunk_results.append(ChunkResult(chunk.index + 1, text))
            summary = self._merge_summaries(chunk_results)
            chunks_analyzed = len(chunks)

        return AnalysisResult(
            sha=commit.sha,
            author=commit.author,
            date=commit.date.isoformat(),
            message=commit.message,
            files=[file.path for file in commit.files],
            summary=summary,
            chunks_analyzed=chunks_analyzed,
            file_changes=list(commit.files),
            additions=commit.additions,
            deletions=commit.deletions,
            provider=getattr(self.llm, "provider_name", "") or "",
            model=getattr(self.llm, "model", "") or "",
        )

    def _analyze_single(self, commit: CommitData, diff_text: str) -> str:
        prompt = self._build_prompt(commit, diff_text)
        return self.llm.generate(prompt, system_prompt=SYSTEM_PROMPT).text

    def _analyze_chunk(
        self,
        commit: CommitData,
        chunk: str,
        chunk_index: int,
        total_chunks: int,
    ) -> str:
        prompt = (
            f"Это часть {chunk_index} из {total_chunks} коммита.\n\n"
            + self._build_prompt(commit, chunk)
        )
        return self.llm.generate(prompt, system_prompt=SYSTEM_PROMPT).text

    def _merge_summaries(self, chunk_results: list[ChunkResult]) -> str:
        if len(chunk_results) == 1:
            return chunk_results[0].summary

        merged_input = "\n\n---\n\n".join(
            f"[Часть {result.chunk_index}]\n{result.summary}" for result in chunk_results
        )
        merge_prompt = (
            "Объедини следующие саммари частей одного коммита в единое целое. "
            "Устрани дублирование, сохрани структуру из 4 пунктов:\n\n" + merged_input
        )
        return self.llm.generate(merge_prompt, system_prompt=SYSTEM_PROMPT).text

    def _build_prompt(self, commit: CommitData, diff_text: str) -> str:
        return f"""Проанализируй изменения в коммите {commit.sha[:8]} ({commit.author}, {commit.date.strftime('%Y-%m-%d %H:%M')}):

Сообщение коммита:
{commit.message}

Изменённые файлы:
{describe_files(commit.files)}

Patch/diff:
{diff_text}
"""
