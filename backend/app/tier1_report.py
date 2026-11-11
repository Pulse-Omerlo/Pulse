# AI contribution: 50% or more AI-generated
"""Print how well Tier 1, then Tier 1 plus Tier 2, find the synthetic duplicates (dev only)."""

import argparse
from dataclasses import replace
from uuid import UUID

from app.core.logging import configure_logging, get_logger
from app.ingestion.mapped import clean_mapped
from app.ingestion.omerlo.mapping.mappers import map_record
from app.models import SourceRecord
from app.processing.clustering import member_from_record, plan_clusters
from app.processing.entity_resolution import blocked_matches, load_generic_domains
from app.processing.fuzzy_config import load_fuzzy_config
from app.processing.tier1_accuracy import combine, expected_pairs, predicted_pairs, score
from app.processing.tier2_accuracy import hard_variant, tier2_grouping
from synthetic.omerlo_fixtures import DEFAULT_SEED, OmerloFixtureGenerator

HEADER = "DEV ONLY: synthetic data, not the official evaluation."
SWEEP_THRESHOLDS = (0.80, 0.85, 0.86, 0.87, 0.88, 0.89, 0.90, 0.95)

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
    generic_domains = load_generic_domains()
    members = {record.id: member_from_record(record, generic_domains) for record in records}
    # The same plan as the matcher, so conflicting links are cut here too.
    plan = plan_clusters(members, blocked_matches(records, generic_domains), {})
    return [{by_id[record_id].external_id for record_id in c.members} for c in plan.clusters]


def score_tenant(records, expected, config):
    """Tier 1 alone, then Tier 1 plus Tier 2, against the same expected pairs."""
    tier1_alone = tier1_groups(records)
    grouping = tier2_grouping(records, tier1_alone, config)
    tier1 = score(expected, predicted_pairs(tier1_alone))
    both = score(expected, predicted_pairs(grouping.groups))
    return tier1, {**both, "linked": grouping.tier2_linked, "review": grouping.tier2_review}


def tenant_datasets(seed, hard, config):
    """(slug, records, expected pairs) for each synthetic tenant."""
    datasets = []
    tenants = OmerloFixtureGenerator(seed).generate()
    for tenant_number, tenant_data in enumerate(tenants, start=1):
        records = build_records(tenant_number, tenant_data)
        if hard:
            records = hard_variant(records, tenant_data.ground_truth, seed, config.generic_tokens)
        datasets.append(
            (tenant_data.tenant.slug, records, expected_pairs(tenant_data.ground_truth))
        )
    return datasets


def combine_tier2(scores):
    total = combine(scores)
    linked = sum(tenant_score["linked"] for tenant_score in scores)
    review = sum(tenant_score["review"] for tenant_score in scores)
    return {**total, "linked": linked, "review": review}


def log_score(tenant, result, event="tier1_score", **fields):
    extra = {key: result[key] for key in ("linked", "review") if key in result} | fields
    logger.info(
        event,
        tenant=tenant,
        precision=round(result["precision"], 3),
        recall=round(result["recall"], 3),
        f1=round(result["f1"], 3),
        true_positives=result["true_positives"],
        false_positives=result["false_positives"],
        false_negatives=result["false_negatives"],
        **extra,
    )


def log_wrong_pairs(tenant, result, tier="tier1"):
    for pair in result["false_positive_pairs"]:
        logger.warning(f"{tier}_false_positive", tenant=tenant, left=pair[0], right=pair[1])
    for pair in result["false_negative_pairs"]:
        logger.warning(f"{tier}_false_negative", tenant=tenant, left=pair[0], right=pair[1])


def report(datasets, config):
    """Each tenant and the total, Tier 1 and Tier 1 plus Tier 2 one after the other."""
    tier1_scores, tier2_scores = [], []
    for slug, records, expected in datasets:
        tier1, both = score_tenant(records, expected, config)
        tier1_scores.append(tier1)
        tier2_scores.append(both)
        log_score(slug, tier1)
        log_score(slug, both, "tier2_score")
        log_wrong_pairs(slug, both, "tier2")
    log_score("all", combine(tier1_scores))
    log_score("all", combine_tier2(tier2_scores), "tier2_score")


def sweep(datasets, config):
    """Tier 1 plus Tier 2 over all tenants at each threshold."""
    for threshold in SWEEP_THRESHOLDS:
        candidate = replace(config, threshold=threshold)
        scores = [score_tenant(records, truth, candidate)[1] for _, records, truth in datasets]
        log_score("all", combine_tier2(scores), "tier2_threshold", threshold=threshold)


def main(argv=None):
    parser = argparse.ArgumentParser(description=HEADER)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="fixture seed")
    parser.add_argument(
        "--hard",
        action="store_true",
        help="remove the shared identifiers of each duplicate and respell its name",
    )
    parser.add_argument("--sweep", action="store_true", help="score each Tier 2 threshold")
    args = parser.parse_args(argv)
    configure_logging()
    config = load_fuzzy_config()

    logger.info(
        "tier1_report_started",
        notice=HEADER,
        seed=args.seed,
        variant="hard" if args.hard else "standard",
        threshold=config.threshold,
    )
    datasets = tenant_datasets(args.seed, args.hard, config)
    if args.sweep:
        sweep(datasets, config)
    else:
        report(datasets, config)


if __name__ == "__main__":
    main()
