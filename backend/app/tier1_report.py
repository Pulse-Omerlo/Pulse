# AI contribution: 50% or more AI-generated
"""Print how well Tier 1 matching finds the synthetic duplicates (dev only)."""

import argparse
from uuid import UUID

from app.core.logging import configure_logging, get_logger
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

logger = get_logger(__name__)


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


def log_score(tenant, result):
    logger.info(
        "tier1_score",
        tenant=tenant,
        precision=round(result["precision"], 3),
        recall=round(result["recall"], 3),
        f1=round(result["f1"], 3),
        true_positives=result["true_positives"],
        false_positives=result["false_positives"],
        false_negatives=result["false_negatives"],
    )


def log_wrong_pairs(tenant, result):
    for pair in result["false_positive_pairs"]:
        logger.warning("tier1_false_positive", tenant=tenant, left=pair[0], right=pair[1])
    for pair in result["false_negative_pairs"]:
        logger.warning("tier1_false_negative", tenant=tenant, left=pair[0], right=pair[1])


def main(argv=None):
    parser = argparse.ArgumentParser(description=HEADER)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="fixture seed")
    args = parser.parse_args(argv)
    configure_logging()

    logger.info("tier1_report_started", notice=HEADER, seed=args.seed)

    scores = []
    tenants = OmerloFixtureGenerator(args.seed).generate()
    for tenant_number, tenant_data in enumerate(tenants, start=1):
        result = score_tenant(tenant_number, tenant_data)
        scores.append(result)
        log_score(tenant_data.tenant.slug, result)
        log_wrong_pairs(tenant_data.tenant.slug, result)

    log_score("all", combine(scores))


if __name__ == "__main__":
    main()
