# AI contribution: Below 50% AI-generated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import polars as pl

# Field rule will select the format in another file
DATE_FORMATS = {
    "%d/%m/%Y": r"^\d{1,2}/\d{1,2}/\d{4}$",
    "%m/%d/%Y": r"^\d{1,2}/\d{1,2}/\d{4}$",
    "%Y/%m/%d": r"^\d{4}/\d{1,2}/\d{1,2}$",
    "%Y-%m-%d": r"^\d{4}-\d{1,2}-\d{1,2}$",
}
DATETIME_FORMATS = {
    "%d/%m/%Y %H:%M": r"^\d{1,2}/\d{1,2}/\d{4} \d{1,2}:\d{1,2}$",
    "%Y-%m-%d %H:%M:%S": r"^\d{4}-\d{1,2}-\d{1,2} \d{1,2}:\d{1,2}:[0-5]?\d$",
    "%Y-%m-%dT%H:%M:%S": r"^\d{4}-\d{1,2}-\d{1,2}T\d{1,2}:\d{1,2}:[0-5]?\d$",
}


# Builds expressions that converts a column into standardized date strings
def date_expressions(column, rule):
    # Get the pattern
    formats = [pattern for pattern in rule.input_formats if pattern in DATE_FORMATS]
    # Make sure the configured format is supported
    if len(formats) != len(rule.input_formats):
        return None
    # Check whether a value follows the date format YYYY-MM-DD, if yes return the value
    iso = pl.when(column.str.contains(r"^\d{4}-\d{2}-\d{2}$")).then(column)
    # convert a string into a date by testing multiple date formats
    parsed = pl.coalesce(
        [
            iso.str.to_date("%Y-%m-%d", strict=False),
            *(
                pl.when(column.str.contains(DATE_FORMATS[pattern]))
                .then(column)
                .str.to_date(pattern, strict=False)
                for pattern in formats
            ),
        ]
    )
    # Failed parses use Python to preserve its detailed error messages and ISO
    return (
        parsed.dt.strftime("%Y-%m-%d"),
        pl.lit(None, pl.String),
        parsed.is_not_null() & ~column.str.contains(r"(^|[/.-])0000($|[ T])"),
    )


# Builds instructions to standardize a column of timestamp strings into UTC.
def datetime_expressions(column, rule):
    formats = [pattern for pattern in rule.input_formats if pattern in DATETIME_FORMATS]
    if len(formats) != len(rule.input_formats):
        return None
    iso = column.str.replace(" ", "T", literal=True)
    shape = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:[0-5]\d(\.\d{1,6})?"
    aware = pl.when(iso.str.contains(shape + r"(Z|[+-]\d{2}:\d{2})$")).then(iso)
    utc = aware.str.to_datetime(
        "%Y-%m-%dT%H:%M:%S%.f%#z", strict=False, time_unit="us", time_zone="UTC"
    )
    naive = pl.coalesce(
        [
            pl.when(iso.str.contains(shape + "$"))
            .then(iso)
            .str.to_datetime("%Y-%m-%dT%H:%M:%S%.f", strict=False, time_unit="us"),
            *(
                pl.when(column.str.contains(DATETIME_FORMATS[pattern]))
                .then(column)
                .str.to_datetime(pattern, strict=False, time_unit="us")
                for pattern in formats
            ),
        ]
    )
    localized, local_reason = _local_datetime(naive, rule.source_timezone)
    parsed = pl.coalesce(utc, localized)
    output = (
        pl.when(parsed.dt.microsecond() == 0)
        .then(parsed.dt.strftime("%Y-%m-%dT%H:%M:%SZ"))
        .otherwise(parsed.dt.strftime("%Y-%m-%dT%H:%M:%S%.6fZ"))
    )
    reason = pl.when(utc.is_not_null()).then(pl.lit(None, pl.String)).otherwise(local_reason)
    return (
        output,
        reason,
        (utc.is_not_null() | naive.is_not_null()) & ~column.str.starts_with("0000"),
    )


# Handles timestamps that have a time but no timezone
def _local_datetime(parsed, timezone):
    if not timezone:
        return parsed.dt.replace_time_zone("UTC"), pl.lit(
            "datetime has no timezone; configure source_timezone"
        )
    try:
        # Check if timezone exists
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return parsed.dt.replace_time_zone("UTC"), pl.lit("unknown source timezone")
    first = parsed.dt.replace_time_zone(timezone, ambiguous="earliest", non_existent="null")
    second = parsed.dt.replace_time_zone(timezone, ambiguous="latest", non_existent="null")
    reason = (
        pl.when(parsed.is_not_null() & first.is_null())
        .then(pl.lit("local datetime does not exist because of daylight saving time"))
        .when(first != second)
        .then(pl.lit("ambiguous local datetime because of daylight saving time"))
        .otherwise(pl.lit(None, pl.String))
    )
    return first.dt.convert_time_zone("UTC"), reason
