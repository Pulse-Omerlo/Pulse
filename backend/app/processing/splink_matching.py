# AI contribution: 50% or more AI-generated
"""Match organizations with a Splink probabilistic model (dev only, compared with Tier 1 and 2)."""

import logging
from collections import defaultdict

# Splink is a dev dependency, not installed in the app image. Only dev commands such as
# app.splink_report may import this module; the API and the sync pipeline must not.
import splink.comparison_library as cl
from splink import DuckDBAPI, Linker, SettingsCreator, block_on

from app.processing.tier1_accuracy import make_pair

# Fields Splink compares. Each one is a column of the rows built below.
MATCH_FIELDS = ("name_normalized", "city", "domain", "linkedin")
NAME_PREFIX = "substr(name_normalized, 1, 3)"

# Pairs at or above this match probability count as the same organization.
DEFAULT_THRESHOLD = 0.9

# Assumed share of records that have a duplicate. Splink needs a starting guess for how
# likely two random records are to match; the usual way to estimate it uses exact rules,
# which find nothing when duplicates lost their identifiers.
DUPLICATE_SHARE = 0.1
SAMPLING_SEED = 490


def splink_rows(records):
    """One row per source record in the shape Splink reads. Blank fields become None."""
    rows = []
    for record in records:
        row = {"unique_id": record.external_id, "tenant_id": str(record.tenant_id)}
        for field in MATCH_FIELDS:
            # Splink treats None as missing, but an empty string as a real value.
            row[field] = getattr(record, field) or None
        rows.append(row)
    return rows


def prior(row_count):
    """Chance that two random records match: DUPLICATE_SHARE matches over all pairs."""
    return min(1.0, 2 * DUPLICATE_SHARE / (row_count - 1))


def model_settings(row_count):
    """Comparisons and blocking rules. Every blocking rule keeps pairs inside one tenant."""
    return SettingsCreator(
        link_type="dedupe_only",
        probability_two_random_records_match=prior(row_count),
        comparisons=[
            cl.JaroWinklerAtThresholds("name_normalized", [0.95, 0.88]),
            cl.ExactMatch("city"),
            cl.ExactMatch("domain"),
            cl.ExactMatch("linkedin"),
        ],
        blocking_rules_to_generate_predictions=[
            block_on("tenant_id", "domain"),
            block_on("tenant_id", "linkedin"),
            block_on("tenant_id", "city"),
            block_on("tenant_id", NAME_PREFIX),
        ],
    )


def train(linker):
    """Learn how often each field agrees by chance (u) and for real matches (m)."""
    linker.training.estimate_u_using_random_sampling(max_pairs=1e5, seed=SAMPLING_SEED)
    # Each session fixes the fields it blocks on, so two sessions cover every field.
    linker.training.estimate_parameters_using_expectation_maximisation(
        block_on("tenant_id", "city")
    )
    linker.training.estimate_parameters_using_expectation_maximisation(
        block_on("tenant_id", NAME_PREFIX)
    )


def splink_pairs(records, threshold=DEFAULT_THRESHOLD):
    """Pairs of external ids that Splink matches with at least `threshold` probability."""
    # Random sampling in train() ignores the blocking rules, so a model trained on
    # several tenants would learn from pairs across tenants. Each tenant gets its own.
    by_tenant = defaultdict(list)
    for record in records:
        by_tenant[record.tenant_id].append(record)
    pairs = set()
    for tenant_records in by_tenant.values():
        pairs |= tenant_pairs(tenant_records, threshold)
    return pairs


def tenant_pairs(records, threshold):
    """Train a model on one tenant's records and return the pairs it matches."""
    rows = splink_rows(records)
    if len(rows) < 2:
        return set()
    data = DuckDBAPI().register(rows)
    linker = Linker(data, model_settings(len(rows)), log_level=logging.WARNING)
    train(linker)
    predictions = linker.inference.predict(threshold_match_probability=threshold)
    rows = predictions.as_record_list()
    return {make_pair(row["unique_id_l"], row["unique_id_r"]) for row in rows}
