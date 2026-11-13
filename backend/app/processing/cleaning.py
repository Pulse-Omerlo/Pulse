# AI contribution: Below 50% AI-generated
"""
Clean PULSE data after mapping and before storage.

The original raw payload must be archived before mapping and cleaning.
This module only cleans mapped records and does not handle raw data storage.

The cleaner handles missing values, whitespace, Unicode, common encoding
problems, numbers, booleans, dates, emails, categories, and duplicate records.
Problems are flagged with the original value instead of silently deleting data.

The caller supplies existing records for historical duplicate checks.
This module does not connect to a database by itself.
"""

import hashlib
import json
import math
import unicodedata
from dataclasses import dataclass, field

import polars as pl

from app.processing.cleaning_columns import BROKEN_ENCODING, convert_strings, text_plan
from app.processing.cleaning_values import FALSE_VALUES, TRUE_VALUES
from app.processing.cleaning_values import _convert as _convert

DUPLICATE_IN_BATCH_REASON = "duplicate in current batch"

MISSING_VALUES = {"", "n/a", "na", "null", "none", "unknown", "-"}


@dataclass
class FieldRule:
    """Rules used to clean one field."""

    type: str = "string"
    required: bool = False
    min_value: float | None = None
    max_value: float | None = None
    allowed_values: tuple | None = None
    categories: dict = field(default_factory=dict)
    input_formats: tuple = ()
    source_timezone: str | None = None
    decimal_comma: bool = False


@dataclass
class CleaningConfig:
    """Cleaning rules for a specific kind of record."""

    fields: dict = field(default_factory=dict)
    unique_by: tuple = ()
    keep_extra_fields: bool = True
    remove_duplicate_records: bool = True


@dataclass
class CleaningResult:
    """Cleaned records, duplicates, problem flags, and counts."""

    cleaned: list = field(default_factory=list)
    duplicates: list = field(default_factory=list)
    flags: list = field(default_factory=list)
    unprocessed: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    flag_counts: dict = field(default_factory=dict)


def _fix_encoding(text):
    """Try to repair common UTF-8 text decoded using the wrong encoding."""

    if not any(marker in text for marker in ("Ã", "Â", "â€", "â€™", "â€œ", "â€“")):
        return text

    for encoding in ("cp1252", "latin1"):
        try:
            fixed = text.encode(encoding).decode("utf-8")
        except UnicodeError:
            continue

        # Only change it when the result looks less corrupted.
        bad_before = sum(text.count(marker) for marker in ("Ã", "Â", "â€"))
        bad_after = sum(fixed.count(marker) for marker in ("Ã", "Â", "â€"))
        if bad_after < bad_before:
            return fixed

    return text


def _clean_text(value):
    """Clean whitespace, Unicode, and common missing-value strings."""

    value = unicodedata.normalize("NFC", value)
    value = _fix_encoding(value)
    value = unicodedata.normalize("NFC", value)
    value = " ".join(value.split())

    if value.casefold() in MISSING_VALUES:
        return None

    return value


def _basic_clean(value):
    """Clean normal and nested values without changing the input object."""

    if isinstance(value, str):
        return _clean_text(value)

    if isinstance(value, list):
        cleaned_list = []
        for item in value:
            cleaned_list.append(_basic_clean(item))
        return cleaned_list

    if isinstance(value, dict):
        cleaned_dict = {}
        for name, item in value.items():
            cleaned_dict[name] = _basic_clean(item)
        return cleaned_dict

    if isinstance(value, float) and not math.isfinite(value):
        return None

    return value


def _make_flag(index, name, original, reason):
    """Create one report describing a problem with a record."""

    return {
        "record_index": index,
        "field": name,
        "original_value": original,
        "reason": reason,
    }


