# AI contribution: 50% or more AI-generated
"""Tier 2: propose organizations whose normalized names are alike but not equal."""

from collections import defaultdict
from collections.abc import Callable, Collection, Iterable, Sequence
from dataclasses import dataclass, field
from uuid import UUID

from rapidfuzz import fuzz

from app.models import SourceRecord
from app.processing.fuzzy_blocking import BlockingStats, candidate_pairs
from app.processing.fuzzy_config import FuzzyConfig, load_fuzzy_config
from app.processing.fuzzy_profile import Profile, compare_signals, name_profile, profile
from app.processing.match_rules import FUZZY_NAME_RULE, FUZZY_RULE, MatchCandidateData, Pair

Scorer = Callable[[str, str], float]

# Measured against ratio, token_set_ratio and WRatio on the synthetic duplicates.
SCORER: Scorer = fuzz.token_sort_ratio


@dataclass(frozen=True)
class FuzzyResult:
    matches: tuple[MatchCandidateData, ...] = ()
    comparisons: int = 0
    blocking: BlockingStats = field(default_factory=BlockingStats)

    def __add__(self, other: "FuzzyResult") -> "FuzzyResult":
        return FuzzyResult(
            self.matches + other.matches,
            self.comparisons + other.comparisons,
            self.blocking + other.blocking,
        )


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


# Which agreeing signals can confirm each kind of name difference. A shared city links
# only names whose distinctive words are the same: one letter, word order or an added
# ending in a distinctive word is often another business in the same city.
CONFIRMED_BY = {
    "generic": frozenset({"city", "phone", "email_domain"}),
    "spelling": frozenset({"phone", "email_domain"}),
    "order": frozenset({"phone", "email_domain"}),
    # Denis and Denise Cote: relatives often share a business phone, not a domain.
    "ending": frozenset({"email_domain"}),
}


def _stem(word: str) -> str:
    """The word without a plural s or x."""
    return word[:-1] if len(word) > 3 and word[-1] in "sx" else word


def name_difference(left: Profile, right: Profile) -> str:
    """How the distinctive words of two names differ.

    generic: they are the same, so only generic words, plurals or articles differ.
    order: the same words in another order. ending: a word with letters added at its
    end (Denis, Denise). spelling: anything else.
    """
    ours = [_stem(word) for word in left.distinctive]
    theirs = [_stem(word) for word in right.distinctive]
    if ours == theirs:
        return "generic"
    if sorted(ours) == sorted(theirs):
        return "order"
    only_ours = [word for word in ours if word not in theirs]
    only_theirs = [word for word in theirs if word not in ours]
    if any(a.startswith(b) or b.startswith(a) for a in only_ours for b in only_theirs):
        return "ending"
    return "spelling"


def decide(left: Profile, right: Profile, similar: float) -> MatchCandidateData:
    """A link needs an agreeing signal strong enough for the name difference, and no
    conflict; otherwise the pair waits for review."""
    agreeing, conflicts = compare_signals(left.signals, right.signals)
    difference = name_difference(left, right)
    confirmed = any(field in CONFIRMED_BY[difference] for field, _ in agreeing)
    rule = FUZZY_RULE if confirmed and not conflicts else FUZZY_NAME_RULE
    return MatchCandidateData(
        left=left.id,
        right=right.id,
        rule=rule,
        fields=(("name_similarity", f"{similar:.3f}"), ("name_difference", difference), *agreeing),
        score=similar,
        sides=(("name_normalized", left.name, right.name), *conflicts),
    )


def _tenant_matches(
    profiles: Sequence[Profile], known_pairs: Collection[Pair], config: FuzzyConfig
) -> FuzzyResult:
    by_id = {item.id: item for item in profiles}
    blocking = candidate_pairs(profiles, config)
    matches = []
    comparisons = 0
    for pair in sorted(blocking.pairs):
        left, right = by_id[pair[0]], by_id[pair[1]]
        # Equal names are Tier 1's name rules.
        if pair in known_pairs or left.name == right.name:
            continue
        comparisons += 1
        similar = score(left, right)
        if similar >= config.threshold:
            matches.append(decide(left, right, similar))
    return FuzzyResult(tuple(matches), comparisons, blocking.stats)


def fuzzy_matches(
    records: Iterable[SourceRecord],
    excluded_domains: frozenset[str],
    known_pairs: Collection[Pair] = frozenset(),
    config: FuzzyConfig | None = None,
) -> FuzzyResult:
    """Tier 2 matches between current organization records, skipping known_pairs.

    known_pairs are ordered (smaller id first), like the pairs Tier 1 classified.
    """
    config = config or load_fuzzy_config()
    tenants: dict[UUID, list[Profile]] = defaultdict(list)
    for record in records:
        if record.kind == "organization" and record.valid_to is None and record.name_normalized:
            tenants[record.tenant_id].append(profile(record, config, excluded_domains))
    result = FuzzyResult()
    for tenant_id in sorted(tenants):
        result += _tenant_matches(tenants[tenant_id], known_pairs, config)
    return result
