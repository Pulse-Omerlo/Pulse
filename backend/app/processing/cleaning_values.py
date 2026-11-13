# AI contribution: Below 50% AI-generated

import math
import re
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

TRUE_VALUES = {"true", "yes", "oui", "1"}
FALSE_VALUES = {"false", "no", "non", "0"}
EMAIL_PATTERN = r"[^\s@]+@[^\s@]+\.[^\s@]+"


def _read_date_text(value, rule):
    """Try ISO first, then the source's explicitly configured formats."""
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return date.fromisoformat(text) if rule.type == "date" else datetime.fromisoformat(text)
    except ValueError:
        # A non-ISO value may still match a configured source format.
        for pattern in rule.input_formats:
            try:
                return datetime.strptime(value, pattern)
            except ValueError:
                continue

    if re.fullmatch(r"\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}.*", value):
        raise ValueError("ambiguous or unsupported day/month date; set input_formats")
    raise ValueError("invalid or unsupported date format")


def _localize_datetime(value, source_timezone):
    """Attach a source timezone, rejecting missing and repeated local times."""
    if not source_timezone:
        raise ValueError("datetime has no timezone; configure source_timezone")
    try:
        local_timezone = ZoneInfo(source_timezone)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError("unknown source timezone") from error

    first = value.replace(tzinfo=local_timezone, fold=0)
    second = value.replace(tzinfo=local_timezone, fold=1)
    first_back = first.astimezone(UTC).astimezone(local_timezone)
    second_back = second.astimezone(UTC).astimezone(local_timezone)
    first_valid = first_back.replace(tzinfo=None) == value
    second_valid = second_back.replace(tzinfo=None) == value
    if not first_valid and not second_valid:
        raise ValueError("local datetime does not exist because of daylight saving time")
    if first.utcoffset() != second.utcoffset() and first_valid and second_valid:
        raise ValueError("ambiguous local datetime because of daylight saving time")
    return first if first_valid else second


def _parse_date(value, rule):
    """Convert a date to YYYY-MM-DD or a datetime to ISO 8601 UTC."""
    if isinstance(value, datetime | date):
        date_value = value
    elif isinstance(value, str):
        date_value = _read_date_text(value, rule)
    else:
        raise ValueError("invalid date or datetime")

    if rule.type == "date":
        # A date has no timezone. Only keep the calendar day.
        if isinstance(date_value, datetime):
            date_value = date_value.date()
        return date_value.isoformat()
    if not isinstance(date_value, datetime):
        raise ValueError("datetime needs a time value")
    if date_value.tzinfo is None:
        date_value = _localize_datetime(date_value, rule.source_timezone)
    return date_value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_number(value, rule):
    if isinstance(value, bool):
        raise ValueError("invalid number")
    if isinstance(value, str) and rule.decimal_comma and "," in value and "." not in value:
        value = value.replace(",", ".")
    try:
        value = float(value)
    except (ValueError, TypeError, OverflowError) as error:
        raise ValueError("invalid number") from error
    if not math.isfinite(value):
        raise ValueError("number must be finite")
    return value


def _parse_boolean(value):
    if isinstance(value, bool):
        return value
    # Reject arbitrary integers such as 2, but accept 0 and 1.
    if isinstance(value, int) and value not in (0, 1):
        raise ValueError("invalid boolean")
    text = str(value).casefold()
    if text in TRUE_VALUES:
        return True
    if text in FALSE_VALUES:
        return False
    raise ValueError("invalid boolean")


def _convert_type(value, rule):
    """Convert the value before applying categories and validation limits."""
    if rule.type == "string":
        if not isinstance(value, str):
            raise ValueError("must be text")
        return value
    if rule.type == "integer":
        if isinstance(value, bool) or not re.fullmatch(r"[+-]?\d+", str(value)):
            raise ValueError("invalid integer")
        return int(value)
    if rule.type == "number":
        return _parse_number(value, rule)
    if rule.type == "boolean":
        return _parse_boolean(value)
    if rule.type in ("date", "datetime"):
        return _parse_date(value, rule)
    if rule.type == "email":
        if not isinstance(value, str) or not re.fullmatch(EMAIL_PATTERN, value):
            raise ValueError("invalid email")
        return value.casefold()
    raise ValueError("unknown field type: " + str(rule.type))


def _validate_value(value, rule):
    """Check allowed values and numeric bounds after category mapping."""
    if rule.allowed_values is not None and value not in rule.allowed_values:
        raise ValueError("value not in allowed values")
    if rule.min_value is not None or rule.max_value is not None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("number limits require a numeric field")
        if rule.min_value is not None and value < rule.min_value:
            raise ValueError("value is below minimum")
        if rule.max_value is not None and value > rule.max_value:
            raise ValueError("value is above maximum")


def _convert(value, rule):
    """Convert a field into the type listed in its FieldRule."""
    value = _convert_type(value, rule)
    # Categories can have French and English names for the same value.
    if rule.categories and isinstance(value, str):
        mapping = {str(name).casefold(): standard for name, standard in rule.categories.items()}
        value = mapping.get(value.casefold(), value)
    # The caller keeps and flags the original value if validation fails.
    _validate_value(value, rule)
    return value
