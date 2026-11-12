# AI contribution: 50% or more AI-generated
"""Turn generated detail records into create payloads the backoffice API accepts."""

from typing import Any

from synthetic.pipeline.target import Target

# Prefixes the backoffice API requires on social links, without a scheme.
SOCIAL_PREFIXES = {
    "linkedin": ("www.linkedin.com/", "linkedin.com/"),
    "facebook": ("www.facebook.com/", "facebook.com/"),
    "instagram": ("www.instagram.com/", "instagram.com/"),
    "twitter": ("www.twitter.com/", "twitter.com/", "www.x.com/", "x.com/"),
    "bluesky": ("bsky.app/",),
}
# Nested fields the server fills in itself.
SERVER_FIELDS = ("id", "deleted_at")


class PayloadError(ValueError):
    """A payload would be refused by the API; caught before it is sent."""


def organization_payload(record: dict[str, Any], target: Target) -> dict[str, Any]:
    payload = {
        "name": record["name"],
        "profile": profile(target.organization_type, target),
        "contact": [contact(entry) for entry in record["contact"]],
        "address": [strip(entry) for entry in record["address"]],
        "description": [description(entry) for entry in record["description"]],
        "locales": [strip(entry) for entry in record["locales"]],
    }
    check(payload, record["name"])
    return payload


def person_payload(record: dict[str, Any], target: Target) -> dict[str, Any]:
    payload = {
        "first_name": record["first_name"],
        "last_name": record["last_name"],
        "other_name": record["other_name"],
        "pronoun": record["pronoun"],
        "profile": profile(target.person_type, target),
        "contact": [contact(entry) for entry in record["contact"]],
        "address": [strip(entry) for entry in record["address"]],
        "locales": [strip(entry) for entry in record["locales"]],
    }
    check(payload, f"{record['first_name']} {record['last_name']}")
    return payload


def profile(profile_type: str, target: Target) -> dict[str, Any]:
    # Categories are left out: the generator's category ids do not exist in the target.
    return {"profile_type_id": profile_type, "status_id": target.completed_status}


def strip(entry: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in entry.items() if key not in SERVER_FIELDS}


def contact(entry: dict[str, Any]) -> dict[str, Any]:
    cleaned = strip(entry)
    for field in SOCIAL_PREFIXES:
        value = cleaned.get(field)
        if value:
            cleaned[field] = value.removeprefix("https://").removeprefix("http://")
    return cleaned


def description(entry: dict[str, Any]) -> dict[str, Any]:
    blocks = [
        {key: value for key, value in block.items() if value is not None}
        for block in entry["blocks"]
    ]
    return {**strip(entry), "blocks": blocks}


def check(payload: dict[str, Any], label: str) -> None:
    for entry in payload["contact"]:
        for field, prefixes in SOCIAL_PREFIXES.items():
            value = entry.get(field)
            if value and not value.startswith(prefixes):
                raise PayloadError(f"{label}: {field} must start with {prefixes[0]}")
