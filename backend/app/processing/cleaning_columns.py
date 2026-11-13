# AI contribution: Below 50% AI-generated
# Class using polars to clean columns

import math
import re

import polars as pl

from app.processing.cleaning_dates import date_expressions, datetime_expressions

# Python str.split also treats these four control characters as whitespace.
WHITESPACE = r"[\s\x1c-\x1f]+"
BROKEN_ENCODING = re.compile("Ã|Â|â€|â€™|â€œ|â€“")


# Start with a function that receives columns-shaped data
# Builds the text-normalization instructions that the cleaner executes by taking columns of text and
# returning a Polars LazyFrame
# Passing the columns produced by clean_scalar_batch() where the key is the column name and list are
#  the values
# Pass also the cleaning policy of "n/a", "na", "null", "none", "unknown", "-" as missing
def text_plan(columns: dict[str, list[str | None]], missing: set[str]) -> pl.LazyFrame:
    # 1. Construct a table
    frame = pl.DataFrame(columns, schema={name: pl.String for name in columns})

    # Build an expression for each field
    expressions = []
    for name in columns:
        # Refer to the entire column, normalize unicode for accents and so on, collapse whitespace
        value = pl.col(name).str.normalize("NFC").str.replace_all(WHITESPACE, " ")
        # Remove whitespaces at the start and end
        value = value.str.strip_chars(" ")
        expressions.append(
            # Check if lowercased normalized text match one of our missing-value markers
            pl.when(value.str.to_lowercase().is_in(sorted(missing)))
            # if it does, return None as typed null expression since in Polars or else return the
            # cleaned value
            .then(pl.lit(None, dtype=pl.String))
            .otherwise(value)
            # Give the result it's field name for example city column would pass city
            .alias(name)
        )
    # Return a lazy plan whcih turns the table into a lazy source and describes the output columns
    # and transformations
    return frame.lazy().select(expressions)


# Build a conditional error message
def _failure(condition, reason):
    return pl.when(condition).then(pl.lit(reason)).otherwise(pl.lit(None, pl.String))


# Build converted value, error reason and native-handling mask.
def _conversion_expressions(column, rule, true_values, false_values):
    handled = pl.lit(True)
    if rule.type == "string":
        return column, pl.lit(None, pl.String), handled
    if rule.type == "integer":
        converted = column.cast(pl.Int64, strict=False)
        digits = column.str.contains(r"^[+-]?[0-9]+$")
        handled = ~(digits & converted.is_null())  # Python arbitrary-size ints.
        if any(type(bound) is float for bound in (rule.min_value, rule.max_value)):
            # Arrow float comparisons can round large integers, unlike Python.
            handled = handled & converted.is_between(-(2**53), 2**53)
        return converted, _failure(~digits, "invalid integer"), handled
    if rule.type == "number":
        number = column
        if rule.decimal_comma:
            number = (
                pl.when(column.str.contains(",") & ~column.str.contains(r"\."))
                .then(column.str.replace_all(",", ".", literal=True))
                .otherwise(column)
            )
        converted = number.cast(pl.Float64, strict=False)
        reason = (
            pl.when(converted.is_null())
            .then(pl.lit("invalid number"))
            .when(~converted.is_finite())
            .then(pl.lit("number must be finite"))
            .otherwise(pl.lit(None, pl.String))
        )
        return converted, reason, ~column.str.contains("_", literal=True)
    if rule.type == "boolean":
        lowered = column.str.to_lowercase()
        valid = lowered.is_in(sorted(true_values | false_values))
        return lowered.is_in(sorted(true_values)), _failure(~valid, "invalid boolean"), handled
    if rule.type == "email":
        valid = column.str.contains(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
        return column.str.to_lowercase(), _failure(~valid, "invalid email"), handled
    if rule.type == "date":
        return date_expressions(column, rule)
    if rule.type == "datetime":
        return datetime_expressions(column, rule)
    return None


def _native_rule_supported(rule):
    """Only express rules whose Python equality and output types are preserved."""
    text = rule.type in ("string", "email", "date", "datetime")
    if rule.categories and text and any(type(v) is not str for v in rule.categories.values()):
        return False
    if rule.allowed_values is not None:
        expected = {"integer": int, "number": float, "boolean": bool}.get(rule.type, str)
        for value in rule.allowed_values:
            if type(value) is not expected:
                return False
            if expected is int and not -(2**63) <= value < 2**63:
                return False
    if rule.type == "number" and any(
        type(bound) is int and abs(bound) > 2**53 for bound in (rule.min_value, rule.max_value)
    ):
        return False
    return all(
        bound is None
        or (
            type(bound) in (int, float)
            and (type(bound) is not float or not math.isnan(bound))
            and (not isinstance(bound, int) or abs(bound) < 2**63)
        )
        for bound in (rule.min_value, rule.max_value)
    )


def _validation_reason(column, rule):
    reason = pl.lit(None, pl.String)
    if rule.min_value is not None or rule.max_value is not None:
        if rule.type not in ("integer", "number"):
            reason = pl.lit("number limits require a numeric field")
        else:
            if rule.max_value is not None:
                reason = (
                    pl.when(column > rule.max_value)
                    .then(pl.lit("value is above maximum"))
                    .otherwise(reason)
                )
            if rule.min_value is not None:
                reason = (
                    pl.when(column < rule.min_value)
                    .then(pl.lit("value is below minimum"))
                    .otherwise(reason)
                )
    if rule.allowed_values is not None:
        reason = (
            pl.when(~column.is_in(list(rule.allowed_values)))
            .then(pl.lit("value not in allowed values"))
            .otherwise(reason)
        )
    return reason


def convert_strings(values, rule, true_values, false_values):
    if not _native_rule_supported(rule):
        return None
    column = pl.col("value")
    expressions = _conversion_expressions(column, rule, true_values, false_values)
    if expressions is None:
        return None
    converted, reason, handled = expressions
    eligibility = [
        value is None
        or (
            value.isascii()
            if rule.type in ("integer", "number")
            else value.casefold() == value.lower()
        )
        for value in values
    ]
    if rule.categories and rule.type in ("string", "email", "date", "datetime"):
        mapping = {str(key).casefold(): value for key, value in rule.categories.items()}
        converted = converted.str.to_lowercase().replace_strict(mapping, default=converted)
    frame = pl.DataFrame(
        {"value": values, "eligible": eligibility},
        schema={"value": pl.String, "eligible": pl.Boolean},
    ).lazy()
    frame = frame.select(
        column,
        converted.alias("converted"),
        reason.alias("reason"),
        ((handled & pl.col("eligible")).fill_null(False) | column.is_null()).alias("handled"),
    )
    missing_reason = "required field is missing" if rule.required else None
    reason = (
        pl.when(column.is_null())
        .then(pl.lit(missing_reason, pl.String))
        .otherwise(pl.coalesce(pl.col("reason"), _validation_reason(pl.col("converted"), rule)))
    )
    return frame.with_columns(
        reason.alias("reason"),
        pl.when(column.is_null()).then(None).otherwise(pl.col("converted")).alias("converted"),
    ).collect()
