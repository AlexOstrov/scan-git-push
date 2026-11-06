"""Tests for the canonical ``REQ-META`` marker parser."""

from __future__ import annotations

from scan_git_push.scan.markers import (
    MAX_MARKER_LENGTH,
    SCAN_PARSER_VERSION,
    parse_markers,
)


class TestCanonicalMarker:
    def test_simple_marker(self) -> None:
        result = parse_markers('<!-- REQ-META id="REQ_5" point="4.1" name="Требование 5" -->')
        assert result.errors == []
        assert result.warnings == []
        assert len(result.occurrences) == 1
        occurrence = result.occurrences[0]
        assert occurrence.code == "REQ_5"
        assert occurrence.point == "4.1"
        assert occurrence.name == "Требование 5"
        assert occurrence.line_no == 1
        assert occurrence.raw_marker == (
            '<!-- REQ-META id="REQ_5" point="4.1" name="Требование 5" -->'
        )

    def test_attribute_order_and_optional_name(self) -> None:
        result = parse_markers('<!--REQ-META point="2" id="REQ_A"-->)')
        assert [item.code for item in result.occurrences] == ["REQ_A"]
        assert result.occurrences[0].name is None
        assert result.occurrences[0].point == "2"

    def test_case_insensitive_keys(self) -> None:
        result = parse_markers('<!-- REQ-META ID="REQ_1" Point="1.0" NAME="n" -->')
        assert not result.errors
        assert result.occurrences[0].code == "REQ_1"

    def test_escaped_quotes_and_unicode(self) -> None:
        result = parse_markers(
            '<!-- REQ-META id="REQ_1" point="1" name="Имя \\"в кавычках\\"" -->'
        )
        assert not result.errors
        assert result.occurrences[0].name == 'Имя "в кавычках"'

    def test_unknown_escape_keeps_literal_backslash(self) -> None:
        result = parse_markers(
            r'<!-- REQ-META id="REQ_1" point="1" name="C:\users\new" -->'
        )
        assert not result.errors
        assert result.occurrences[0].name == r"C:\users\new"

    def test_empty_name_is_absent(self) -> None:
        result = parse_markers('<!-- REQ-META id="REQ_1" point="1" name="" -->')
        assert not result.errors
        assert result.occurrences[0].name is None

    def test_multiple_markers_and_duplicate_pairs(self) -> None:
        text = "\n".join(
            [
                '<!-- REQ-META id="REQ_1" point="1.1" -->',
                "plain text",
                '<!-- REQ-META id="REQ_1" point="1.1" -->',
                "<!-- ordinary comment -->",
                '<!-- REQ-META id="REQ_2" point="1.2" -->',
            ]
        )
        result = parse_markers(text)
        assert not result.errors
        assert [(item.code, item.line_no) for item in result.occurrences] == [
            ("REQ_1", 1),
            ("REQ_1", 3),
            ("REQ_2", 5),
        ]

    def test_marker_inside_text_line(self) -> None:
        result = parse_markers('prefix <!-- REQ-META id="REQ_1" point="1.1" --> suffix')
        assert result.occurrences[0].raw_marker == (
            '<!-- REQ-META id="REQ_1" point="1.1" -->'
        )


class TestValidation:
    def test_missing_id_and_point(self) -> None:
        result = parse_markers('<!-- REQ-META name="no identifiers" -->')
        assert len(result.errors) == 1
        assert result.errors[0].missing == ["id", "point"]

    def test_missing_point(self) -> None:
        result = parse_markers('<!-- REQ-META id="REQ_5" name="Без пункта" -->')
        assert result.errors[0].missing == ["point"]
        assert result.errors[0].invalid == []

    def test_invalid_id(self) -> None:
        result = parse_markers('<!-- REQ-META id="REQ 5" point="4.1" -->')
        assert result.errors[0].invalid == ["id"]

    def test_invalid_point(self) -> None:
        result = parse_markers('<!-- REQ-META id="REQ_5" point="4.a" -->')
        assert result.errors[0].invalid == ["point"]

    def test_trailing_dot_in_point_is_ignored(self) -> None:
        result = parse_markers(
            '<!-- REQ-META id="TR_3" point="5.5.1." name="Требование 3" -->'
        )
        assert not result.errors
        assert result.occurrences[0].point == "5.5.1"
        assert result.occurrences[0].raw_marker.endswith('point="5.5.1." name="Требование 3" -->')

    def test_point_without_trailing_dot_is_unchanged(self) -> None:
        result = parse_markers('<!-- REQ-META id="TR_3" point="5.5.1" -->')
        assert result.occurrences[0].point == "5.5.1"

    def test_point_of_only_dots_is_invalid(self) -> None:
        result = parse_markers('<!-- REQ-META id="TR_3" point="." -->')
        assert result.occurrences == []
        assert result.errors[0].invalid == ["point"]

    def test_duplicate_key(self) -> None:
        result = parse_markers(
            '<!-- REQ-META id="REQ_5" point="4.1" id="REQ_6" -->'
        )
        assert result.occurrences == []
        assert result.errors[0].duplicate == ["id"]

    def test_unknown_key_is_warning(self) -> None:
        result = parse_markers('<!-- REQ-META id="REQ_5" point="4.1" color="red" -->')
        assert not result.errors
        assert len(result.occurrences) == 1
        assert "unknown marker key 'color'" in result.warnings[0].message

    def test_name_too_long(self) -> None:
        name = "x" * 256
        result = parse_markers(f'<!-- REQ-META id="REQ_1" point="1" name="{name}" -->')
        assert result.errors[0].invalid == ["name"]

    def test_marker_too_long(self) -> None:
        name = "x" * (MAX_MARKER_LENGTH + 100)
        result = parse_markers(f'<!-- REQ-META id="REQ_1" point="1" name="{name}" -->')
        assert result.errors
        assert "longer than" in result.errors[0].message


class TestLegacyAndOrdinaryComments:
    def test_legacy_id_req(self) -> None:
        result = parse_markers('<!-- "ID_REQ" = "REQ_5" "POINT" = "4.1" -->')
        assert result.occurrences == []
        assert result.errors == []
        assert result.warnings[0].message == "legacy marker format"

    def test_legacy_id_example_without_closing(self) -> None:
        result = parse_markers('<!-- "ID_EXAMPLE" = "REQ_5" "POINT" = "4.1"')
        assert result.occurrences == []
        assert result.errors == []
        assert result.warnings[0].message == "legacy marker format"

    def test_unterminated_req_meta_is_warning(self) -> None:
        result = parse_markers('<!-- REQ-META id="REQ_5" point="4.1"')
        assert result.occurrences == []
        assert result.errors == []
        assert result.warnings[0].message == "legacy marker format"

    def test_ordinary_comment_is_ignored(self) -> None:
        result = parse_markers("<!-- TODO: доработать -->")
        assert result.occurrences == []
        assert result.errors == []
        assert result.warnings == []

    def test_parser_version_is_positive(self) -> None:
        assert SCAN_PARSER_VERSION >= 1