def _prepare_text_columns(records, names):
    """Gather strings for Polars and track values that must stay in Python."""
    strings = {}
    exceptional = set()
    for name in names:
        values = []
        for index, record in enumerate(records):
            raw = record.get(name)
            if not isinstance(raw, str):
                values.append(None)
            elif any(0xD800 <= ord(char) <= 0xDFFF for char in raw):
                # Arrow cannot represent lone surrogates; retain Python handling.
                exceptional.add((index, name))
                values.append(None)
            else:
                if BROKEN_ENCODING.search(raw):
                    raw = _fix_encoding(unicodedata.normalize("NFC", raw))
                values.append(raw)
        strings[name] = values
    return strings, exceptional


def _column_inputs(records, name, values, rule, exceptional):
    """Route strings and representable scalar inputs to native expressions."""
    inputs, eligible = [], []
    for index, record in enumerate(records):
        raw = record.get(name)
        supported = (raw is None or isinstance(raw, str)) and (index, name) not in exceptional
        value = values[index]
        if rule and rule.type in ("integer", "number", "boolean"):
            numeric = type(raw) in (int, float, bool)
            if numeric and not (isinstance(raw, float) and not math.isfinite(raw)):
                value, supported = str(raw), True
        inputs.append(value)
        eligible.append(supported)
    return inputs, eligible


def _clean_column(records, name, values, rule, exceptional):
    """Assemble native outputs and flags; Python handles only unsupported cases."""
    inputs, eligible = _column_inputs(records, name, values, rule, exceptional)
    native = convert_strings(inputs, rule, TRUE_VALUES, FALSE_VALUES) if rule else None
    native_rows = native.to_dicts() if native is not None else [None] * len(records)
    results = []
    for index, original in enumerate(records):
        if name not in original and rule is None:
            continue
        raw = original.get(name)
        ordinary_string = isinstance(raw, str) and (index, name) not in exceptional
        value = values[index] if ordinary_string else _basic_clean(raw)
        row = native_rows[index]
        reason = None
        output = value
        if row is not None and eligible[index] and row["handled"]:
            reason = row["reason"]
            output = value if reason else row["converted"]
        elif rule is not None:
            if value is None:
                reason = "required field is missing" if rule.required else None
            else:
                try:
                    output = _convert(value, rule)
                except ValueError as error:
                    reason = str(error)
        flags = [_make_flag(index, name, raw, reason)] if reason else []
        results.append((index, output, flags))
    return results


def clean_scalar_batch(records, config):
    """Clean Python dictionaries and return one (cleaned, flags) pair per row.

    Called by clean_mapped and clean_records. Only string candidates enter Polars;
    other Python values retain their types. This function does not deduplicate.
    """
    if not records:
        return []
    names = list(config.fields)
    if config.keep_extra_fields:
        names = list(dict.fromkeys(name for record in records for name in record))
        names = list(dict.fromkeys([*names, *config.fields]))
    cleaned = [{} for _ in records]
    flags = [[] for _ in records]
    strings, exceptional = _prepare_text_columns(records, names)
    normalized = {}
    if strings:
        normalization_plan = text_plan(strings, MISSING_VALUES)
        normalized_frame = normalization_plan.collect(engine="streaming")
        normalized = normalized_frame.to_dict(as_series=False)
    for name in names:
        rule = config.fields.get(name)
        for index, value, field_flags in _clean_column(
            records, name, normalized[name], rule, exceptional
        ):
            cleaned[index][name] = value
            flags[index].extend(field_flags)
    # Preserve config field order even when extra input columns have another order.
    positions = {name: position for position, name in enumerate(config.fields)}
    for row_flags in flags:
        row_flags.sort(key=lambda flag: positions[flag["field"]])
    return list(zip(cleaned, flags, strict=True))


def record_fingerprint(record, fields=None):
    """Make a fingerprint of the full record or just chosen fields."""

    if fields is None:
        data = record
    else:
        data = {}
        for name in fields:
            data[name] = record.get(name)

    text = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _can_compare(record, flags, fields):
    """Avoid using invalid or missing values to decide two records match."""

    if fields is None:
        return len(flags) == 0

    for name in fields:
        if record.get(name) is None:
            return False
        for flag in flags:
            if flag["field"] == name:
                return False
    return True


