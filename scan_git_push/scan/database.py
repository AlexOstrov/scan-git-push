"""SQLite storage for the ``scan`` mode (``scan.db``, schema version 4).

The schema is created from scratch; migrations are intentionally not supported
(``dop_tz01.md``, section 7.5). The database never stores file contents.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence
from urllib.parse import urlsplit

from loguru import logger

from scan_git_push.analysis.filters import path_matches
from scan_git_push.git.url import is_local_path, normalize_url, url_key

SCAN_SCHEMA_VERSION = 4
RUNNING_RUN_TTL = timedelta(hours=24)

SCAN_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER NOT NULL,
    applied_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS git_project (
    id          INTEGER PRIMARY KEY,
    url         TEXT    NOT NULL,
    url_key     TEXT    NOT NULL UNIQUE,
    author      TEXT,
    status      INTEGER NOT NULL DEFAULT 1 CHECK (status IN (0, 1)),
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS req (
    id              INTEGER PRIMARY KEY,
    code            TEXT    NOT NULL UNIQUE,
    name            TEXT    NOT NULL DEFAULT 'Требование без наименования',
    status          INTEGER NOT NULL DEFAULT 1 CHECK (status IN (0, 1)),
    is_export       INTEGER NOT NULL DEFAULT 0 CHECK (is_export IN (0, 1)),
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS point (
    id          INTEGER PRIMARY KEY,
    req_id      INTEGER NOT NULL REFERENCES req(id) ON DELETE CASCADE,
    point       TEXT    NOT NULL,
    status      INTEGER NOT NULL DEFAULT 1 CHECK (status IN (0, 1)),
    is_export   INTEGER NOT NULL DEFAULT 0 CHECK (is_export IN (0, 1)),
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (req_id, point)
);

CREATE TABLE IF NOT EXISTS file_hash (
    hash        TEXT    PRIMARY KEY CHECK (length(hash) = 64),
    size_bytes  INTEGER NOT NULL CHECK (size_bytes >= 0),
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS file_snapshot (
    id              INTEGER PRIMARY KEY,
    git_project_id  INTEGER NOT NULL REFERENCES git_project(id) ON DELETE CASCADE,
    branch          TEXT    NOT NULL,
    path            TEXT    NOT NULL,
    commit_sha      TEXT,
    author          TEXT,
    committer       TEXT,
    blob_hash       TEXT    NOT NULL REFERENCES file_hash(hash) ON DELETE RESTRICT,
    encoding        TEXT    NOT NULL DEFAULT 'utf-8',
    parser_version  INTEGER NOT NULL DEFAULT 1,
    scanned_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (git_project_id, branch, path)
);

CREATE TABLE IF NOT EXISTS point_content (
    id               INTEGER PRIMARY KEY,
    point_id         INTEGER NOT NULL REFERENCES point(id) ON DELETE CASCADE,
    file_id          INTEGER NOT NULL REFERENCES file_snapshot(id) ON DELETE CASCADE,
    line_no          INTEGER NOT NULL CHECK (line_no > 0),
    raw_marker       TEXT    NOT NULL,
    numbeartefact    TEXT    DEFAULT NULL,
    numbercase       TEXT    DEFAULT NULL,
    numberscenario   TEXT    DEFAULT NULL,
    numberstep       TEXT    DEFAULT NULL,
    created_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (point_id, file_id, line_no)
);

CREATE TABLE IF NOT EXISTS scan_run (
    id              INTEGER PRIMARY KEY,
    git_project_id  INTEGER NOT NULL REFERENCES git_project(id) ON DELETE CASCADE,
    started_at      TEXT    NOT NULL,
    finished_at     TEXT,
    status          TEXT    NOT NULL CHECK (status IN ('running', 'success', 'partial', 'failed')),
    branches        TEXT    NOT NULL DEFAULT '[]',
    files_scanned   INTEGER NOT NULL DEFAULT 0,
    files_skipped   INTEGER NOT NULL DEFAULT 0,
    markers_found   INTEGER NOT NULL DEFAULT 0,
    errors_count    INTEGER NOT NULL DEFAULT 0,
    message         TEXT
);

-- Статистика по каждому значению SCAN.INCLUDE в разрезе проекта.
CREATE TABLE IF NOT EXISTS include_stat (
    id              INTEGER PRIMARY KEY,
    git_project_id  INTEGER NOT NULL REFERENCES git_project(id) ON DELETE CASCADE,
    pattern         TEXT    NOT NULL,
    position        INTEGER NOT NULL CHECK (position >= 0),
    files_count     INTEGER NOT NULL DEFAULT 0 CHECK (files_count >= 0),
    markers_count   INTEGER NOT NULL DEFAULT 0 CHECK (markers_count >= 0),
    status          INTEGER NOT NULL DEFAULT 1 CHECK (status IN (0, 1)),
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (git_project_id, pattern)
);

CREATE INDEX IF NOT EXISTS idx_point_req
    ON point(req_id);

CREATE INDEX IF NOT EXISTS idx_point_content_point
    ON point_content(point_id);

CREATE INDEX IF NOT EXISTS idx_point_content_file
    ON point_content(file_id);

CREATE INDEX IF NOT EXISTS idx_file_snapshot_project_branch
    ON file_snapshot(git_project_id, branch);

CREATE INDEX IF NOT EXISTS idx_scan_run_project_started
    ON scan_run(git_project_id, started_at);

CREATE INDEX IF NOT EXISTS idx_include_stat_project
    ON include_stat(git_project_id, position);

-- Инвалидация экспорта: любое смысловое изменение сбрасывает is_export.
CREATE TRIGGER IF NOT EXISTS trg_req_is_export
AFTER UPDATE OF code, name, status ON req
FOR EACH ROW
WHEN NEW.code IS NOT OLD.code
  OR NEW.name IS NOT OLD.name
  OR NEW.status IS NOT OLD.status
BEGIN
    UPDATE req SET is_export = 0 WHERE id = NEW.id AND is_export = 1;
END;

CREATE TRIGGER IF NOT EXISTS trg_point_is_export
AFTER UPDATE OF point, status ON point
FOR EACH ROW
WHEN NEW.point IS NOT OLD.point
  OR NEW.status IS NOT OLD.status
BEGIN
    UPDATE point SET is_export = 0 WHERE id = NEW.id AND is_export = 1;
END;

CREATE TRIGGER IF NOT EXISTS trg_point_content_insert
AFTER INSERT ON point_content
FOR EACH ROW
BEGIN
    UPDATE point SET is_export = 0 WHERE id = NEW.point_id AND is_export = 1;
END;

CREATE TRIGGER IF NOT EXISTS trg_point_content_update
AFTER UPDATE OF point_id, line_no, raw_marker,
    numbeartefact, numbercase, numberscenario, numberstep ON point_content
FOR EACH ROW
BEGIN
    UPDATE point SET is_export = 0
    WHERE id IN (OLD.point_id, NEW.point_id) AND is_export = 1;
END;

CREATE TRIGGER IF NOT EXISTS trg_point_content_delete
AFTER DELETE ON point_content
FOR EACH ROW
BEGIN
    UPDATE point SET is_export = 0 WHERE id = OLD.point_id AND is_export = 1;
END;
"""

