# AI contribution: 50% or more AI-generated
"""Records mapped from any source, and the cleaning they all go through before storage."""

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from typing import Any

import polars as pl

from app.processing.cleaning import (
    DUPLICATE_IN_BATCH_REASON,
    clean_scalar_batch,
    record_fingerprint,
)
from app.processing.cleaning_config import get_cleaning_config

logger = logging.getLogger(__name__)

MISSING_REASON = "required field is missing"


class MappingError(ValueError):
    """The payload lacks a field the record cannot exist without."""


@dataclass(frozen=True)
class MappedRecord:
    """The columns of a `source_record` version that come from the payload."""

    kind: str
    external_id: str
    workspace_id: str | None
    name_normalized: str | None
    domain: str | None = None
    linkedin: str | None = None
    city: str | None = None
    external_ids: dict[str, list[str]] = field(default_factory=dict)
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MappingFailure:
    entity_kind: str
    external_id: str
    reason: str


@dataclass(frozen=True)
class MappingResult:
    records: tuple[MappedRecord, ...]
    failures: tuple[MappingFailure, ...]
    cleaning_flag_counts: dict[str, int] = field(default_factory=dict)


# AI helped me suggest better ways of coding or traversings loops I had.
# Ai completed the function after the records were cleaned by recycling old logic and
# making it work with the new logic.
def clean_mapped(
    mapped: Iterable[MappedRecord], failures: Iterable[MappingFailure] = ()
) -> MappingResult:
    """Clean the scalar columns of each record and drop exact repeats within the batch.

    A record that loses a required field is added to `failures` after the given ones.
    """
    # Materializes the iterable into a list so we can access records by index and traverse them
    records = list(mapped)
    # Copies the supplied mapping failures into a mutable list. Cleaning failures will be appended
    # without modifying the callers collection.
    failed = list(failures)
    # Starts the collection of final retained records. It remains empty if there are no valid
    # candidates.
    cleaned: list[MappedRecord] = []
    # Stores how many times each cleaning problem occurred:
    flag_counts: dict[str, int] = {}
    # Will associate each original record position with its cleaning result
    by_index = {}
    # Associate each entity kind with the positions of its records.
    groups: dict[str, list[int]] = {}
    # Group records by kind to apply their own cleaning configurations
    for index, record in enumerate(records):
        groups.setdefault(record.kind, []).append(index)
    # Get the cleaning configurations for each kind (organisation or people)
    for kind, indices in groups.items():
        config = get_cleaning_config(kind)
        # Get the data
        values = [
            {name: getattr(records[index], name) for name in config.fields} for index in indices
        ]
        # Clean the whole group using Polars and connect back to their original indices using zip()
        # (AI suggested this line of code)
        for index, result in zip(indices, clean_scalar_batch(values, config), strict=True):
            by_index[index] = result
    # Initilialize cleaned MappedRecords that are now duplicate candidates
    candidates = []
    # Hold a fingerprint for each candiadate
    fingerprints = []
    # Now that records are clean, decide which cleaned records can be returned and remove dupes
    for index in reversed(range(len(records))):
        record = records[index]
        value, flags = by_index[index]
        for flag in flags:
            reason = flag["reason"]
            flag_counts[reason] = flag_counts.get(reason, 0) + 1
        missing = [flag["field"] for flag in flags if flag["reason"] == MISSING_REASON]
        if missing:
            reason = "cleaning removed required fields: " + ", ".join(missing)
            logger.warning("Could not clean %s %s: %s", record.kind, record.external_id, reason)
            failed.append(MappingFailure(record.kind, record.external_id, reason))
            continue
        fingerprint = record_fingerprint(
            {
                **value,
                "attributes": record.attributes,
                "external_ids": record.external_ids,
            }
        )
        fingerprints.append(fingerprint)
        candidates.append(replace(record, **value))
    if fingerprints:
        # Candidates are reversed, so the first fingerprint is the last source row.
        fingerprint_column = pl.Series("fingerprint", fingerprints, dtype=pl.String)
        keep = fingerprint_column.is_first_distinct()
        flag_counts[DUPLICATE_IN_BATCH_REASON] = len(candidates) - keep.sum()
        if not flag_counts[DUPLICATE_IN_BATCH_REASON]:
            del flag_counts[DUPLICATE_IN_BATCH_REASON]
        cleaned = [record for record, retain in zip(candidates, keep, strict=True) if retain]
    return MappingResult(tuple(reversed(cleaned)), tuple(failed), flag_counts)
