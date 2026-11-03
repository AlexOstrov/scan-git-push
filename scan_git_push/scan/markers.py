"""Parser for the canonical ``<!-- REQ-META ... -->`` marker.

Only the canonical format is supported (``dop_tz01.md``, section 6). Lines with
legacy markers (``ID_REQ`` / ``ID_EXAMPLE``) or an unterminated comment are
reported as warnings and are not treated as markers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Bump whenever the parsing rules change: it forces a re-parse of every file.
# v2: trailing dots in ``point`` are ignored (``5.5.1.`` == ``5.5.1``).
SCAN_PARSER_VERSION = 2

MAX_MARKER_LENGTH = 4096
MAX_NAME_LENGTH = 255

_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_POINT_RE = re.compile(r"^\d+(?:\.\d+){0,7}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
_LEGACY_MARKERS = ("ID_REQ", "ID_EXAMPLE")

_KNOWN_KEYS = ("id", "point", "name")
_WS = " \t"


@dataclass(frozen=True)
class Occurrence:
    """A valid marker occurrence inside a file."""

    code: str
    point: str
    name: str | None
    line_no: int
    raw_marker: str


def normalize_point(value: str) -> str:
    """Normalize a section number: ``5.5.1.`` and ``5.5.1`` are identical.

    A trailing dot is a common typo in hand-written markers and carries no
    meaning, so it is stripped before validation and storage.
    """
    return value.strip().rstrip(".")


@dataclass
class MarkerError:
    """A marker that was found but rejected by validation."""

    path: str
    line: int
    message: str
    missing: list[str] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)
    duplicate: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "file": self.path,
            "line": self.line,
            "message": self.message,
        }
        if self.missing:
            data["missing"] = list(self.missing)
        if self.invalid:
            data["invalid"] = list(self.invalid)
        if self.duplicate:
            data["duplicate"] = list(self.duplicate)
        return data

    def describe(self) -> str:
        parts: list[str] = []
        if self.missing:
            parts.append("missing: " + ", ".join(self.missing))
        if self.invalid:
            parts.append("invalid: " + ", ".join(self.invalid))
        if self.duplicate:
            parts.append("duplicate: " + ", ".join(self.duplicate))
        detail = "; ".join(parts) or self.message
        return f"{self.path}:{self.line} {detail}"


@dataclass(frozen=True)
class MarkerWarning:
    """A diagnostic that does not affect the exit code."""

    path: str
    line: int
    message: str

    def describe(self) -> str:
        return f"{self.path}:{self.line} {self.message}"


@dataclass
class ParseResult:
    occurrences: list[Occurrence] = field(default_factory=list)
    errors: list[MarkerError] = field(default_factory=list)
    warnings: list[MarkerWarning] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)


# ---------------------------------------------------------------------------
# Low-level parsing
# ---------------------------------------------------------------------------
def _skip_ws(line: str, index: int) -> int:
    while index < len(line) and line[index] in _WS:
        index += 1
    return index


def _parse_attributes(line: str, index: int) -> tuple[dict[str, str], list[str], str | None]:
    """Parse ``key="value"`` attributes after the ``REQ-META`` prefix.

    Returns ``(attributes, duplicate_keys, error)``. ``error`` is set when the
    attribute syntax is broken. Unterminated comments are reported through the
    special error value ``"unterminated"`` so the caller can downgrade them to a
    legacy warning.
    """
    attrs: dict[str, str] = {}
    duplicates: list[str] = []

    while True:
        index = _skip_ws(line, index)
        if index >= len(line):
            return attrs, duplicates, "unterminated"
        if line.startswith("-->", index):
            return attrs, duplicates, None

        match = _KEY_RE.match(line, index)
        if match is None:
            return attrs, duplicates, f"unexpected character {line[index]!r} at column {index + 1}"
        key = match.group(0)
        index = _skip_ws(line, match.end())

        if index >= len(line) or line[index] != "=":
            return attrs, duplicates, f"missing '=' after key {key!r}"
        index = _skip_ws(line, index + 1)

        if index >= len(line) or line[index] != '"':
            return attrs, duplicates, f"value of {key!r} must be quoted"
        index += 1

        value_chars: list[str] = []
        while index < len(line):
            char = line[index]
            if char == "\\" and index + 1 < len(line):
                escaped = line[index + 1]
                if escaped in {'"', "\\"}:
                    value_chars.append(escaped)
                    index += 2
                    continue
                value_chars.append(escaped)
                index += 2
                continue
            if char == '"':
                index += 1
                break
            value_chars.append(char)
            index += 1
        else:
            return attrs, duplicates, "unterminated attribute value"

        key_lower = key.lower()
        if key_lower in attrs:
            duplicates.append(key_lower)
        attrs[key_lower] = "".join(value_chars)


def _parse_canonical_line(
    line: str, path: str, line_no: int
) -> tuple[Occurrence | None, MarkerError | None, list[MarkerWarning]]:
    warnings: list[MarkerWarning] = []

    marker_name = line.find("REQ-META")
    if marker_name < 0:
        return None, None, warnings
    # The comment that opens the marker is the nearest ``<!--`` to its left.
    comment_start = line.rfind("<!--", 0, marker_name)
    if comment_start < 0:
        return None, None, warnings

    prefix_end = _skip_ws(line, comment_start + len("<!--"))
    if not line.startswith("REQ-META", prefix_end):
        return None, None, warnings
    index = prefix_end + len("REQ-META")
    # ``REQ-METAX`` is not a marker but a legacy look-alike.
    if index < len(line) and (line[index].isalnum() or line[index] in "_"):
        warnings.append(MarkerWarning(path, line_no, "legacy marker format"))
        return None, None, warnings

    attrs, duplicates, error = _parse_attributes(line, index)
    if error == "unterminated":
        warnings.append(MarkerWarning(path, line_no, "legacy marker format"))
        return None, None, warnings
    if error is not None:
        return (
            None,
            MarkerError(path, line_no, f"cannot parse marker: {error}"),
            warnings,
        )

    for key in attrs:
        if key not in _KNOWN_KEYS:
            warnings.append(MarkerWarning(path, line_no, f"unknown marker key {key!r}"))

    comment_end = line.find("-->", index)
    raw_marker = line[comment_start : comment_end + 3] if comment_end >= 0 else line[comment_start:]
    if len(raw_marker) > MAX_MARKER_LENGTH:
        return (
            None,
            MarkerError(
                path,
                line_no,
                f"marker is longer than {MAX_MARKER_LENGTH} characters",
            ),
            warnings,
        )

    missing: list[str] = []
    invalid: list[str] = []
    code = (attrs.get("id") or "").strip()
    raw_point = (attrs.get("point") or "").strip()
    point = normalize_point(raw_point)

    if not code:
        missing.append("id")
    if not raw_point:
        missing.append("point")

    if code and not _ID_RE.match(code):
        invalid.append("id")
    if raw_point and not point:
        # A point that consists only of dots carries no section number.
        invalid.append("point")
    elif point and not _POINT_RE.match(point):
        invalid.append("point")

    name = attrs.get("name") or ""
    name = name.strip()
    if name and (
        len(name) > MAX_NAME_LENGTH or _CONTROL_RE.search(name) is not None
    ):
        invalid.append("name")

    if duplicates:
        return (
            None,
            MarkerError(
                path,
                line_no,
                "duplicate marker keys: " + ", ".join(sorted(set(duplicates))),
                duplicate=sorted(set(duplicates)),
            ),
            warnings,
        )

    if missing or invalid:
        message_parts: list[str] = []
        if missing:
            message_parts.append("missing: " + ", ".join(missing))
        if invalid:
            message_parts.append("invalid: " + ", ".join(invalid))
        return (
            None,
            MarkerError(path, line_no, "; ".join(message_parts), missing, invalid),
            warnings,
        )

    return (
        Occurrence(
            code=code,
            point=point,
            name=name or None,
            line_no=line_no,
            raw_marker=raw_marker,
        ),
        None,
        warnings,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def parse_markers(text: str, path: str = "<memory>") -> ParseResult:
    """Parse all canonical markers in *text*.

    ``path`` is only used for diagnostics. Line numbers are 1-based.
    """
    result = ParseResult()

    for line_no, line in enumerate(text.splitlines(), start=1):
        if "<!--" not in line:
            continue

        if "REQ-META" in line:
            occurrence, error, warnings = _parse_canonical_line(line, path, line_no)
            result.warnings.extend(warnings)
            if occurrence is not None:
                result.occurrences.append(occurrence)
            if error is not None:
                result.errors.append(error)
            continue

        if any(marker in line for marker in _LEGACY_MARKERS):
            result.warnings.append(
                MarkerWarning(path, line_no, "legacy marker format")
            )

    return result


def format_occurrence(occurrence: Occurrence) -> str:
    name = f' name="{occurrence.name}"' if occurrence.name else ""
    return f"id={occurrence.code} point={occurrence.point}{name}"
