# AI contribution: 50% or more AI-generated
"""Unsaved organization records for the Tier 2 tests."""

from uuid import UUID

from app.models import SourceRecord
from app.processing.fuzzy_config import load_fuzzy_config
from app.processing.fuzzy_profile import profile

TENANT = UUID(int=100)
EXCLUDED = frozenset({"gmail.com"})
CONFIG = load_fuzzy_config()


def record(number, name, *, tenant=TENANT, city=None, phones=(), emails=(), **fields):
    return SourceRecord(
        **(
            {
                "id": UUID(int=number),
                "tenant_id": tenant,
                "kind": "organization",
                "external_id": str(number),
                "external_ids": {},
                "name_normalized": name,
                "city": city,
                "attributes": {
                    "identifiers": {"phones": list(phones), "emails": list(emails)},
                    "cities": [city] if city else [],
                },
            }
            | fields
        )
    )


def profiles(records, config=CONFIG):
    return [profile(item, config, EXCLUDED) for item in records]
