# AI contribution: 50% or more AI-generated
"""Print how Splink compares with Tier 1 and Tier 1 plus Tier 2 on synthetic data (dev only)."""

import argparse

from app.core.logging import configure_logging, get_logger
from app.processing.fuzzy_config import load_fuzzy_config
from app.processing.splink_matching import DEFAULT_THRESHOLD, splink_pairs
from app.processing.tier1_accuracy import combine, score
from app.tier1_report import HEADER, log_score, score_tenant, tenant_datasets
from synthetic.omerlo_fixtures import DEFAULT_SEED

METHODS = ("splink", "tier1", "tier2")

logger = get_logger(__name__)


def compare(datasets, config, threshold):
    """Score Splink and both rule tiers per tenant, log them, and return the totals."""
    scores = {method: [] for method in METHODS}
    for slug, records, expected in datasets:
        tier1, tier2 = score_tenant(records, expected, config)
        splink = score(expected, splink_pairs(records, threshold))
        for method, result in zip(METHODS, (splink, tier1, tier2), strict=True):
            scores[method].append(result)
            log_score(slug, result, f"{method}_score", kind="organization")

    totals = {method: combine(results) for method, results in scores.items()}
    for method, total in totals.items():
        log_score("all", total, f"{method}_score", kind="organization")
    return totals


def main(argv=None):
    parser = argparse.ArgumentParser(description=HEADER)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="fixture seed")
    parser.add_argument(
        "--hard",
        action="store_true",
        help="respell each duplicate and drop its identifiers, and add near-miss negatives",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help="lowest Splink match probability that counts as a match",
    )
    args = parser.parse_args(argv)
    if not 0 < args.threshold <= 1:
        parser.error("--threshold must be above 0 and at most 1")
    configure_logging()
    config = load_fuzzy_config()

    logger.info(
        "splink_report_started",
        notice=HEADER,
        seed=args.seed,
        variant="hard" if args.hard else "standard",
        threshold=args.threshold,
    )
    compare(tenant_datasets(args.seed, args.hard, config), config, args.threshold)


if __name__ == "__main__":
    main()
