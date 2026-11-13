# AI contribution: 50% or more AI-generated
"""Expected output of Polars cleaning and its mapped-record integration."""

import copy

import pytest

from app.ingestion.mapped import MappedRecord, clean_mapped
from app.processing.cleaning import CleaningConfig, FieldRule, clean_records


@pytest.mark.parametrize(
    ("kind", "original", "expected"),
    [
        ("string", "  Company   One  ", "Company One"),
        ("string", "Socie\u0301te\u0301", "Société"),
        ("string", "MontrÃ©al", "Montréal"),
        ("string", "\x1c A\tB\x1f", "A B"),
        ("string", " N/A ", None),
        ("string", None, None),
        ("integer", " +12 ", 12),
        ("integer", "9223372036854775808", 9223372036854775808),
        ("integer", "١٢", 12),
        ("boolean", " OUI ", True),
        ("boolean", "fal\u017fe", False),
        ("email", " HELLO@EXAMPLE.ORG ", "hello@example.org"),
    ],
)
def test_expected_scalar_output(kind, original, expected):
    # Arrange
    config = CleaningConfig(fields={"value": FieldRule(type=kind)})
    # Act
    result = clean_records([{"value": original}], config)
    # Assert
    assert result.cleaned == [{"value": expected}]
    assert result.flags == []


def test_invalid_conversion_retains_value_and_original_evidence():
    # Arrange
    config = CleaningConfig(fields={"age": FieldRule(type="integer")})
    # Act
    result = clean_records([{"age": " bad "}], config)
    # Assert
    assert result.cleaned == [{"age": "bad"}]
    assert result.flags == [
        {"record_index": 0, "field": "age", "original_value": " bad ", "reason": "invalid integer"}
    ]


def test_mapped_batch_keeps_last_repeat_and_reports_missing_names():
    # Arrange
    nested = {"name": " N/A ", "list": ["  untouched  "]}
    rows = [
        MappedRecord("organization", "a", None, "  A  ", attributes=nested),
        MappedRecord("person", "bad", None, " N/A "),
        MappedRecord("event", "e", None, None),
        MappedRecord("organization", "b", None, " B "),
        MappedRecord("organization", "a", None, "A", attributes=nested),
    ]
    before = copy.deepcopy(rows)
    # Act
    result = clean_mapped(rows)
    # Assert
    assert [row.external_id for row in result.records] == ["e", "b", "a"]
    assert result.records[-1].attributes is nested
    assert [failure.external_id for failure in result.failures] == ["bad"]
    assert result.cleaning_flag_counts == {
        "required field is missing": 1,
        "duplicate in current batch": 1,
    }
    assert rows == before


@pytest.mark.parametrize(
    ("rule", "values"),
    [
        (
            FieldRule(type="number", decimal_comma=True, min_value=0, max_value=10),
            [
                "1,25",
                "-1",
                "11",
                "bad",
                "inf",
                "NaN",
                "+NaN",
                "-NaN",
                "INFINITY",
                "1e500",
                2.5,
                True,
                None,
            ],
        ),
        (FieldRule(type="integer", min_value=0, max_value=10), ["2", "-1", "11", "bad", 3]),
        (FieldRule(type="boolean", required=True), ["YES", "non", "maybe", True, 0, 2, None]),
        (FieldRule(type="email"), ["UPPER@example.org", "bad", None]),
        (
            FieldRule(categories={"CONFÉRENCIER": "speaker"}, allowed_values=("speaker",)),
            ["conférencier", "other", None],
        ),
        (FieldRule(min_value=1), ["text"]),
        (FieldRule(type="date", input_formats=("%d/%m/%Y",)), ["2026-01-02", "31/12/2026"]),
        (FieldRule(type="datetime"), ["2026-10-20T16:30:00+02:00", "2026-01-01T10:00:00.123Z"]),
        (
            FieldRule(type="datetime", source_timezone="America/Toronto"),
            ["2026-10-20T14:30:00", "2026-11-01T01:30:00", "2026-03-08T02:30:00"],
        ),
        (
            FieldRule(
                type="datetime",
                input_formats=("%d/%m/%Y %H:%M",),
                source_timezone="America/Toronto",
            ),
            ["20/10/2026 14:30"],
        ),
        (FieldRule(type="datetime", source_timezone="Invalid/Zone"), ["2026-01-01T10:00:00"]),
    ],
)
def test_supported_columns_do_not_use_python_conversion(monkeypatch, rule, values):
    # Arrange
    from app.processing import cleaning
    from app.processing.cleaning_values import _convert

    expected_values, expected_reasons = [], []
    for value in values:
        reason = None
        if value is None:
            output = None
            reason = "required field is missing" if rule.required else None
        else:
            try:
                output = _convert(value, rule)
            except ValueError as error:
                output, reason = value, str(error)
        expected_values.append(output)
        if reason:
            expected_reasons.append(reason)

    def unexpected(*args):
        pytest.fail("supported column fell back to Python conversion")

    monkeypatch.setattr(cleaning, "_convert", unexpected)
    config = CleaningConfig(fields={"value": rule}, remove_duplicate_records=False)
    # Act
    result = clean_records([{"value": value} for value in values], config)
    # Assert
    assert [record["value"] for record in result.cleaned] == expected_values
    assert [flag["reason"] for flag in result.flags] == expected_reasons


@pytest.mark.parametrize(
    ("rule", "value", "expected"),
    [
        (FieldRule(type="integer"), "9223372036854775808", 9223372036854775808),
        (FieldRule(type="integer"), "١٢", 12),
        (FieldRule(type="number"), "1_000.5", 1000.5),
        (FieldRule(type="boolean"), "fal\u017fe", False),
        (FieldRule(type="date", input_formats=("%d.%m.%Y",)), "31.12.2026", "2026-12-31"),
        (FieldRule(type="string", categories={"one": 1}, min_value=0), "one", 1),
        (FieldRule(type="number", min_value=float("nan")), "1", 1.0),
    ],
)
def test_special_cases_keep_python_semantics(rule, value, expected):
    # Arrange
    config = CleaningConfig(fields={"value": rule})
    # Act
    result = clean_records([{"value": value}], config)
    # Assert
    assert result.cleaned == [{"value": expected}]
    assert result.flags == []


@pytest.mark.parametrize(
    "kind", ["string", "integer", "number", "boolean", "email", "date", "datetime"]
)
def test_optional_null_stays_null_for_every_native_type(kind):
    # Arrange
    config = CleaningConfig(fields={"value": FieldRule(type=kind)})
    # Act
    result = clean_records([{"value": None}, {"value": " N/A "}], config)
    # Assert
    assert result.cleaned == [{"value": None}]
    assert result.flags[-1]["reason"] == "duplicate in current batch"


@pytest.mark.parametrize(
    ("rule", "value", "reason"),
    [
        (
            FieldRule(type="integer", max_value=float(2**53)),
            str(2**53 + 1),
            "value is above maximum",
        ),
        (FieldRule(type="number", min_value=2**53 + 1), str(2**53), "value is below minimum"),
    ],
)
def test_large_mixed_numeric_bounds_preserve_exact_comparison(rule, value, reason):
    # Arrange
    config = CleaningConfig(fields={"value": rule})
    # Act
    result = clean_records([{"value": value}], config)
    # Assert
    assert result.cleaned == [{"value": value}]
    assert result.flags[0]["reason"] == reason
