"""Normalization of git URLs and local repository paths.

The same canonical key (``url_key``) is used by ``scan.db`` and ``analysis.db``
so that both databases can be joined with ``ATTACH``.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

_SCP_LIKE = re.compile(r"^(?P<user>[^@/\\:]+)@(?P<host>[^:/\\]+):(?P<path>.+)$")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
_URL_SCHEMES = {"http", "https", "ssh", "git", "file"}


def is_local_path(value: str) -> bool:
    """Return True when *value* should be treated as a filesystem path."""
    value = value.strip()
    if not value:
        return False
    if value.startswith("file://"):
        return True
    if _WINDOWS_DRIVE.match(value):
        return True
    if value.startswith(("/", "./", "../", "~", ".\\", "..\\")):
        return True
    if "://" in value:
        return False
    if _SCP_LIKE.match(value):
        return False
    # A plain relative path such as ``vendor/repo``. Remote URLs always carry a
    # scheme or the scp-like ``user@host:path`` form.
    return True


def _resolve_local(value: str, base_dir: Path | None) -> Path:
    if value.startswith("file://"):
        parsed = urlsplit(value)
        raw = parsed.path
        # file:///d:/repo -> /d:/repo
        if re.match(r"^/[A-Za-z]:", raw):
            raw = raw[1:]
        if parsed.netloc and not raw:
            raw = parsed.netloc
        path = Path(raw)
    else:
        path = Path(value).expanduser()

    if not path.is_absolute():
        path = (base_dir or Path.cwd()) / path
    return path.resolve()


def normalize_url(value: str, base_dir: Path | None = None) -> str:
    """Return a sanitized URL/path suitable for storage.

    Remote URLs lose credentials, default ports, the trailing slash and the
    ``.git`` suffix. Local paths are converted to absolute paths.
    """
    value = value.strip()
    if not value:
        raise ValueError("empty repository reference")

    if is_local_path(value):
        return _resolve_local(value, base_dir).as_posix()

    scp = _SCP_LIKE.match(value)
    if scp and "://" not in value:
        value = f"ssh://{scp.group('user')}@{scp.group('host')}/{scp.group('path')}"

    parsed = urlsplit(value)
    scheme = parsed.scheme.lower()
    if scheme not in _URL_SCHEMES:
        raise ValueError(f"unsupported git URL scheme: {parsed.scheme}")

    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError(f"git URL has no host: {value}")
    port = parsed.port
    if (scheme in {"http", "https"} and port == 80) or (scheme == "ssh" and port == 22):
        port = None

    path = parsed.path.rstrip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    path = path.rstrip("/")

    username = parsed.username or ""
    password = parsed.password
    if scheme in {"http", "https"}:
        # HTTP(S) credentials are never stored: only the host is kept.
        netloc = host if not port else f"{host}:{port}"
    else:
        # SSH needs the user (usually ``git``); a password is always stripped.
        keep_user = username if (username and not password) else ""
        netloc = f"{keep_user}@{host}" if keep_user else host
        if port:
            netloc = f"{netloc}:{port}"

    return urlunsplit((scheme, netloc, path, "", ""))


def sanitize_url(value: str, base_dir: Path | None = None) -> str:
    """Return a display-safe URL/path without credentials (alias of normalize)."""
    return normalize_url(value, base_dir)


def url_key(value: str, base_dir: Path | None = None) -> str:
    """Return the canonical cross-database key for a repository.

    * remote: ``host/path`` without scheme, credentials and ``.git``;
    * local:  absolute normalized path.
    """
    normalized = normalize_url(value, base_dir)
    if is_local_path(normalized) or _WINDOWS_DRIVE.match(normalized):
        return Path(normalized).as_posix()

    parsed = urlsplit(normalized)
    host = (parsed.hostname or "").lower()
    path = parsed.path.rstrip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    return f"{host}{path}"


def repo_name(value: str, base_dir: Path | None = None) -> str:
    """Return a short repository name used in report file names."""
    try:
        normalized = normalize_url(value, base_dir)
    except ValueError:
        normalized = value.strip().rstrip("/")
    path = normalized.rstrip("/")
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    if name.endswith(".git"):
        name = name[: -len(".git")]
    return name or "repository"