def remove_duplicates(new_records, existing_records=None, fields=None):
    """Standalone duplicate check for already cleaned dictionaries.

    The caller should pass cleaned records for accurate comparisons.
    Returns unique new records and separate duplicate records.
    """

    seen = set()
    unique = []
    duplicates = []

    for record in existing_records or []:
        if isinstance(record, dict) and _can_compare(record, [], fields):
            seen.add(record_fingerprint(record, fields))

    for record in new_records:
        if not isinstance(record, dict) or not _can_compare(record, [], fields):
            unique.append(record)
            continue
        fingerprint = record_fingerprint(record, fields)
        if fingerprint in seen:
            duplicates.append(record)
        else:
            unique.append(record)
            seen.add(fingerprint)

    return unique, duplicates


def _historical_fingerprints(existing_records, config, fields):
    """Prepare old records using the same cleaning rules as new ones."""
    historical = set()
    if not config.remove_duplicate_records:
        return historical
    dictionaries = [old for old in existing_records or [] if isinstance(old, dict)]
    for cleaned, flags in clean_scalar_batch(dictionaries, config):
        if _can_compare(cleaned, flags, fields):
            historical.add(record_fingerprint(cleaned, fields))
    return historical


def _flag_missing_duplicate_key(original, cleaned, fields, index, result):
    if fields is None:
        return
    missing = [name for name in fields if cleaned.get(name) is None]
    if missing:
        result.flags.append(
            _make_flag(
                index,
                ",".join(missing),
                {name: original.get(name) for name in missing},
                "missing duplicate key",
            )
        )


def _summarize_cleaning(result, received):
    """Store all counts in one place so the sync service can persist them."""
    for flag in result.flags:
        reason = flag["reason"]
        result.flag_counts[reason] = result.flag_counts.get(reason, 0) + 1
    result.stats = {
        "received": received,
        "cleaned": len(result.cleaned),
        "duplicates": len(result.duplicates),
        "duplicate_in_batch": result.flag_counts.get(DUPLICATE_IN_BATCH_REASON, 0),
        "duplicate_historical": result.flag_counts.get("duplicate of historical record", 0),
        "unprocessed": len(result.unprocessed),
        "flag_count": len(result.flags),
        "flagged_records": len(set(flag["record_index"] for flag in result.flags)),
    }


def clean_records(records, config, existing_records=None):
    """Clean a batch, keeping flagged records and reporting duplicates separately."""
    result = CleaningResult()
    fields = config.unique_by if config.unique_by else None
    historical = _historical_fingerprints(existing_records, config, fields)
    dictionaries = [record for record in records if isinstance(record, dict)]
    batch = clean_scalar_batch(dictionaries, config)
    fingerprints = [
        record_fingerprint(row, fields)
        if config.remove_duplicate_records and _can_compare(row, flags, fields)
        else None
        for row, flags in batch
    ]
    fingerprint_column = pl.Series("fingerprint", fingerprints, dtype=pl.String)
    first = fingerprint_column.is_first_distinct()
    batches = iter(zip(batch, fingerprints, first, strict=True))
    for index, original in enumerate(records):
        if not isinstance(original, dict):
            flag = _make_flag(index, "__record__", original, "record must be a dictionary")
            result.flags.append(flag)
            result.unprocessed.append({"index": index, "record": original})
            continue
        (cleaned, flags), fingerprint, first_occurrence = next(batches)
        for flag in flags:
            flag["record_index"] = index
        result.flags.extend(flags)
        if config.remove_duplicate_records:
            if _can_compare(cleaned, flags, fields):
                reason = (
                    "duplicate of historical record"
                    if fingerprint in historical
                    else DUPLICATE_IN_BATCH_REASON
                    if not first_occurrence
                    else None
                )
                if reason is not None:
                    result.flags.append(_make_flag(index, "__record__", original, reason))
                    result.duplicates.append({"index": index, "record": cleaned, "reason": reason})
                    continue
            else:
                _flag_missing_duplicate_key(original, cleaned, fields, index, result)
        result.cleaned.append(cleaned)
    _summarize_cleaning(result, len(records))
    return result
