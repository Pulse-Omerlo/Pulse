# AI contribution: 50% or more AI-generated
"""Tier 2: propose organizations whose normalized names are alike but not equal."""

from collections import defaultdict
from collections.abc import Callable, Collection, Iterable, Sequence
from uuid import UUID

from rapidfuzz import fuzz

from app.models import SourceRecord
from app.processing.fuzzy_blocking import candidate_pairs
from app.processing.fuzzy_config import FuzzyConfig, load_fuzzy_config
from app.processing.fuzzy_profile import Profile, compare_signals, name_profile, profile
from app.processing.match_rules import FUZZY_NAME_RULE, FUZZY_RULE, MatchCandidateData, Pair

Scorer = Callable[[str, str], float]

# Measured against ratio, token_set_ratio and WRatio on the synthetic duplicates.
SCORER: Scorer = fuzz.token_sort_ratio


def _agreement(ours: Sequence[str], theirs: Sequence[str]) -> float:
    """How well each of our distinctive words is spelled somewhere in theirs (worst word)."""
    return min(max(fuzz.ratio(word, other) for other in theirs) for word in ours)


def score(left: Profile, right: Profile, scorer: Scorer = SCORER) -> float:
    """0 to 1: the whole-name score, capped by the worst-matched distinctive word.

    Names whose numbers differ, or made only of generic words, score 0.
    """
    if left.numbers != right.numbers or not left.distinctive or not right.distinctive:
        return 0.0
    words = min(
        _agreement(left.distinctive, right.words), _agreement(right.distinctive, left.words)
    )
    return min(scorer(left.name, right.name), words) / 100


def similarity(left: str, right: str, config: FuzzyConfig) -> float:
    """The score of two normalized names."""
    return score(name_profile(left, config), name_profile(right, config))


def decide(left: Profile, right: Profile, similar: float) -> MatchCandidateData:
    """A link only with an agreeing signal and no conflict; otherwise a review."""
    agreeing, conflicts = compare_signals(left.signals, right.signals)
    rule = FUZZY_RULE if agreeing and not conflicts else FUZZY_NAME_RULE
    return MatchCandidateData(
        left=left.id,
        right=right.id,
        rule=rule,
        fields=(("name_similarity", f"{similar:.3f}"), *agreeing),
        score=similar,
        sides=(("name_normalized", left.name, right.name), *conflicts),
    )


def _tenant_matches(
    profiles: Sequence[Profile], known_pairs: Collection[Pair], config: FuzzyConfig
) -> list[MatchCandidateData]:
    by_id = {item.id: item for item in profiles}
    matches = []
    for pair in sorted(candidate_pairs(profiles, config).pairs):
        left, right = by_id[pair[0]], by_id[pair[1]]
        # Equal names are Tier 1's name rules.
        if pair in known_pairs or left.name == right.name:
            continue
        similar = score(left, right)
        if similar >= config.threshold:
            matches.append(decide(left, right, similar))
    return matches


def fuzzy_matches(
    records: Iterable[SourceRecord],
    excluded_domains: frozenset[str],
    known_pairs: Collection[Pair] = frozenset(),
    config: FuzzyConfig | None = None,
) -> list[MatchCandidateData]:
    """Tier 2 matches between current organization records, skipping known_pairs.

    known_pairs are ordered (smaller id first), like the pairs Tier 1 classified.
    """
    config = config or load_fuzzy_config()
    tenants: dict[UUID, list[Profile]] = defaultdict(list)
    for record in records:
        if record.kind == "organization" and record.valid_to is None and record.name_normalized:
            tenants[record.tenant_id].append(profile(record, config, excluded_domains))
    matches = []
    for tenant_id in sorted(tenants):
        matches += _tenant_matches(tenants[tenant_id], known_pairs, config)
    return matches
