"""Tests for ``scan.db`` (schema version 4)."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from scan_git_push.scan.database import (
    SCAN_SCHEMA_VERSION,
    ScanDatabase,
    ScanSchemaVersionError,
    now_iso,
)


@pytest.fixture
def scan_db(tmp_path: Path) -> ScanDatabase:
    db = ScanDatabase(tmp_path / "db" / "scan.db")
    db.initialize()
    return db


def _hash(char: str) -> str:
    return char * 64


def _insert_hash(conn: sqlite3.Connection, value: str, size: int = 10) -> None:
    conn.execute(
        "INSERT INTO file_hash (hash, size_bytes) VALUES (?, ?)", (value, size)
    )


def _insert_project(conn: sqlite3.Connection, db: ScanDatabase) -> int:
    return db.ensure_project(conn, "https://github.com/owner/repo")


def _path_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class TestSchema:
    def test_creates_current_version(self, scan_db: ScanDatabase) -> None:
        assert scan_db.schema_version() == SCAN_SCHEMA_VERSION
        assert SCAN_SCHEMA_VERSION == 4
        with scan_db.connect() as conn:
            names = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        assert {
            "schema_version",
            "git_project",
            "req",
            "point",
            "file_hash",
            "file_snapshot",
            "point_content",
            "scan_run",
            "include_stat",
        } <= names

    def test_pragmas(self, scan_db: ScanDatabase) -> None:
        with scan_db.connect() as conn:
            assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
            assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1
            assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000

    def test_file_hash_has_no_content_column(self, scan_db: ScanDatabase) -> None:
        with scan_db.connect() as conn:
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(file_hash)")
            }
        assert columns == {"hash", "size_bytes", "created_at"}

    def test_export_and_number_columns(self, scan_db: ScanDatabase) -> None:
        with scan_db.connect() as conn:
            req_columns = {
                row[1]: row for row in conn.execute("PRAGMA table_info(req)")
            }
            point_columns = {
                row[1]: row for row in conn.execute("PRAGMA table_info(point)")
            }
            content_columns = {
                row[1]: row for row in conn.execute("PRAGMA table_info(point_content)")
            }

        assert req_columns["is_export"]["dflt_value"] == "0"
        assert req_columns["is_export"]["notnull"] == 1
        assert point_columns["is_export"]["dflt_value"] == "0"
        assert point_columns["is_export"]["notnull"] == 1
        for name in (
            "numbeartefact",
            "numbercase",
            "numberscenario",
            "numberstep",
        ):
            assert name in content_columns
            assert content_columns[name]["notnull"] == 0

    def test_reopen_existing_database(self, tmp_path: Path) -> None:
        first = ScanDatabase(tmp_path / "scan.db")
        first.initialize()
        second = ScanDatabase(tmp_path / "scan.db")
        second.initialize()
        assert second.schema_version() == SCAN_SCHEMA_VERSION

    def test_missing_schema_version_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "scan.db"
        conn = sqlite3.connect(str(path))
        conn.execute("CREATE TABLE something (id INTEGER)")
        conn.commit()
        conn.close()

        with pytest.raises(ScanSchemaVersionError):
            ScanDatabase(path).initialize()

    @pytest.mark.parametrize("version", [0, 1, 2, 3, 99])
    def test_incompatible_version_raises(self, tmp_path: Path, version: int) -> None:
        path = tmp_path / "scan.db"
        db = ScanDatabase(path)
        db.initialize()
        with db.transaction() as conn:
            conn.execute("UPDATE schema_version SET version = ?", (version,))
        with pytest.raises(ScanSchemaVersionError):
            ScanDatabase(path).initialize()

    def test_empty_schema_version_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "scan.db"
        db = ScanDatabase(path)
        db.initialize()
        with db.transaction() as conn:
            conn.execute("DELETE FROM schema_version")
        with pytest.raises(ScanSchemaVersionError):
            ScanDatabase(path).initialize()

    def test_empty_file_is_created(self, tmp_path: Path) -> None:
        path = tmp_path / "scan.db"
        path.write_bytes(b"")
        db = ScanDatabase(path)
        assert db.initialize(create=True) is True
        assert db.schema_version() == SCAN_SCHEMA_VERSION

    def test_dry_run_does_not_create(self, tmp_path: Path) -> None:
        path = tmp_path / "scan.db"
        db = ScanDatabase(path)
        assert db.initialize(create=False) is False
        assert not path.exists()


class TestCascades:
    def _populate(self, scan_db: ScanDatabase) -> int:
        with scan_db.transaction() as conn:
            project_id = _insert_project(conn, scan_db)
            _insert_hash(conn, _hash("a"))
            req_id, _, _, _ = scan_db.upsert_req(conn, "REQ_1", "Name")
            point_id, _ = scan_db.upsert_point(conn, req_id, "1.1")
            file_id = scan_db.upsert_snapshot(
                conn,
                project_id=project_id,
                branch="main",
                path="docs/req.md",
                blob_hash=_hash("a"),
                commit_sha="abc",
                author="author",
                committer="committer",
                encoding="utf-8",
                parser_version=1,
            )
            scan_db.replace_occurrences(conn, file_id, [(point_id, 3, "raw")])
            scan_db.create_run(conn, project_id)
        return project_id

    def test_delete_project_removes_project_data(self, scan_db: ScanDatabase) -> None:
        project_id = self._populate(scan_db)
        with scan_db.transaction() as conn:
            conn.execute("DELETE FROM git_project WHERE id = ?", (project_id,))
            for table in ("point_content", "file_snapshot", "scan_run"):
                assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
            # Requirements are global and are archived by reconciliation, not
            # deleted together with a project.
            assert conn.execute("SELECT COUNT(*) FROM req").fetchone()[0] == 1

    def test_delete_snapshot_removes_occurrences(self, scan_db: ScanDatabase) -> None:
        self._populate(scan_db)
        with scan_db.transaction() as conn:
            conn.execute("DELETE FROM file_snapshot")
            assert conn.execute("SELECT COUNT(*) FROM point_content").fetchone()[0] == 0
            # Requirements and points are archived by reconciliation, not deleted.
            assert conn.execute("SELECT COUNT(*) FROM req").fetchone()[0] == 1


class TestIncludeStat:
    def test_sync_creates_rows_for_all_projects(self, scan_db: ScanDatabase) -> None:
        with scan_db.transaction() as conn:
            first = _insert_project(conn, scan_db)
            second = scan_db.ensure_project(conn, "https://github.com/owner/other")
            scan_db.sync_include_patterns(conn, ["**/*.txt", "**/*.md"])

        with scan_db.connect(readonly=True) as conn:
            rows = conn.execute(
                "SELECT git_project_id, pattern, position, files_count, "
                "markers_count, status FROM include_stat "
                "ORDER BY git_project_id, position"
            ).fetchall()
        assert len(rows) == 4
        for project_id in (first, second):
            project_rows = [
                row for row in rows if row["git_project_id"] == project_id
            ]
            assert [row["pattern"] for row in project_rows] == [
                "**/*.txt",
                "**/*.md",
            ]
            assert [row["position"] for row in project_rows] == [0, 1]
            assert all(
                row["files_count"] == 0
                and row["markers_count"] == 0
                and row["status"] == 1
                for row in project_rows
            )

    def test_sync_is_idempotent_and_preserves_counts(
        self, scan_db: ScanDatabase
    ) -> None:
        with scan_db.transaction() as conn:
            _insert_project(conn, scan_db)
            scan_db.sync_include_patterns(conn, ["a", "b"])
            conn.execute(
                "UPDATE include_stat SET files_count = 3, markers_count = 4 "
                "WHERE pattern = 'a'"
            )
            scan_db.sync_include_patterns(conn, ["a", "b"])
            count = conn.execute("SELECT COUNT(*) FROM include_stat").fetchone()[0]
            row = conn.execute(
                "SELECT files_count, markers_count FROM include_stat "
                "WHERE pattern = 'a'"
            ).fetchone()
        assert count == 2
        assert (row["files_count"], row["markers_count"]) == (3, 4)

    def test_removed_pattern_is_archived_and_reactivated(
        self, scan_db: ScanDatabase
    ) -> None:
        with scan_db.transaction() as conn:
            _insert_project(conn, scan_db)
            scan_db.sync_include_patterns(conn, ["a", "b"])
            conn.execute(
                "UPDATE include_stat SET files_count = 5, markers_count = 6 "
                "WHERE pattern = 'b'"
            )
            scan_db.sync_include_patterns(conn, ["a"])
            archived = conn.execute(
                "SELECT status FROM include_stat WHERE pattern = 'b'"
            ).fetchone()
            scan_db.sync_include_patterns(conn, ["a", "b"])
            active = conn.execute(
                "SELECT status, position, files_count, markers_count "
                "FROM include_stat WHERE pattern = 'b'"
            ).fetchone()
        assert archived["status"] == 0
        assert active["status"] == 1
        assert active["position"] == 1
        assert (active["files_count"], active["markers_count"]) == (0, 0)

    def test_delete_project_removes_include_stat(
        self, scan_db: ScanDatabase
    ) -> None:
        with scan_db.transaction() as conn:
            project_id = _insert_project(conn, scan_db)
            scan_db.sync_include_patterns(conn, ["a"])
            conn.execute("DELETE FROM git_project WHERE id = ?", (project_id,))
            assert conn.execute("SELECT COUNT(*) FROM include_stat").fetchone()[0] == 0

    def test_recompute_counts_files_and_markers(
        self, scan_db: ScanDatabase
    ) -> None:
        with scan_db.transaction() as conn:
            project_id = _insert_project(conn, scan_db)
            req_id, _, _, _ = scan_db.upsert_req(conn, "REQ_1", None)
            point_id, _ = scan_db.upsert_point(conn, req_id, "1.1")
            files = {
                ("main", "docs/a.md"): 2,
                ("main", "docs/b.txt"): 1,
                ("main", "src/c.py"): 1,
                ("feature", "docs/a.md"): 1,
            }
            for (branch, path), markers in files.items():
                blob_hash = _path_hash(f"{branch}:{path}")
                scan_db.upsert_file_hash(conn, blob_hash, 10)
                file_id = scan_db.upsert_snapshot(
                    conn,
                    project_id=project_id,
                    branch=branch,
                    path=path,
                    blob_hash=blob_hash,
                    commit_sha="abc",
                    author="author",
                    committer="committer",
                    encoding="utf-8",
                    parser_version=2,
                )
                scan_db.replace_occurrences(
                    conn,
                    file_id,
                    [(point_id, line, "raw") for line in range(1, markers + 1)],
                )
            scan_db.sync_include_patterns(
                conn, ["**/*.md", "**/*.txt", "docs/**"]
            )
            scan_db.recompute_include_stats(conn, project_id)

        with scan_db.connect(readonly=True) as conn:
            rows = {
                row["pattern"]: row
                for row in conn.execute(
                    "SELECT pattern, files_count, markers_count FROM include_stat"
                )
            }
        assert (rows["**/*.md"]["files_count"], rows["**/*.md"]["markers_count"]) == (
            2,
            3,
        )
        assert (rows["**/*.txt"]["files_count"], rows["**/*.txt"]["markers_count"]) == (
            1,
            1,
        )
        assert (rows["docs/**"]["files_count"], rows["docs/**"]["markers_count"]) == (
            3,
            4,
        )


class TestConstraints:
    def test_same_code_is_shared_across_projects(self, scan_db: ScanDatabase) -> None:
        with scan_db.transaction() as conn:
            _insert_project(conn, scan_db)
            scan_db.ensure_project(conn, "https://github.com/owner/other")
            first, created, _, _ = scan_db.upsert_req(conn, "REQ_1", "one")
            second, created_again, old, new = scan_db.upsert_req(
                conn, "REQ_1", "two"
            )
            assert first == second
            assert created is True
            assert created_again is False
            assert (old, new) == ("one", "two")
            assert conn.execute("SELECT COUNT(*) FROM req").fetchone()[0] == 1
            assert conn.execute("SELECT name FROM req").fetchone()[0] == "two"

    def test_unique_point_per_req(self, scan_db: ScanDatabase) -> None:
        with scan_db.transaction() as conn:
            project_id = _insert_project(conn, scan_db)
            req_id, _, _, _ = scan_db.upsert_req(conn, "REQ_1", None)
            first, created = scan_db.upsert_point(conn, req_id, "1.1")
            second, created_again = scan_db.upsert_point(conn, req_id, "1.1")
            assert first == second
            assert created is True
            assert created_again is False
            assert conn.execute("SELECT COUNT(*) FROM point").fetchone()[0] == 1

    def test_same_point_content_on_different_lines(self, scan_db: ScanDatabase) -> None:
        with scan_db.transaction() as conn:
            project_id = _insert_project(conn, scan_db)
            _insert_hash(conn, _hash("b"))
            req_id, _, _, _ = scan_db.upsert_req(conn, "REQ_1", None)
            point_id, _ = scan_db.upsert_point(conn, req_id, "1.1")
            file_id = scan_db.upsert_snapshot(
                conn,
                project_id=project_id,
                branch="main",
                path="a.md",
                blob_hash=_hash("b"),
                commit_sha=None,
                author=None,
                committer=None,
                encoding="utf-8",
                parser_version=1,
            )
            count = scan_db.replace_occurrences(
                conn, file_id, [(point_id, 1, "raw"), (point_id, 2, "raw")]
            )
            assert count == 2

    def test_unique_point_content_same_line(self, scan_db: ScanDatabase) -> None:
        with scan_db.transaction() as conn:
            project_id = _insert_project(conn, scan_db)
            _insert_hash(conn, _hash("c"))
            req_id, _, _, _ = scan_db.upsert_req(conn, "REQ_1", None)
            point_id, _ = scan_db.upsert_point(conn, req_id, "1.1")
            file_id = scan_db.upsert_snapshot(
                conn,
                project_id=project_id,
                branch="main",
                path="a.md",
                blob_hash=_hash("c"),
                commit_sha=None,
                author=None,
                committer=None,
                encoding="utf-8",
                parser_version=1,
            )
            count = scan_db.replace_occurrences(
                conn, file_id, [(point_id, 1, "raw"), (point_id, 1, "raw")]
            )
            assert count == 1
            assert conn.execute("SELECT COUNT(*) FROM point_content").fetchone()[0] == 1

    @pytest.mark.parametrize("status", [-1, 2])
    def test_status_check(self, scan_db: ScanDatabase, status: int) -> None:
        with scan_db.transaction() as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO req (code, status) VALUES ('X', ?)",
                    (status,),
                )


class TestUpserts:
    def test_req_name_conflict_updates_name(self, scan_db: ScanDatabase) -> None:
        with scan_db.transaction() as conn:
            project_id = _insert_project(conn, scan_db)
            _, created, _, _ = scan_db.upsert_req(conn, "REQ_1", "first")
            req_id, created_again, old, new = scan_db.upsert_req(
                conn, "REQ_1", "second"
            )
            assert created is True
            assert created_again is False
            assert (old, new) == ("first", "second")
            row = conn.execute("SELECT name FROM req WHERE id = ?", (req_id,)).fetchone()
            assert row["name"] == "second"

    def test_empty_name_keeps_existing(self, scan_db: ScanDatabase) -> None:
        with scan_db.transaction() as conn:
            project_id = _insert_project(conn, scan_db)
            scan_db.upsert_req(conn, "REQ_1", "first")
            req_id, _, old, new = scan_db.upsert_req(conn, "REQ_1", None)
            assert (old, new) == (None, None)
            row = conn.execute("SELECT name FROM req WHERE id = ?", (req_id,)).fetchone()
            assert row["name"] == "first"

    def test_upsert_snapshot_is_idempotent(self, scan_db: ScanDatabase) -> None:
        with scan_db.transaction() as conn:
            project_id = _insert_project(conn, scan_db)
            _insert_hash(conn, _hash("d"))
            kwargs = dict(
                project_id=project_id,
                branch="main",
                path="a.md",
                blob_hash=_hash("d"),
                commit_sha="abc",
                author="a",
                committer="c",
                encoding="utf-8",
                parser_version=1,
            )
            first = scan_db.upsert_snapshot(conn, **kwargs)
            second = scan_db.upsert_snapshot(conn, **kwargs)
            assert first == second
            assert conn.execute("SELECT COUNT(*) FROM file_snapshot").fetchone()[0] == 1

    def test_recompute_statuses_archives_and_restores(self, scan_db: ScanDatabase) -> None:
        with scan_db.transaction() as conn:
            project_id = _insert_project(conn, scan_db)
            _insert_hash(conn, _hash("e"))
            req_id, _, _, _ = scan_db.upsert_req(conn, "REQ_1", None)
            point_id, _ = scan_db.upsert_point(conn, req_id, "1.1")
            file_id = scan_db.upsert_snapshot(
                conn,
                project_id=project_id,
                branch="main",
                path="a.md",
                blob_hash=_hash("e"),
                commit_sha=None,
                author=None,
                committer=None,
                encoding="utf-8",
                parser_version=1,
            )
            scan_db.replace_occurrences(conn, file_id, [(point_id, 1, "raw")])
            archived, _ = scan_db.recompute_statuses(conn)
            assert archived == 0

            scan_db.replace_occurrences(conn, file_id, [])
            archived, archived_reqs = scan_db.recompute_statuses(conn)
            assert archived == 1
            assert archived_reqs == 1
            assert conn.execute("SELECT status FROM point").fetchone()[0] == 0
            assert conn.execute("SELECT status FROM req").fetchone()[0] == 0

            scan_db.replace_occurrences(conn, file_id, [(point_id, 1, "raw")])
            scan_db.recompute_statuses(conn)
            assert conn.execute("SELECT status FROM point").fetchone()[0] == 1
            assert conn.execute("SELECT status FROM req").fetchone()[0] == 1

    def test_gc_hashes(self, scan_db: ScanDatabase) -> None:
        with scan_db.transaction() as conn:
            project_id = _insert_project(conn, scan_db)
            _insert_hash(conn, _hash("f"))
            _insert_hash(conn, _hash("g"))
            scan_db.upsert_snapshot(
                conn,
                project_id=project_id,
                branch="main",
                path="a.md",
                blob_hash=_hash("f"),
                commit_sha=None,
                author=None,
                committer=None,
                encoding="utf-8",
                parser_version=1,
            )
            removed = scan_db.gc_hashes(conn)
            assert removed == 1
            assert conn.execute("SELECT COUNT(*) FROM file_hash").fetchone()[0] == 1


class TestExportFlags:
    """``is_export`` is reset by real changes and kept by a no-op rescan."""

    @staticmethod
    def _setup(
        scan_db: ScanDatabase, conn: sqlite3.Connection
    ) -> tuple[int, int, int]:
        project_id = _insert_project(conn, scan_db)
        _insert_hash(conn, _hash("x"))
        req_id, _, _, _ = scan_db.upsert_req(conn, "REQ_EXPORT", "Name")
        point_id, _ = scan_db.upsert_point(conn, req_id, "1.1")
        file_id = scan_db.upsert_snapshot(
            conn,
            project_id=project_id,
            branch="main",
            path="docs/export.md",
            blob_hash=_hash("x"),
            commit_sha=None,
            author=None,
            committer=None,
            encoding="utf-8",
            parser_version=1,
        )
        scan_db.replace_occurrences(conn, file_id, [(point_id, 1, "raw")])
        return req_id, point_id, file_id

    def test_defaults_are_zero_and_numbers_null(self, scan_db: ScanDatabase) -> None:
        with scan_db.transaction() as conn:
            req_id, point_id, _ = self._setup(scan_db, conn)
            req_flag = conn.execute(
                "SELECT is_export FROM req WHERE id = ?", (req_id,)
            ).fetchone()[0]
            point_flag = conn.execute(
                "SELECT is_export FROM point WHERE id = ?", (point_id,)
            ).fetchone()[0]
            numbers = conn.execute(
                "SELECT numbeartefact, numbercase, numberscenario, numberstep "
                "FROM point_content"
            ).fetchone()
        assert req_flag == 0
        assert point_flag == 0
        assert tuple(numbers) == (None, None, None, None)

    def test_req_resets_on_name_change(self, scan_db: ScanDatabase) -> None:
        with scan_db.transaction() as conn:
            req_id, _, _ = self._setup(scan_db, conn)
            conn.execute("UPDATE req SET is_export = 1 WHERE id = ?", (req_id,))
            scan_db.upsert_req(conn, "REQ_EXPORT", "New name")
            flag = conn.execute(
                "SELECT is_export FROM req WHERE id = ?", (req_id,)
            ).fetchone()[0]
        assert flag == 0

    def test_req_resets_on_status_change(self, scan_db: ScanDatabase) -> None:
        with scan_db.transaction() as conn:
            req_id, _, _ = self._setup(scan_db, conn)
            conn.execute("UPDATE req SET is_export = 1 WHERE id = ?", (req_id,))
            conn.execute("UPDATE req SET status = 0 WHERE id = ?", (req_id,))
            flag = conn.execute(
                "SELECT is_export FROM req WHERE id = ?", (req_id,)
            ).fetchone()[0]
        assert flag == 0

    def test_req_noop_upsert_keeps_flag(self, scan_db: ScanDatabase) -> None:
        with scan_db.transaction() as conn:
            req_id, _, _ = self._setup(scan_db, conn)
            conn.execute("UPDATE req SET is_export = 1 WHERE id = ?", (req_id,))
            scan_db.upsert_req(conn, "REQ_EXPORT", "Name")
            flag = conn.execute(
                "SELECT is_export FROM req WHERE id = ?", (req_id,)
            ).fetchone()[0]
        assert flag == 1

    def test_point_resets_on_status_change(self, scan_db: ScanDatabase) -> None:
        with scan_db.transaction() as conn:
            _, point_id, _ = self._setup(scan_db, conn)
            conn.execute("UPDATE point SET is_export = 1 WHERE id = ?", (point_id,))
            conn.execute("UPDATE point SET status = 0 WHERE id = ?", (point_id,))
            flag = conn.execute(
                "SELECT is_export FROM point WHERE id = ?", (point_id,)
            ).fetchone()[0]
        assert flag == 0

    def test_point_noop_rescan_keeps_flag(self, scan_db: ScanDatabase) -> None:
        with scan_db.transaction() as conn:
            req_id, point_id, file_id = self._setup(scan_db, conn)
            conn.execute("UPDATE point SET is_export = 1 WHERE id = ?", (point_id,))
            scan_db.upsert_point(conn, req_id, "1.1")
            scan_db.replace_occurrences(conn, file_id, [(point_id, 1, "raw")])
            flag = conn.execute(
                "SELECT is_export FROM point WHERE id = ?", (point_id,)
            ).fetchone()[0]
        assert flag == 1

    def test_point_resets_on_content_insert(self, scan_db: ScanDatabase) -> None:
        with scan_db.transaction() as conn:
            _, point_id, file_id = self._setup(scan_db, conn)
            conn.execute("UPDATE point SET is_export = 1 WHERE id = ?", (point_id,))
            scan_db.replace_occurrences(
                conn, file_id, [(point_id, 1, "raw"), (point_id, 2, "other")]
            )
            flag = conn.execute(
                "SELECT is_export FROM point WHERE id = ?", (point_id,)
            ).fetchone()[0]
        assert flag == 0

    def test_point_resets_on_content_update(self, scan_db: ScanDatabase) -> None:
        with scan_db.transaction() as conn:
            _, point_id, file_id = self._setup(scan_db, conn)
            conn.execute("UPDATE point SET is_export = 1 WHERE id = ?", (point_id,))
            scan_db.replace_occurrences(conn, file_id, [(point_id, 1, "changed")])
            flag = conn.execute(
                "SELECT is_export FROM point WHERE id = ?", (point_id,)
            ).fetchone()[0]
        assert flag == 0

    def test_point_resets_on_number_change(self, scan_db: ScanDatabase) -> None:
        with scan_db.transaction() as conn:
            _, point_id, _ = self._setup(scan_db, conn)
            conn.execute("UPDATE point SET is_export = 1 WHERE id = ?", (point_id,))
            conn.execute(
                "UPDATE point_content SET numbercase = 'TC-1' "
                "WHERE point_id = ?",
                (point_id,),
            )
            flag = conn.execute(
                "SELECT is_export FROM point WHERE id = ?", (point_id,)
            ).fetchone()[0]
        assert flag == 0

    def test_point_resets_on_content_delete(self, scan_db: ScanDatabase) -> None:
        with scan_db.transaction() as conn:
            _, point_id, file_id = self._setup(scan_db, conn)
            conn.execute("UPDATE point SET is_export = 1 WHERE id = ?", (point_id,))
            scan_db.replace_occurrences(conn, file_id, [])
            flag = conn.execute(
                "SELECT is_export FROM point WHERE id = ?", (point_id,)
            ).fetchone()[0]
        assert flag == 0

    def test_export_update_does_not_reset_flag(self, scan_db: ScanDatabase) -> None:
        with scan_db.transaction() as conn:
            req_id, point_id, _ = self._setup(scan_db, conn)
            conn.execute("UPDATE req SET is_export = 1 WHERE id = ?", (req_id,))
            conn.execute("UPDATE point SET is_export = 1 WHERE id = ?", (point_id,))
            req_flag = conn.execute(
                "SELECT is_export FROM req WHERE id = ?", (req_id,)
            ).fetchone()[0]
            point_flag = conn.execute(
                "SELECT is_export FROM point WHERE id = ?", (point_id,)
            ).fetchone()[0]
        assert (req_flag, point_flag) == (1, 1)


class TestRuns:
    def test_active_run_blocks_and_force_supersedes(self, scan_db: ScanDatabase) -> None:
        with scan_db.transaction() as conn:
            project_id = _insert_project(conn, scan_db)
            run_id = scan_db.create_run(conn, project_id)
            active = scan_db.resolve_active_run(conn, project_id)
            assert active is not None and active["id"] == run_id

            assert scan_db.resolve_active_run(conn, project_id, force=True) is None
            row = conn.execute(
                "SELECT status, message FROM scan_run WHERE id = ?", (run_id,)
            ).fetchone()
            assert row["status"] == "failed"
            assert row["message"] == "superseded by --force"

    def test_stale_run_is_closed(self, scan_db: ScanDatabase) -> None:
        with scan_db.transaction() as conn:
            project_id = _insert_project(conn, scan_db)
            run_id = scan_db.create_run(conn, project_id)
            conn.execute(
                "UPDATE scan_run SET started_at = '2020-01-01T00:00:00.000Z' WHERE id = ?",
                (run_id,),
            )
            assert scan_db.resolve_active_run(conn, project_id) is None
            row = conn.execute("SELECT status FROM scan_run WHERE id = ?", (run_id,)).fetchone()
            assert row["status"] == "failed"

    def test_finish_run(self, scan_db: ScanDatabase) -> None:
        with scan_db.transaction() as conn:
            project_id = _insert_project(conn, scan_db)
            run_id = scan_db.create_run(conn, project_id)
            scan_db.finish_run(
                conn,
                run_id,
                status="success",
                branches=["main"],
                files_scanned=2,
                files_skipped=1,
                markers_found=3,
                errors_count=0,
                message="ok",
            )
        latest = scan_db.latest_run(project_id)
        assert latest is not None
        assert latest["status"] == "success"
        assert latest["branches"] == '["main"]'
        assert latest["files_scanned"] == 2
        assert latest["finished_at"] == now_iso() or latest["finished_at"]
