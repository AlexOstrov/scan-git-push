"""Artifact memory manager for docs/memory/ directory."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from loguru import logger


MEMORY_TEMPLATE = """# Текущее состояние проекта

## Текущая задача
{current_task}

## Реализованы
{implemented}

## Архитектурные решения
{decisions}

## Следующие шаги
{next_steps}

## Последнее обновление
{timestamp}
"""


class MemoryManager:
    """Manages artifact memory files in docs/memory/."""

    def __init__(self, memory_dir: Path) -> None:
        self.memory_dir = memory_dir
        self.state_file = memory_dir / "STATE.md"
        self.features_file = memory_file = memory_dir / "FEATURES.md"
        self.decisions_file = memory_dir / "DECISIONS.md"
        self.changelog_file = memory_dir / "CHANGELOG.md"

    def ensure_files(self) -> None:
        """Create memory directory and files if they don't exist."""
        self.memory_dir.mkdir(parents=True, exist_ok=True)

        if not self.state_file.exists():
            self.state_file.write_text(
                "# Текущее состояние проекта\n\n(Initial state — not yet populated)\n",
                encoding="utf-8",
            )

        for file in [self.features_file, self.decisions_file, self.changelog_file]:
            if not file.exists():
                file.write_text(f"# {file.stem}\n", encoding="utf-8")

    def read_state(self) -> str:
        """Read current STATE.md content."""
        if not self.state_file.exists():
            self.ensure_files()
        return self.state_file.read_text(encoding="utf-8")

    def update_state(
        self,
        current_task: str = "",
        implemented: list[str] | None = None,
        decisions: list[str] | None = None,
        next_steps: list[str] | None = None,
    ) -> None:
        """Update STATE.md with current project state."""
        content = MEMORY_TEMPLATE.format(
            current_task=current_task or "(не задана)",
            implemented="\n".join(f"- {item}" for item in (implemented or [])),
            decisions="\n".join(f"- {item}" for item in (decisions or [])),
            next_steps="\n".join(f"{i}. {step}" for i, step in enumerate(next_steps or [], 1)),
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M"),
        )
        self.state_file.write_text(content, encoding="utf-8")
        logger.info("STATE.md updated")

    def append_feature(self, feature: str) -> None:
        """Append a new feature to FEATURES.md."""
        with self.features_file.open("a", encoding="utf-8") as f:
            f.write(f"- {feature}\n")
        logger.info(f"FEATURES.md updated: {feature}")

    def append_decision(self, decision: str) -> None:
        """Append a new decision to DECISIONS.md."""
        with self.decisions_file.open("a", encoding="utf-8") as f:
            f.write(f"- {decision}\n")
        logger.info(f"DECISIONS.md updated: {decision}")

    def append_changelog(self, entry: str) -> None:
        """Append a new entry to CHANGELOG.md."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        with self.changelog_file.open("a", encoding="utf-8") as f:
            f.write(f"- [{timestamp}] {entry}\n")
        logger.info(f"CHANGELOG.md updated: {entry}")
