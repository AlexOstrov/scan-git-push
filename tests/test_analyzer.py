"""Tests for commit analyzer module."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from scan_git_push.analyzer import CommitAnalyzer, AnalysisResult
from scan_git_push.github_client import CommitData, FileChange
from scan_git_push.llm.base import LLMResponse


class TestCommitAnalyzer:
    """Test CommitAnalyzer class."""

    def test_analyze_small_commit(self, sample_commit_data) -> None:
        """Test analyzing a commit that fits in a single chunk."""
        mock_llm = MagicMock()
        mock_llm.generate.return_value = LLMResponse(
            text="Test summary",
            model="test-model",
            provider="ollama",
            usage={},
        )

        analyzer = CommitAnalyzer(
            llm_provider=mock_llm,
            max_chunk_chars=12_000,
        )

        result = analyzer.analyze_commit(sample_commit_data)

        assert isinstance(result, AnalysisResult)
        assert result.sha == "abc123def456"
        assert result.author == "testuser"
        assert result.summary == "Test summary"
        assert result.chunks_analyzed == 1
        assert "test_file.py" in result.files

    def test_chunk_diff(self, sample_commit_data) -> None:
        """Test that large diffs are properly chunked."""
        mock_llm = MagicMock()
        mock_llm.generate.return_value = LLMResponse(
            text="Chunk summary",
            model="test-model",
            provider="ollama",
            usage={},
        )

        # Create a commit with a large diff
        large_patch = "+" + "x" * 15_000  # Exceeds default 12000 limit
        commit = CommitData(
            sha="large123",
            author="testuser",
            date=sample_commit_data.date,
            message="Large commit",
            files=[
                FileChange(
                    filename="large_file.py",
                    status="modified",
                    additions=100,
                    deletions=0,
                    patch=large_patch,
                ),
            ],
            is_merge=False,
        )

        analyzer = CommitAnalyzer(
            llm_provider=mock_llm,
            max_chunk_chars=12_000,
        )

        result = analyzer.analyze_commit(commit)

        # Should be analyzed in chunks
        assert result.chunks_analyzed >= 1
        mock_llm.generate.assert_called()

    def test_chunk_on_file_boundaries(self, sample_commit_data) -> None:
        """Test that chunking respects file boundaries."""
        mock_llm = MagicMock()
        mock_llm.generate.return_value = LLMResponse(
            text="File summary",
            model="test-model",
            provider="ollama",
            usage={},
        )

        # Create a commit with multiple files
        commit = CommitData(
            sha="multi123",
            author="testuser",
            date=sample_commit_data.date,
            message="Multi-file commit",
            files=[
                FileChange(
                    filename="file1.py",
                    status="modified",
                    additions=50,
                    deletions=0,
                    patch="@@ -0,0 +1,50 @@\n" + "+line\n" * 50,
                ),
                FileChange(
                    filename="file2.py",
                    status="modified",
                    additions=30,
                    deletions=0,
                    patch="@@ -0,0 +1,30 @@\n" + "+line\n" * 30,
                ),
            ],
            is_merge=False,
        )

        analyzer = CommitAnalyzer(
            llm_provider=mock_llm,
            max_chunk_chars=500,  # Very small to force chunking
        )

        result = analyzer.analyze_commit(commit)

        # Should be analyzed in multiple chunks
        assert result.chunks_analyzed >= 2

    def test_merge_summaries(self, sample_commit_data) -> None:
        """Test merging multiple chunk summaries."""
        mock_llm = MagicMock()
        mock_llm.generate.return_value = LLMResponse(
            text="Merged summary",
            model="test-model",
            provider="ollama",
            usage={},
        )

        analyzer = CommitAnalyzer(
            llm_provider=mock_llm,
            max_chunk_chars=100,  # Very small to force multiple chunks
        )

        # Create commit with many files to force chunking
        files = [
            FileChange(
                filename=f"file{i}.py",
                status="modified",
                additions=10,
                deletions=0,
                patch=f"@@ -0,0 +1,10 @@\n+line\n" * 10,
            )
            for i in range(10)
        ]

        commit = CommitData(
            sha="manyfiles",
            author="testuser",
            date=sample_commit_data.date,
            message="Many files",
            files=files,
            is_merge=False,
        )

        result = analyzer.analyze_commit(commit)

        # Should have multiple chunks and a merge call
        assert result.chunks_analyzed >= 2
        # At least one call for merge (when chunks > 1)
        assert mock_llm.generate.call_count >= 3
