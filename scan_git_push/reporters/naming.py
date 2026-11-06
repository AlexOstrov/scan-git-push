"""Deterministic report file names shared by Markdown and HTML reporters."""

from __future__ import annotations

import hashlib
from pathlib import Path

from scan_git_push.git.url import url_key


def report_stem(
    name: str, project_url: str = "", base_dir: Path | None = None
) -> str:
    """Return ``<name>-<hash>`` where the hash comes from the project ``url_key``.

    Two projects with the same short name (for example ``backend`` from
    different owners) must not overwrite each other's reports. The suffix is
    always appended so names stay deterministic even when there is no collision.
    """
    key = project_url.strip() or name
    try:
        canonical = url_key(key, base_dir)
    except ValueError:
        canonical = key
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:6]
    return f"{name}-{digest}"
