"""Tests for memory manager module."""

from __future__ import annotations

from pathlib import Path

import pytest

from scan_git_push.memory.manager import MemoryManager


class TestMemoryManager:
    """Test MemoryManager class."""

    def test_ensure_files(self, tmp_path: Path) -> None:
        """Test creating memory directory and files."""
        memory_dir = tmp_path / "docs/memory"
        mgr = MemoryManager(memory_dir)
        mgr.ensure_files()

        assert memory_dir.exists()
        assert mgr.state_file.exists()
        assert mgr.features_file.exists()
        assert mgr.decisions_file.exists()
        assert mgr.changelog_file.exists()

    def test_read_state(self, tmp_path: Path) -> None:
        """Test reading STATE.md content."""
        memory_dir = tmp_path / "docs/memory"
        mgr = MemoryManager(memory_dir)
        mgr.ensure_files()
        mgr.state_file.write_text("# Test state\nContent here", encoding="utf-8")

        content = mgr.read_state()
        assert "Test state" in content
        assert "Content here" in content

    def test_update_state(self, tmp_path: Path) -> None:
        """Test updating STATE.md."""
        memory_dir = tmp_path / "docs/memory"
        mgr = MemoryManager(memory_dir)
        mgr.ensure_files()

        mgr.update_state(
            current_task="Test task",
            implemented=["Feature 1", "Feature 2"],
            decisions=["Decision 1"],
            next_steps=["Step 1", "Step 2"],
        )

        content = mgr.state_file.read_text(encoding="utf-8")
        assert "Test task" in content
        assert "Feature 1" in content
        assert "Decision 1" in content
        assert "Step 1" in content

    def test_append_feature(self, tmp_path: Path) -> None:
        """Test appending to FEATURES.md."""
        memory_dir = tmp_path / "docs/memory"
        mgr = MemoryManager(memory_dir)
        mgr.ensure_files()

        mgr.append_feature("New feature")
        mgr.append_feature("Another feature")

        content = mgr.features_file.read_text(encoding="utf-8")
        assert "New feature" in content
        assert "Another feature" in content

    def test_append_decision(self, tmp_path: Path) -> None:
        """Test appending to DECISIONS.md."""
        memory_dir = tmp_path / "docs/memory"
        mgr = MemoryManager(memory_dir)
        mgr.ensure_files()

        mgr.append_decision("Use Python 3.11+")

        content = mgr.decisions_file.read_text(encoding="utf-8")
        assert "Use Python 3.11+" in content

    def test_append_changelog(self, tmp_path: Path) -> None:
        """Test appending to CHANGELOG.md."""
        memory_dir = tmp_path / "docs/memory"
        mgr = MemoryManager(memory_dir)
        mgr.ensure_files()

        mgr.append_changelog("Test entry")

        content = mgr.changelog_file.read_text(encoding="utf-8")
        assert "Test entry" in content
