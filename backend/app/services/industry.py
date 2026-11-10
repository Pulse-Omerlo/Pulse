# AI contribution: 50% or more AI-generated
"""Classify a tenant's current organization records into NAICS sectors."""

import logging
import os
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ingestion.omerlo.mapping.fields import html_text
from app.models import IndustryClassification, SourceRecord, Tenant
from app.processing.industry import (
    CategoryMapping,
    Classification,
    classify,
    load_category_mapping,
    rules_version,
)

logger = logging.getLogger(__name__)

# One <omerlo_media_id or tenant id>.toml per network; see the README in this folder.
DEFAULT_MAPPINGS_DIR = Path(__file__).parents[1] / "processing" / "industry_mappings"


@dataclass
class ClassificationReport:
    classified: int = 0
    unclassified: int = 0
    # Category id -> number of organizations carrying it that the mapping does not list.
    unmapped_categories: Counter[str] = field(default_factory=Counter)


def mappings_dir() -> Path:
    return Path(os.environ.get("INDUSTRY_MAPPINGS_DIR", DEFAULT_MAPPINGS_DIR))


def mapping_for(tenant: Tenant) -> CategoryMapping:
    return load_category_mapping(mappings_dir() / f"{tenant.omerlo_media_id or tenant.id}.toml")


def classify_organizations(session: Session, tenant_id: uuid.UUID) -> ClassificationReport:
    """Classify current organization versions that have no result for the current rules.

    Runs inside the caller's transaction, in a session bound to `tenant_id`.
    """
    tenant = session.get_one(Tenant, tenant_id)
    mapping = mapping_for(tenant)
    version = rules_version(mapping)
    up_to_date = select(IndustryClassification.source_record_id).where(
        IndustryClassification.rules_version == version
    )
    records = session.scalars(
        select(SourceRecord).where(
            SourceRecord.kind == "organization",
            SourceRecord.valid_to.is_(None),
            SourceRecord.id.not_in(up_to_date),
        )
    ).all()

    report = ClassificationReport()
    for record in records:
        name, description, category_ids = classification_inputs(record)
        result = classify(
            name=name, description=description, category_ids=category_ids, mapping=mapping
        )
        store(session, record, result, version)
        report.unmapped_categories.update(result.unmapped_categories)
        if result.classified:
            report.classified += 1
        else:
            report.unclassified += 1
    session.flush()

    if report.unmapped_categories:
        logger.warning(
            "categories missing from the industry mapping",
            extra={"tenant_id": str(tenant_id), "categories": dict(report.unmapped_categories)},
        )
    return report


def store(session: Session, record: SourceRecord, result: Classification, version: str) -> None:
    """Insert the version's result, or replace one made with older rules."""
    row = session.get(IndustryClassification, record.id)
    if row is None:
        row = IndustryClassification(source_record_id=record.id, tenant_id=record.tenant_id)
        session.add(row)
    row.status = "classified" if result.classified else "unclassified"
    row.primary_sector = result.primary
    row.sectors = [
        {"code": match.code, "method": match.method, "evidence": list(match.evidence)}
        for match in result.sectors
    ]
    row.reason = result.reason
    row.rules_version = version
    row.classified_at = func.now()


def classification_inputs(record: SourceRecord) -> tuple[str | None, str | None, list[str]]:
    """Name, description text and category ids from an Omerlo or file import record."""
    attributes = record.attributes or {}
    name = attributes.get("name") if isinstance(attributes.get("name"), str) else None
    profile = attributes.get("profile") if isinstance(attributes.get("profile"), dict) else {}
    category_ids = [
        entry["category_id"]
        for entry in _dicts(profile.get("profile_categories"))
        if isinstance(entry.get("category_id"), str)
    ]
    texts = [
        block.get("content_text") or html_text(block.get("content_html"))
        for description in _dicts(attributes.get("description"))
        if not description.get("deleted_at")
        for block in _dicts(description.get("blocks"))
    ]
    texts += [
        html_text(locale.get("summary_html"))
        for locale in _dicts(attributes.get("locales"))
        if not locale.get("deleted_at")
    ]
    description = " ".join(text for text in texts if isinstance(text, str) and text.strip())
    return name or record.name_normalized, description or None, category_ids


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
