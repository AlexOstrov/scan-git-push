"""``scan`` mode: REQ-META extraction into ``scan.db``.

The mode is independent from commit analysis: it never calls an LLM and never
touches ``analysis.db``. See ``dop_tz01.md`` for the full specification.
"""

from __future__ import annotations

from scan_git_push.scan.database import (
    SCAN_SCHEMA_VERSION,
    ScanDatabase,
    ScanDatabaseError,
    ScanSchemaVersionError,
)
from scan_git_push.scan.markers import (
    SCAN_PARSER_VERSION,
    MarkerError,
    MarkerWarning,
    Occurrence,
    ParseResult,
    parse_markers,
)

__all__ = [
    "SCAN_PARSER_VERSION",
    "SCAN_SCHEMA_VERSION",
    "MarkerError",
    "MarkerWarning",
    "Occurrence",
    "ParseResult",
    "ScanDatabase",
    "ScanDatabaseError",
    "ScanSchemaVersionError",
    "parse_markers",
]