_REQUIRED_TABLES = {
    "schema_version",
    "git_project",
    "req",
    "point",
    "file_hash",
    "file_snapshot",
    "point_content",
    "scan_run",
    "include_stat",
}


class ScanDatabaseError(RuntimeError):
    """A database failure in ``scan`` mode."""


class ScanSchemaVersionError(ScanDatabaseError):
    """``scan.db`` has an incompatible or damaged schema."""


def now_iso() -> str:
    """Return an ISO 8601 UTC timestamp with millisecond precision and ``Z``."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def author_from_url(value: str, base_dir: Path | None = None) -> str | None:
    """Derive a project author (owner) from a remote URL, if possible."""
    normalized = normalize_url(value, base_dir)
    if is_local_path(normalized):
        return None
    parsed = urlsplit(normalized)
    segments = [part for part in parsed.path.split("/") if part]
    if len(segments) >= 2:
        return segments[0]
    return None


def _normalize_include_patterns(patterns: Iterable[str]) -> list[str]:
    """Strip, drop empty and duplicate ``SCAN.INCLUDE`` values, keep order."""
    ordered: list[str] = []
    seen: set[str] = set()
    for raw in patterns:
        pattern = str(raw).strip()
        if not pattern or pattern in seen:
            continue
        seen.add(pattern)
        ordered.append(pattern)
    return ordered


class ScanDatabase:
    """Manages ``scan.db`` (schema version 4)."""

    def __init__(self, db_path: Path | str) -> None:
        path = Path(db_path)
        if path.suffix == ".db":
            self.db_path = path
        else:
            self.db_path = path / "scan.db"

    # ------------------------------------------------------------- connection
    @contextmanager
    def connect(self, readonly: bool = False) -> Iterator[sqlite3.Connection]:
        if readonly:
            if not self.db_path.exists():
                raise ScanDatabaseError(f"database does not exist: {self.db_path}")
            uri = f"file:{self.db_path.as_posix()}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=5.0, isolation_level=None)
        else:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.db_path), timeout=5.0, isolation_level=None)

        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 5000")
            if not readonly:
                conn.execute("PRAGMA journal_mode = WAL")
            # Must be set before a transaction starts (section 5, step 0).
            conn.execute("PRAGMA synchronous = NORMAL")
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except Exception:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")

    # ------------------------------------------------------------ schema init
    def initialize(self, create: bool = True) -> bool:
        """Create the schema when missing and validate an existing database.

        Returns ``True`` when the database exists after the call. With
        ``create=False`` (``--dry-run``) a missing database is not created.
        """
        exists = self.db_path.exists() and self.db_path.stat().st_size > 0
        if not exists:
            if not create:
                return False
            try:
                with self.connect() as conn:
                    conn.executescript(SCAN_SCHEMA_SQL)
                    conn.execute(
                        "INSERT INTO schema_version(version) VALUES (?)",
                        (SCAN_SCHEMA_VERSION,),
                    )
            except sqlite3.Error as exc:
                raise ScanDatabaseError(f"cannot create {self.db_path}: {exc}") from exc
            if os.name != "nt":
                try:
                    self.db_path.chmod(0o600)
                except OSError:
                    pass
            logger.info("Scan database created at {}", self.db_path)
            return True

        self._validate_schema()
        return True

    def validate(self) -> None:
        """Validate an existing database without creating anything."""
        if not self.db_path.exists() or self.db_path.stat().st_size == 0:
            return
        self._validate_schema()

    def _validate_schema(self) -> None:
        try:
            with self.connect(readonly=True) as conn:
                tables = _table_names(conn)
                if "schema_version" not in tables:
                    raise ScanSchemaVersionError(
                        f"{self.db_path}: schema_version table is missing"
                    )
                missing = _REQUIRED_TABLES - tables
                if missing:
                    raise ScanSchemaVersionError(
                        f"{self.db_path}: missing table(s): {', '.join(sorted(missing))}"
                    )
                version = _read_version(conn)
                if version != SCAN_SCHEMA_VERSION:
                    raise ScanSchemaVersionError(
                        f"{self.db_path}: schema version {version} is not supported "
                        f"(expected {SCAN_SCHEMA_VERSION}); "
                        "delete the database and re-run scan"
                    )
        except ScanSchemaVersionError:
            raise
        except sqlite3.Error as exc:
            raise ScanSchemaVersionError(
                f"{self.db_path}: cannot read schema: {exc}"
            ) from exc

    def schema_version(self) -> int:
        with self.connect(readonly=True) as conn:
            return _read_version(conn)

    # ---------------------------------------------------------------- projects
    def ensure_project(
        self, conn: sqlite3.Connection, url: str, base_dir: Path | None = None
    ) -> int:
        display = normalize_url(url, base_dir)
        key = url_key(url, base_dir)
        author = author_from_url(url, base_dir)
        conn.execute(
            """
            INSERT INTO git_project (url, url_key, author, status, updated_at)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(url_key) DO UPDATE SET
                url = excluded.url,
                status = 1,
                author = COALESCE(excluded.author, git_project.author),
                updated_at = excluded.updated_at
            """,
            (display, key, author, now_iso()),
        )
        row = conn.execute(
            "SELECT id FROM git_project WHERE url_key = ?", (key,)
        ).fetchone()
        if row is None:  # pragma: no cover - defensive
            raise ScanDatabaseError(f"failed to register project {display}")
        return int(row["id"])

    def get_project_id(self, url: str, base_dir: Path | None = None) -> int | None:
        key = url_key(url, base_dir)
        with self.connect(readonly=True) as conn:
            row = conn.execute(
                "SELECT id FROM git_project WHERE url_key = ?", (key,)
            ).fetchone()
        return int(row["id"]) if row else None

    # ------------------------------------------------------------ include stat
    def sync_include_patterns(
        self, conn: sqlite3.Connection, patterns: Iterable[str]
    ) -> None:
        """Synchronize ``include_stat`` with the current ``SCAN.INCLUDE``.

        Missing patterns are inserted with zero counters and ``status = 1``;
        patterns absent from the configuration are archived (``status = 0``)
        for every project. Reactivating an archived pattern resets its counters
        to zero until the next reconciliation.
        """
        ordered = _normalize_include_patterns(patterns)
        projects = conn.execute("SELECT id FROM git_project ORDER BY id").fetchall()
        for project in projects:
            self._sync_project_include_patterns(
                conn, int(project["id"]), ordered, deactivate=True
            )

    def ensure_include_patterns(
        self, conn: sqlite3.Connection, project_id: int, patterns: Iterable[str]
    ) -> None:
        """Ensure pattern rows exist for one project (new project mid-run)."""
        self._sync_project_include_patterns(
            conn,
            project_id,
            _normalize_include_patterns(patterns),
            deactivate=True,
        )

    def _sync_project_include_patterns(
        self,
        conn: sqlite3.Connection,
        project_id: int,
        patterns: Sequence[str],
        *,
        deactivate: bool,
    ) -> None:
        now = now_iso()
        active = set(patterns)
        for position, pattern in enumerate(patterns):
            row = conn.execute(
                "SELECT id, position, status FROM include_stat "
                "WHERE git_project_id = ? AND pattern = ?",
                (project_id, pattern),
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO include_stat "
                    "(git_project_id, pattern, position, files_count, "
                    "markers_count, status, updated_at) "
                    "VALUES (?, ?, ?, 0, 0, 1, ?)",
                    (project_id, pattern, position, now),
                )
                continue
            if row["status"] == 0:
                conn.execute(
                    "UPDATE include_stat SET status = 1, position = ?, "
                    "files_count = 0, markers_count = 0, updated_at = ? "
                    "WHERE id = ?",
                    (position, now, row["id"]),
                )
            elif int(row["position"]) != position:
                conn.execute(
                    "UPDATE include_stat SET position = ?, updated_at = ? WHERE id = ?",
                    (position, now, row["id"]),
                )

        if not deactivate:
            return
        rows = conn.execute(
            "SELECT id, pattern FROM include_stat "
            "WHERE git_project_id = ? AND status = 1",
            (project_id,),
        ).fetchall()
        for row in rows:
            if row["pattern"] not in active:
                conn.execute(
                    "UPDATE include_stat SET status = 0, updated_at = ? WHERE id = ?",
                    (now, row["id"]),
                )

    def recompute_include_stats(
        self, conn: sqlite3.Connection, project_id: int
    ) -> None:
        """Recount files and markers for every active pattern of a project.

        Counters are taken from ``file_snapshot`` (only successfully scanned
        files) aggregated over all branches. A file matching several patterns
        is counted in each of them. Marker counters are the number of
        ``point_content`` rows attached to the counted snapshots.
        """
        patterns = conn.execute(
            "SELECT id, pattern FROM include_stat "
            "WHERE git_project_id = ? AND status = 1 "
            "ORDER BY position, id",
            (project_id,),
        ).fetchall()
        if not patterns:
            return

        counts: dict[int, list[int]] = {
            int(row["id"]): [0, 0] for row in patterns
        }
        files = conn.execute(
            """
            SELECT fs.path AS path, COUNT(pc.id) AS marker_count
            FROM file_snapshot fs
            LEFT JOIN point_content pc ON pc.file_id = fs.id
            WHERE fs.git_project_id = ?
            GROUP BY fs.id
            """,
            (project_id,),
        ).fetchall()
        for file in files:
            path = str(file["path"])
            markers = int(file["marker_count"] or 0)
            for row in patterns:
                if not path_matches(path, row["pattern"]):
                    continue
                counts[int(row["id"])][0] += 1
                counts[int(row["id"])][1] += markers

        now = now_iso()
        for pattern_id, (files_count, markers_count) in counts.items():
            conn.execute(
                "UPDATE include_stat SET files_count = ?, markers_count = ?, "
                "updated_at = ? WHERE id = ? "
                "AND (files_count <> ? OR markers_count <> ?)",
                (
                    files_count,
                    markers_count,
                    now,
                    pattern_id,
                    files_count,
                    markers_count,
                ),
            )

    def include_stats(
        self, project_keys: Sequence[str] | None = None
    ) -> list[dict[str, Any]]:
        """Return active per-pattern statistics for the requirements report."""
        query = """
            SELECT gp.url        AS project_url,
                   gp.url_key    AS url_key,
                   gp.author     AS author,
                   ist.pattern   AS pattern,
                   ist.position  AS position,
                   ist.files_count   AS files_count,
                   ist.markers_count AS markers_count
            FROM include_stat ist
            JOIN git_project gp ON gp.id = ist.git_project_id
            WHERE ist.status = 1
        """
        params: list[Any] = []
        if project_keys:
            placeholders = ",".join("?" for _ in project_keys)
            query += f" AND gp.url_key IN ({placeholders})"
            params.extend(project_keys)
        query += " ORDER BY gp.url, ist.position, ist.id"
        with self.connect(readonly=True) as conn:
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    # -------------------------------------------------------------- scan runs
    def resolve_active_run(
        self, conn: sqlite3.Connection, project_id: int, force: bool = False
    ) -> sqlite3.Row | None:
        """Return a blocking ``running`` run, or close a stale one.

        A run younger than 24 hours blocks a new scan unless ``force`` is set.
        Older runs are marked ``failed`` and do not block.
        """
        row = conn.execute(
            "SELECT * FROM scan_run WHERE git_project_id = ? AND status = 'running' "
            "ORDER BY started_at DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        if row is None:
            return None

        if force:
            conn.execute(
                "UPDATE scan_run SET status = 'failed', finished_at = ?, "
                "message = ? WHERE id = ?",
                (now_iso(), "superseded by --force", row["id"]),
            )
            logger.warning("Marked active run {} as failed (--force)", row["id"])
            return None

        started = _parse_iso(row["started_at"])
        if started is not None and datetime.now(timezone.utc) - started < RUNNING_RUN_TTL:
            return row

        conn.execute(
            "UPDATE scan_run SET status = 'failed', finished_at = ?, "
            "message = ? WHERE id = ?",
            (now_iso(), "stale running run", row["id"]),
        )
        logger.warning("Marked stale run {} as failed", row["id"])
        return None

    def create_run(self, conn: sqlite3.Connection, project_id: int) -> int:
        cursor = conn.execute(
            "INSERT INTO scan_run (git_project_id, started_at, status) VALUES (?, ?, 'running')",
            (project_id, now_iso()),
        )
        return int(cursor.lastrowid)

    def finish_run(
        self,
        conn: sqlite3.Connection,
        run_id: int,
        *,
        status: str,
        branches: Sequence[str] = (),
        files_scanned: int = 0,
        files_skipped: int = 0,
        markers_found: int = 0,
        errors_count: int = 0,
        message: str | None = None,
    ) -> None:
        import json

        conn.execute(
            """
            UPDATE scan_run SET
                finished_at = ?, status = ?, branches = ?, files_scanned = ?,
                files_skipped = ?, markers_found = ?, errors_count = ?, message = ?
            WHERE id = ?
            """,
            (
                now_iso(),
                status,
                json.dumps(list(branches), ensure_ascii=False),
                files_scanned,
                files_skipped,
                markers_found,
                errors_count,
                message,
                run_id,
            ),
        )

    def fail_run(self, run_id: int, message: str) -> None:
        """Best-effort update of a run after a fatal project error."""
        try:
            with self.transaction() as conn:
                conn.execute(
                    "UPDATE scan_run SET status = 'failed', finished_at = ?, message = ? "
                    "WHERE id = ?",
                    (now_iso(), message, run_id),
                )
        except sqlite3.Error:
            logger.exception("Cannot mark scan_run {} as failed", run_id)

    def latest_run(self, project_id: int) -> dict[str, Any] | None:
        with self.connect(readonly=True) as conn:
            row = conn.execute(
                "SELECT * FROM scan_run WHERE git_project_id = ? "
                "ORDER BY started_at DESC, id DESC LIMIT 1",
                (project_id,),
            ).fetchone()
        return dict(row) if row else None

    # --------------------------------------------------------------- snapshots
    def get_snapshots(
        self, project_id: int, branch: str
    ) -> dict[str, dict[str, Any]]:
        with self.connect(readonly=True) as conn:
            rows = conn.execute(
                """
                SELECT fs.*, (
                    SELECT COUNT(*) FROM point_content pc WHERE pc.file_id = fs.id
                ) AS marker_count
                FROM file_snapshot fs
                WHERE fs.git_project_id = ? AND fs.branch = ?
                """,
                (project_id, branch),
            ).fetchall()
        return {row["path"]: dict(row) for row in rows}

    def upsert_file_hash(
        self, conn: sqlite3.Connection, blob_hash: str, size_bytes: int
    ) -> None:
        conn.execute(
            "INSERT INTO file_hash (hash, size_bytes) VALUES (?, ?) "
            "ON CONFLICT(hash) DO NOTHING",
            (blob_hash, size_bytes),
        )

    def upsert_snapshot(
        self,
        conn: sqlite3.Connection,
        *,
        project_id: int,
        branch: str,
        path: str,
        blob_hash: str,
        commit_sha: str | None,
        author: str | None,
        committer: str | None,
        encoding: str,
        parser_version: int,
    ) -> int:
        conn.execute(
            """
            INSERT INTO file_snapshot (
                git_project_id, branch, path, commit_sha, author, committer,
                blob_hash, encoding, parser_version, scanned_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(git_project_id, branch, path) DO UPDATE SET
                commit_sha = excluded.commit_sha,
                author = excluded.author,
                committer = excluded.committer,
                blob_hash = excluded.blob_hash,
                encoding = excluded.encoding,
                parser_version = excluded.parser_version,
                scanned_at = excluded.scanned_at
            """,
            (
                project_id,
                branch,
                path,
                commit_sha,
                author,
                committer,
                blob_hash,
                encoding,
                parser_version,
                now_iso(),
            ),
        )
        row = conn.execute(
            "SELECT id FROM file_snapshot WHERE git_project_id = ? AND branch = ? AND path = ?",
            (project_id, branch, path),
        ).fetchone()
        if row is None:  # pragma: no cover - defensive
            raise ScanDatabaseError(f"failed to store snapshot {branch}:{path}")
        return int(row["id"])

    # ------------------------------------------------------------------- reqs
    def upsert_req(
        self,
        conn: sqlite3.Connection,
        code: str,
        name: str | None,
    ) -> tuple[int, bool, str | None, str | None]:
        """Insert or update a requirement (unique across all projects).

        Returns ``(req_id, created, previous_name, new_name)``; the last two are
        only set when a non-empty name replaced a different value.
        """
        row = conn.execute(
            "SELECT id, name FROM req WHERE code = ?",
            (code,),
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO req (code, name, status, updated_at) "
                "VALUES (?, ?, 1, ?)",
                (
                    code,
                    name or "Требование без наименования",
                    now_iso(),
                ),
            )
            new_row = conn.execute(
                "SELECT id FROM req WHERE code = ?",
                (code,),
            ).fetchone()
            return int(new_row["id"]), True, None, None

        req_id = int(row["id"])
        conflict: tuple[str, str] | None = None
        if name and name != row["name"]:
            conflict = (row["name"], name)
            conn.execute(
                "UPDATE req SET name = ?, status = 1, updated_at = ? WHERE id = ?",
                (name, now_iso(), req_id),
            )
        else:
            conn.execute(
                "UPDATE req SET status = 1, updated_at = ? WHERE id = ?",
                (now_iso(), req_id),
            )
        return req_id, False, *(conflict or (None, None))

    def upsert_point(
        self, conn: sqlite3.Connection, req_id: int, point: str
    ) -> tuple[int, bool]:
        row = conn.execute(
            "SELECT id FROM point WHERE req_id = ? AND point = ?", (req_id, point)
        ).fetchone()
        if row is not None:
            conn.execute(
                "UPDATE point SET status = 1, updated_at = ? WHERE id = ?",
                (now_iso(), row["id"]),
            )
            return int(row["id"]), False

        conn.execute(
            "INSERT INTO point (req_id, point, status, updated_at) VALUES (?, ?, 1, ?)",
            (req_id, point, now_iso()),
        )
        new_row = conn.execute(
            "SELECT id FROM point WHERE req_id = ? AND point = ?", (req_id, point)
        ).fetchone()
        return int(new_row["id"]), True

    def replace_occurrences(
        self,
        conn: sqlite3.Connection,
        file_id: int,
        occurrences: Iterable[tuple[int, int, str]],
    ) -> int:
        """Synchronize the marker occurrences of a file.

        Only real changes are written (inserts, marker updates, deletions), so
        re-parsing a file whose markers did not change keeps the number fields
        and does not reset ``point.is_export`` (schema triggers handle the reset
        on actual changes). Returns the number of newly inserted rows.
        """
        desired: dict[tuple[int, int], str] = {}
        for point_id, line_no, raw_marker in occurrences:
            desired.setdefault((point_id, line_no), raw_marker)

        existing = {
            (row["point_id"], row["line_no"]): row
            for row in conn.execute(
                "SELECT id, point_id, line_no, raw_marker "
                "FROM point_content WHERE file_id = ?",
                (file_id,),
            ).fetchall()
        }

        for key, row in existing.items():
            if key not in desired:
                conn.execute("DELETE FROM point_content WHERE id = ?", (row["id"],))

        inserted = 0
        for (point_id, line_no), raw_marker in desired.items():
            row = existing.get((point_id, line_no))
            if row is None:
                conn.execute(
                    "INSERT INTO point_content "
                    "(point_id, file_id, line_no, raw_marker) VALUES (?, ?, ?, ?)",
                    (point_id, file_id, line_no, raw_marker),
                )
                inserted += 1
            elif row["raw_marker"] != raw_marker:
                conn.execute(
                    "UPDATE point_content SET raw_marker = ? WHERE id = ?",
                    (raw_marker, row["id"]),
                )
        return inserted

    # --------------------------------------------------------- reconciliation
    def reconcile_branch(
        self,
        conn: sqlite3.Connection,
        project_id: int,
        branch: str,
        keep_paths: Iterable[str],
    ) -> int:
        """Delete snapshots of a branch whose paths are no longer scanned."""
        keep = set(keep_paths)
        rows = conn.execute(
            "SELECT id, path FROM file_snapshot WHERE git_project_id = ? AND branch = ?",
            (project_id, branch),
        ).fetchall()
        stale = [(row["id"],) for row in rows if row["path"] not in keep]
        if stale:
            conn.executemany("DELETE FROM file_snapshot WHERE id = ?", stale)
        return len(stale)

    def delete_missing_branches(
        self, conn: sqlite3.Connection, project_id: int, branches: Iterable[str]
    ) -> int:
        keep = set(branches)
        rows = conn.execute(
            "SELECT DISTINCT branch FROM file_snapshot WHERE git_project_id = ?",
            (project_id,),
        ).fetchall()
        removed = 0
        for row in rows:
            branch = row["branch"]
            if branch in keep:
                continue
            cursor = conn.execute(
                "DELETE FROM file_snapshot WHERE git_project_id = ? AND branch = ?",
                (project_id, branch),
            )
            removed += cursor.rowcount
        return removed

    def recompute_statuses(
        self, conn: sqlite3.Connection
    ) -> tuple[int, int]:
        """Archive points/requirements without occurrences (globally).

        Requirements are shared by all projects, so statuses are computed over
        the whole database: an occurrence in any project keeps a requirement
        and its section active.

        Returns ``(points_archived, reqs_archived)``.
        """
        archived_points = conn.execute(
            "SELECT COUNT(*) FROM point p WHERE p.status = 1 "
            "AND NOT EXISTS (SELECT 1 FROM point_content pc WHERE pc.point_id = p.id)"
        ).fetchone()[0]

        conn.execute(
            "UPDATE point SET status = 0, updated_at = ? "
            "WHERE status = 1 "
            "AND NOT EXISTS (SELECT 1 FROM point_content pc WHERE pc.point_id = point.id)",
            (now_iso(),),
        )
        conn.execute(
            "UPDATE point SET status = 1, updated_at = ? "
            "WHERE status = 0 "
            "AND EXISTS (SELECT 1 FROM point_content pc WHERE pc.point_id = point.id)",
            (now_iso(),),
        )

        archived_reqs = conn.execute(
            "SELECT COUNT(*) FROM req r WHERE r.status = 1 "
            "AND NOT EXISTS (SELECT 1 FROM point p WHERE p.req_id = r.id AND p.status = 1)"
        ).fetchone()[0]
        conn.execute(
            "UPDATE req SET status = 1, updated_at = ? "
            "WHERE status = 0 "
            "AND EXISTS (SELECT 1 FROM point p WHERE p.req_id = req.id AND p.status = 1)",
            (now_iso(),),
        )
        conn.execute(
            "UPDATE req SET status = 0, updated_at = ? "
            "WHERE status = 1 "
            "AND NOT EXISTS (SELECT 1 FROM point p WHERE p.req_id = req.id AND p.status = 1)",
            (now_iso(),),
        )
        return int(archived_points), int(archived_reqs)

    def gc_hashes(self, conn: sqlite3.Connection) -> int:
        cursor = conn.execute(
            "DELETE FROM file_hash WHERE hash NOT IN (SELECT blob_hash FROM file_snapshot)"
        )
        return cursor.rowcount

    # ---------------------------------------------------------------- reporting
    def count(self, table: str) -> int:
        if table not in _REQUIRED_TABLES:
            raise ValueError(f"unknown table: {table}")
        with self.connect(readonly=True) as conn:
            return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def occurrences(self, project_url: str | None = None) -> list[dict[str, Any]]:
        query = """
            SELECT gp.url, r.code, r.name, p.point, p.status AS point_status,
                   fs.branch, fs.path, pc.line_no, pc.raw_marker
            FROM point_content pc
            JOIN point p ON p.id = pc.point_id
            JOIN req r ON r.id = p.req_id
            JOIN file_snapshot fs ON fs.id = pc.file_id
            JOIN git_project gp ON gp.id = fs.git_project_id
        """
        params: list[Any] = []
        if project_url is not None:
            query += " WHERE gp.url = ?"
            params.append(project_url)
        query += " ORDER BY gp.url, r.code, p.point, fs.branch, fs.path, pc.line_no"
        with self.connect(readonly=True) as conn:
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    def requirement_report(
        self, project_keys: Sequence[str] | None = None
    ) -> list[dict[str, Any]]:
        """Return active requirement occurrences for the HTML report.

        Every row is one marker occurrence: requirement, its section, and the
        project/branch/commit/file where the marker currently lives.
        """
        query = """
            SELECT gp.url            AS project_url,
                   r.code            AS code,
                   r.name            AS name,
                   p.point           AS point,
                   fs.branch         AS branch,
                   fs.commit_sha     AS commit_sha,
                   fs.path           AS path,
                   fs.committer      AS committer,
                   pc.line_no        AS line_no,
                   pc.raw_marker     AS raw_marker
            FROM point_content pc
            JOIN point p ON p.id = pc.point_id
            JOIN req r ON r.id = p.req_id
            JOIN file_snapshot fs ON fs.id = pc.file_id
            JOIN git_project gp ON gp.id = fs.git_project_id
            WHERE r.status = 1 AND p.status = 1
        """
        params: list[Any] = []
        if project_keys:
            placeholders = ",".join("?" for _ in project_keys)
            query += f" AND gp.url_key IN ({placeholders})"
            params.extend(project_keys)
        query += (
            " ORDER BY r.code, p.point, gp.url, fs.branch, fs.path, pc.line_no"
        )
        with self.connect(readonly=True) as conn:
            return [dict(row) for row in conn.execute(query, params).fetchall()]


# --------------------------------------------------------------------------
def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    return {row[0] for row in rows}


def _read_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute(
            "SELECT version FROM schema_version ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
    except sqlite3.Error as exc:
        raise ScanSchemaVersionError(f"cannot read schema_version: {exc}") from exc
    if row is None:
        raise ScanSchemaVersionError("schema_version is empty")
    return int(row[0])


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
