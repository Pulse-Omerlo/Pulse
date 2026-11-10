# AI contribution: 50% or more AI-generated
"""Print how well Tier 1 matching finds the synthetic duplicates (dev only)."""

import argparse
from uuid import UUID

from app.ingestion.mapped import clean_mapped
from app.ingestion.omerlo.mapping.mappers import map_record
from app.models import SourceRecord
from app.processing.entity_resolution import (
    blocked_matches,
    components,
    conflicting_identifiers,
    load_generic_domains,
)
from app.processing.tier1_accuracy import combine, expected_pairs, predicted_pairs, score
from synthetic.omerlo_fixtures import DEFAULT_SEED, OmerloFixtureGenerator

HEADER = "DEV ONLY: synthetic data, not the official evaluation."


def build_records(tenant_number, tenant_data):
    """Map one tenant's synthetic organizations to unsaved source records."""
    mapped = []
    for payload in tenant_data.resources["organizations"]:
        metadata = tenant_data.metadata.get(payload["id"], [])
        mapped.append(map_record("organization", payload, metadata=metadata))
    cleaned = clean_mapped(mapped)

    records = []
    for number, record in enumerate(cleaned.records, start=1):
        records.append(
            SourceRecord(
                id=UUID(int=tenant_number * 100000 + number),
                tenant_id=UUID(int=tenant_number),
                kind="organization",
                external_id=record.external_id,
                external_ids=record.external_ids,
                attributes=record.attributes,
                name_normalized=record.name_normalized,
                domain=record.domain,
                linkedin=record.linkedin,
                city=record.city,
            )
        )
    return records


def tier1_groups(records):
    """Groups of external ids that Tier 1 would link to one organization."""
    by_id = {record.id: record for record in records}
    matches = blocked_matches(records, load_generic_domains())
    groups = []
    for group in components(matches):
        group_records = [by_id[record_id] for record_id in group]
        # Tier 1 sends a group with conflicting identifiers to review instead of linking it.
        if conflicting_identifiers(group_records):
            continue
        groups.append({record.external_id for record in group_records})
    return groups


def score_tenant(tenant_number, tenant_data):
    records = build_records(tenant_number, tenant_data)
    expected = expected_pairs(tenant_data.ground_truth)
    predicted = predicted_pairs(tier1_groups(records))
    return score(expected, predicted)


def print_score(title, result, show_pairs):
    print(title)  # noqa: T201
    print(  # noqa: T201
        f"  precision {result['precision']:.3f}  recall {result['recall']:.3f}"
        f"  F1 {result['f1']:.3f}"
    )
    print(  # noqa: T201
        f"  true positives {result['true_positives']}"
        f"  false positives {result['false_positives']}"
        f"  false negatives {result['false_negatives']}"
    )
    if show_pairs:
        for pair in result["false_positive_pairs"]:
            print(f"  false positive: {pair[0]} {pair[1]}")  # noqa: T201
        for pair in result["false_negative_pairs"]:
            print(f"  false negative: {pair[0]} {pair[1]}")  # noqa: T201


def main(argv=None):
    parser = argparse.ArgumentParser(description=HEADER)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="fixture seed")
    args = parser.parse_args(argv)

    print(HEADER)  # noqa: T201
    print(f"Seed {args.seed}. Each tenant is scored on its own.")  # noqa: T201
    print()  # noqa: T201

    scores = []
    tenants = OmerloFixtureGenerator(args.seed).generate()
    for tenant_number, tenant_data in enumerate(tenants, start=1):
        result = score_tenant(tenant_number, tenant_data)
        scores.append(result)
        print_score(f"Tenant {tenant_data.tenant.slug}", result, show_pairs=True)
        print()  # noqa: T201

    print_score("All tenants", combine(scores), show_pairs=False)


if __name__ == "__main__":
    main()
