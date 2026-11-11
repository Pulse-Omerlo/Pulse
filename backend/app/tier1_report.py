# AI contribution: 50% or more AI-generated
"""Print how well Tier 1 matching finds the synthetic duplicates (dev only)."""

import argparse
from uuid import UUID

from app.core.logging import configure_logging, get_logger
from app.ingestion.mapped import clean_mapped
from app.ingestion.omerlo.mapping.mappers import map_record
from app.models import SourceRecord
from app.processing.clustering import member_from_record, plan_clusters
from app.processing.entity_resolution import blocked_matches, load_generic_domains
from app.processing.person_resolution import (
    blocked_person_matches,
    components,
    conflicting_person_identifiers,
)
from app.processing.tier1_accuracy import (
    ORGANIZATION_DUPLICATES,
    PERSON_DUPLICATES,
    combine,
    expected_pairs,
    predicted_pairs,
    score,
)
from synthetic.omerlo_fixtures import DEFAULT_SEED, OmerloFixtureGenerator

HEADER = "DEV ONLY: synthetic data, not the official evaluation."

logger = get_logger(__name__)


def build_organization_records(tenant_number, tenant_data):
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


def build_person_records(tenant_number, tenant_data):
    """Map one tenant's synthetic people to unsaved source records."""
    mapped = []
    for payload in tenant_data.resources["people"]:
        metadata = tenant_data.metadata.get(payload["id"], [])
        mapped.append(map_record("person", payload, metadata=metadata))
    cleaned = clean_mapped(mapped)

    # Offset ids so people never collide with organization UUID ints in the same tenant.
    records = []
    for number, record in enumerate(cleaned.records, start=1):
        records.append(
            SourceRecord(
                id=UUID(int=tenant_number * 100000 + 50000 + number),
                tenant_id=UUID(int=tenant_number),
                source_id=UUID(int=tenant_number),
                kind="person",
                external_id=record.external_id,
                external_ids=record.external_ids,
                attributes=record.attributes,
                name_normalized=record.name_normalized,
                linkedin=record.linkedin,
            )
        )
    return records


def tier1_groups(records):
    """Groups of external ids that Tier 1 would link to one organization."""
    by_id = {record.id: record for record in records}
    generic_domains = load_generic_domains()
    members = {record.id: member_from_record(record, generic_domains) for record in records}
    # The same plan as the matcher, so conflicting links are cut here too.
    plan = plan_clusters(members, blocked_matches(records, generic_domains), {})
    return [{by_id[record_id].external_id for record_id in c.members} for c in plan.clusters]


def tier1_person_groups(records):
    """Groups of external ids that Tier 1 would link to one person."""
    by_id = {record.id: record for record in records}
    matches = blocked_person_matches(records)
    groups = []
    for group in components(matches):
        group_records = [by_id[record_id] for record_id in group]
        if conflicting_person_identifiers(group_records):
            continue
        groups.append({record.external_id for record in group_records})
    return groups


def score_organizations(tenant_number, tenant_data):
    records = build_organization_records(tenant_number, tenant_data)
    expected = expected_pairs(tenant_data.ground_truth, key=ORGANIZATION_DUPLICATES)
    predicted = predicted_pairs(tier1_groups(records))
    return score(expected, predicted)


def score_people(tenant_number, tenant_data):
    records = build_person_records(tenant_number, tenant_data)
    expected = expected_pairs(tenant_data.ground_truth, key=PERSON_DUPLICATES)
    predicted = predicted_pairs(tier1_person_groups(records))
    return score(expected, predicted)


def log_score(tenant, result, *, kind="organization"):
    logger.info(
        "tier1_score",
        tenant=tenant,
        kind=kind,
        precision=round(result["precision"], 3),
        recall=round(result["recall"], 3),
        f1=round(result["f1"], 3),
        true_positives=result["true_positives"],
        false_positives=result["false_positives"],
        false_negatives=result["false_negatives"],
    )


def log_wrong_pairs(tenant, result, *, kind="organization"):
    for pair in result["false_positive_pairs"]:
        logger.warning(
            "tier1_false_positive",
            tenant=tenant,
            kind=kind,
            left=pair[0],
            right=pair[1],
        )
    for pair in result["false_negative_pairs"]:
        logger.warning(
            "tier1_false_negative",
            tenant=tenant,
            kind=kind,
            left=pair[0],
            right=pair[1],
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description=HEADER)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="fixture seed")
    args = parser.parse_args(argv)
    configure_logging()

    logger.info("tier1_report_started", notice=HEADER, seed=args.seed)

    org_scores = []
    people_scores = []
    tenants = OmerloFixtureGenerator(args.seed).generate()
    for tenant_number, tenant_data in enumerate(tenants, start=1):
        slug = tenant_data.tenant.slug
        org_result = score_organizations(tenant_number, tenant_data)
        people_result = score_people(tenant_number, tenant_data)
        org_scores.append(org_result)
        people_scores.append(people_result)
        log_score(slug, org_result, kind="organization")
        log_wrong_pairs(slug, org_result, kind="organization")
        log_score(slug, people_result, kind="person")
        log_wrong_pairs(slug, people_result, kind="person")

    log_score("all", combine(org_scores), kind="organization")
    log_score("all", combine(people_scores), kind="person")


if __name__ == "__main__":
    main()
