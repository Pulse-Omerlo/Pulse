# AI contribution: 50% or more AI-generated
"""Score Tier 1 matches against the synthetic ground truth (dev only)."""

from itertools import combinations


def make_pair(first_id, second_id):
    """Put two record ids in the same order so a pair is never counted twice."""
    if first_id < second_id:
        return (first_id, second_id)
    return (second_id, first_id)


def expected_pairs(ground_truth):
    """Duplicate pairs listed in one tenant's ground truth."""
    pairs = set()
    for duplicate in ground_truth.get("organization_duplicates", []):
        first_id, second_id = duplicate["record_ids"]
        pairs.add(make_pair(first_id, second_id))
    return pairs


def predicted_pairs(groups):
    """Every pair of records that Tier 1 put in the same group."""
    pairs = set()
    for group in groups:
        # Sorting first means each pair already comes out in order.
        for pair in combinations(sorted(group), 2):
            pairs.add(pair)
    return pairs


def precision(true_positives, false_positives):
    """Share of predicted pairs that are real duplicates."""
    predicted = true_positives + false_positives
    if predicted == 0:
        return 0.0
    return true_positives / predicted


def recall(true_positives, false_negatives):
    """Share of real duplicates that Tier 1 found."""
    real = true_positives + false_negatives
    if real == 0:
        return 0.0
    return true_positives / real


def f1(precision_value, recall_value):
    """Harmonic mean of precision and recall."""
    if precision_value + recall_value == 0:
        return 0.0
    return 2 * precision_value * recall_value / (precision_value + recall_value)


def score(expected, predicted):
    """Compare the predicted pairs of one tenant with its expected pairs."""
    false_positives = sorted(predicted - expected)
    false_negatives = sorted(expected - predicted)
    return make_score(
        len(predicted & expected),
        false_positives,
        false_negatives,
    )


def combine(scores):
    """Add up the counts of every tenant, then compute the metrics again."""
    false_positives = []
    false_negatives = []
    true_positives = 0
    for tenant_score in scores:
        true_positives += tenant_score["true_positives"]
        false_positives += tenant_score["false_positive_pairs"]
        false_negatives += tenant_score["false_negative_pairs"]
    return make_score(true_positives, false_positives, false_negatives)


def make_score(true_positives, false_positives, false_negatives):
    precision_value = precision(true_positives, len(false_positives))
    recall_value = recall(true_positives, len(false_negatives))
    return {
        "true_positives": true_positives,
        "false_positives": len(false_positives),
        "false_negatives": len(false_negatives),
        "precision": precision_value,
        "recall": recall_value,
        "f1": f1(precision_value, recall_value),
        "false_positive_pairs": false_positives,
        "false_negative_pairs": false_negatives,
    }
