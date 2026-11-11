# AI contribution: 50% or more AI-generated
"""Print how well Tier 1, then Tier 1 plus Tier 2, find the synthetic duplicates (dev only)."""

import argparse
from dataclasses import replace
from uuid import UUID

from app.core.logging import configure_logging, get_logger
from app.ingestion.mapped import clean_mapped
from app.ingestion.omerlo.mapping.mappers import map_record
from app.processing.entity_resolution import blocked_matches, load_generic_domains
from app.processing.fuzzy_config import load_fuzzy_config
from app.processing.tier1_accuracy import combine, expected_pairs, predicted_pairs, score
from app.processing.tier2_accuracy import (
    RECORD_FIELDS,
    grouping,
    organization_record,
    tier_groupings,
)
from app.processing.tier2_hard_variant import hard_variant
from synthetic.omerlo_fixtures import DEFAULT_SEED, OmerloFixtureGenerator

HEADER = "DEV ONLY: synthetic data, not the official evaluation."
TIER2_COUNTS = ("linked", "review", "pairs_left_out")
SWEEP_THRESHOLDS = (0.80, 0.85, 0.86, 0.87, 0.88, 0.89, 0.90, 0.95)

logger = get_logger(__name__)


def build_records(tenant_number, tenant_data):
    """Map one tenant's synthetic organizations to unsaved source records."""
    mapped = []
    for payload in tenant_data.resources["organizations"]:
        metadata = tenant_data.metadata.get(payload["id"], [])
        mapped.append(map_record("organization", payload, metadata=metadata))
    cleaned = clean_mapped(mapped)

    return [
        organization_record(
            UUID(int=tenant_number * 100000 + number),
            UUID(int=tenant_number),
            **{name: getattr(record, name) for name in RECORD_FIELDS},
        )
        for number, record in enumerate(cleaned.records, start=1)
    ]


def tier1_groups(records):
    """Groups of external ids that Tier 1 would link to one organization."""
    # The same plan as the matcher, so conflicting links are cut here too.
    found = grouping(records, blocked_matches(records, load_generic_domains()))
    return [set(group) for group in found.groups]


def score_tenant(records, expected, config):
    """Tier 1 alone, then Tier 1 plus Tier 2, against the same expected pairs."""
    tier1, both = tier_groupings(records, config)
    return score(expected, predicted_pairs(tier1.groups)), {
        **score(expected, predicted_pairs(both.groups)),
        "linked": both.tier2_linked,
        "review": both.tier2_review,
        "pairs_left_out": both.pairs_left_out,
    }


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
    counts = {key: sum(item[key] for item in scores) for key in TIER2_COUNTS}
    return {**total, **counts}


def log_score(tenant, result, event="tier1_score", **fields):
    extra = {key: result[key] for key in TIER2_COUNTS if key in result} | fields
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
        log_wrong_pairs(slug, tier1)
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
        help="respell each duplicate and drop its identifiers, and add near-miss negatives",
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
