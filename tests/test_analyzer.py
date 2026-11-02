"""Tests for commit analysis and diff chunking."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from scan_git_push.analysis.chunker import build_chunks, describe_files, split_hunks, truncate_patch
from scan_git_push.analyzer import NO_PATCH_SUMMARY, CommitAnalyzer
from scan_git_push.git.models import CommitData, FileChange
from scan_git_push.llm.base import LLMResponse


class FakeProvider:
    """Minimal LLM stand-in that records prompts."""

    provider_name = "fake"

    def __init__(self, text: str = "Test summary") -> None:
        self.model = "test-model"
        self.text = text
        self.prompts: list[str] = []

    def generate(self, prompt: str, system_prompt: str | None = None) -> LLMResponse:
        self.prompts.append(prompt)
        return LLMResponse(text=self.text, model=self.model, provider="fake")


def _commit(files: list[FileChange], **kwargs) -> CommitData:
    return CommitData(
        sha=kwargs.pop("sha", "abc123def456"),
        author=kwargs.pop("author", "testuser"),
        date=kwargs.pop("date", datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)),
        message=kwargs.pop("message", "Test commit message"),
        files=files,
        **kwargs,
    )


class TestChunker:
    def test_truncate_patch_marks_truncation(self) -> None:
        patch = "diff --git a/f b/f\n@@ -1 +1 @@\n" + "+line\n" * 500
        text, truncated = truncate_patch(patch, 200)
        assert truncated is True
        assert text.endswith("[diff truncated]")

    def test_truncate_patch_keeps_small_patch(self) -> None:
        patch = "@@ -1 +1 @@\n+one\n"
        text, truncated = truncate_patch(patch, 1000)
        assert text == patch
        assert truncated is False

    def test_split_hunks_marks_parts(self) -> None:
        patch = "diff --git a/f b/f\n" + "".join(
            f"@@ -{i},1 +{i},1 @@\n" + "+line\n" * 10 for i in range(1, 10)
        )
        pieces = split_hunks(patch, 300)
        assert len(pieces) > 1
        assert "part 1/" in pieces[0]
        assert "part 2/" in pieces[1]

    def test_build_chunks_respects_file_boundaries(self) -> None:
        files = [
            FileChange(path=f"file{i}.py", patch="@@ -0,0 +1 @@\n" + "+line\n" * 20)
            for i in range(4)
        ]
        chunks = build_chunks(files, max_chunk_chars=300, max_patch_chars_per_file=10_000)
        assert len(chunks) >= 2
        assert all(chunk.paths for chunk in chunks)
        assert [chunk.index for chunk in chunks] == list(range(len(chunks)))

    def test_build_chunks_applies_per_file_cap(self) -> None:
        big = FileChange(path="big.py", patch="@@ -0,0 +1 @@\n" + "+line\n" * 2000)
        chunks = build_chunks([big], max_chunk_chars=100_000, max_patch_chars_per_file=500)
        assert len(chunks) == 1
        assert len(chunks[0].text) < 1000
        assert "[diff truncated]" in chunks[0].text

    def test_binary_and_patchless_files_are_skipped(self) -> None:
        files = [
            FileChange(path="logo.png", is_binary=True, patch="Binary files differ"),
            FileChange(path="deleted.py", status="deleted", patch=""),
        ]
        assert build_chunks(files) == []

    def test_describe_files_flags(self) -> None:
        text = describe_files(
            [
                FileChange(path="a.py", additions=2, deletions=1),
                FileChange(path="b.bin", is_binary=True),
            ]
        )
        assert "a.py (modified, +2 -1)" in text
        assert "b.bin" in text and "binary" in text

    def test_describe_files_empty(self) -> None:
        assert describe_files([]) == "  (нет изменённых файлов)"


class TestCommitAnalyzer:
    def test_single_chunk(self) -> None:
        provider = FakeProvider("Single summary")
        analyzer = CommitAnalyzer(provider, max_chunk_chars=12_000)

        result = analyzer.analyze_commit(
            _commit([FileChange(path="a.py", patch="@@ -0,0 +1 @@\n+x\n", additions=1)])
        )

        assert result.sha == "abc123def456"
        assert result.author == "testuser"
        assert result.summary == "Single summary"
        assert result.chunks_analyzed == 1
        assert result.files == ["a.py"]
        assert result.provider == "fake"
        assert result.model == "test-model"
        assert len(provider.prompts) == 1

    def test_no_patch_skips_llm(self) -> None:
        provider = FakeProvider()
        analyzer = CommitAnalyzer(provider)
        result = analyzer.analyze_commit(
            _commit([FileChange(path="logo.png", is_binary=True)])
        )
        assert result.summary == NO_PATCH_SUMMARY
        assert result.chunks_analyzed == 0
        assert provider.prompts == []

    def test_multiple_chunks_are_merged(self) -> None:
        provider = FakeProvider("Chunk summary")
        analyzer = CommitAnalyzer(provider, max_chunk_chars=300, max_patch_chars_per_file=10_000)
        files = [
            FileChange(path=f"file{i}.py", patch="@@ -0,0 +1 @@\n" + "+line\n" * 20)
            for i in range(4)
        ]

        result = analyzer.analyze_commit(_commit(files))

        assert result.chunks_analyzed > 1
        # One call per chunk + one merge call.
        assert len(provider.prompts) == result.chunks_analyzed + 1
        assert "[Часть 1]" in provider.prompts[-1]

    def test_merge_summary_prompt_mentions_parts(self) -> None:
        provider = FakeProvider("Merged")
        analyzer = CommitAnalyzer(provider, max_chunk_chars=100, max_patch_chars_per_file=100)
        files = [
            FileChange(path=f"f{i}.py", patch="@@ -0,0 +1 @@\n" + "+x\n" * 5)
            for i in range(5)
        ]
        result = analyzer.analyze_commit(_commit(files))
        assert result.summary == "Merged"
        assert result.chunks_analyzed >= 2

    def test_additions_and_deletions(self) -> None:
        provider = FakeProvider()
        analyzer = CommitAnalyzer(provider)
        result = analyzer.analyze_commit(
            _commit(
                [
                    FileChange(path="a.py", additions=3, deletions=1, patch="@@ +1 @@\n+x\n"),
                    FileChange(path="b.py", additions=2, deletions=4, patch="@@ +1 @@\n+y\n"),
                ]
            )
        )
        assert result.additions == 5
        assert result.deletions == 5

    def test_prompt_contains_commit_metadata(self) -> None:
        provider = FakeProvider()
        analyzer = CommitAnalyzer(provider)
        analyzer.analyze_commit(
            _commit(
                [FileChange(path="a.py", patch="@@ -0,0 +1 @@\n+x\n")],
                message="Fix the bug",
            )
        )
        prompt = provider.prompts[0]
        assert "abc123de" in prompt
        assert "Fix the bug" in prompt
        assert "a.py" in prompt
