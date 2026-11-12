# AI contribution: 50% or more AI-generated
"""A harder copy of the synthetic data for the dev-only report: duplicates Tier 1 cannot
find, and distinct organizations with near-miss names that must stay apart."""

import random
import string
from collections.abc import Sequence
from itertools import pairwise
from uuid import UUID

from app.models import SourceRecord
from app.processing.normalization import normalize_organization_name
from app.processing.tier2_accuracy import copy_record, organization_record

# Name tokens at least this long get the typo, so short words stay readable.
TYPO_MIN_TOKEN_LENGTH = 5
# Share of hard duplicates that keep the phone of the record they duplicate.
SAME_PHONE_SHARE = 0.5
# Negative record numbers start here inside each tenant's id range.
NEGATIVE_ID_OFFSET = 50_000

# Synthetic names only. Real, distinct surnames one or two letters apart.
NEAR_MISS_SURNAMES = (
    ("martin", "marin"),
    ("couture", "couturier"),
    ("bouchard", "boucher"),
    ("gagnon", "gagne"),
    ("pelletier", "peltier"),
    ("gauthier", "gautier"),
    ("fortin", "fortier"),
    ("girard", "girardin"),
    ("lavoie", "lavoix"),
    ("bernier", "bernie"),
)
# A first name and its feminine form: often two relatives with two businesses.
GENDERED_FIRST_NAMES = (
    ("denis", "denise"),
    ("paul", "paule"),
    ("louis", "louise"),
    ("daniel", "danielle"),
    ("jean", "jeanne"),
    ("michel", "michele"),
    ("francois", "francoise"),
    ("martin", "martine"),
)
SURNAMES = ("tremblay", "cote", "roy", "gagnon", "morin", "lavoie", "fortin", "gauthier")
BRANCHES = (("nord", "sud"), ("est", "ouest"), ("limoilou", "lebourgneuf"))
TRADES = ("boulangerie", "pharmacie", "garage", "clinique", "imprimerie", "quincaillerie")


def _typo(name: str, rng: random.Random) -> str:
    """One deleted, swapped, replaced or added letter in a long word of the name."""
    tokens = name.split()
    long_tokens = [i for i, token in enumerate(tokens) if len(token) >= TYPO_MIN_TOKEN_LENGTH]
    longest = max(range(len(tokens)), key=lambda i: len(tokens[i]))
    index = rng.choice(long_tokens) if long_tokens else longest
    token = tokens[index]
    at = rng.randrange(1, len(token)) if len(token) > 1 else 0
    letter = rng.choice([c for c in string.ascii_lowercase if c != token[at]])
    edits = {
        "delete": token[:at] + token[at + 1 :],
        "swap": token[: at - 1] + token[at] + token[at - 1] + token[at + 1 :] if at else token,
        "replace": token[:at] + letter + token[at + 1 :],
        "insert": token[:at] + letter + token[at:],
    }
    edited = edits[rng.choice(sorted(edits))]
    # Swapping two equal letters changes nothing.
    tokens[index] = edited if edited != token else edits["replace"]
    return " ".join(tokens)


def name_variant(name: str, generic: frozenset[str], rng: random.Random) -> str:
    """A typo, the first word moved to the end, or a generic word left out."""
    tokens = name.split()
    droppable = [i for i, token in enumerate(tokens) if token in generic]
    change = rng.choice(("typo", "typo", "reorder", "drop_generic"))
    if change == "reorder" and len(tokens) > 1:
        return " ".join([*tokens[1:], tokens[0]])
    if change == "drop_generic" and droppable and len(tokens) > 1:
        dropped = rng.choice(droppable)
        return " ".join(token for i, token in enumerate(tokens) if i != dropped)
    return _typo(name, rng)


def _phones(record: SourceRecord) -> list[str]:
    identifiers = (record.attributes or {}).get("identifiers") or {}
    return list(identifiers.get("phones") or [])


def _hard_duplicate(
    record: SourceRecord, original: SourceRecord | None, seed: int, generic: frozenset[str]
) -> SourceRecord:
    rng = random.Random(f"{seed}:{record.external_id}")
    attributes = dict(record.attributes or {})
    identifiers = dict(attributes.get("identifiers") or {})
    identifiers.update(emails=[], domains=[], linkedin=[])
    if original is not None and rng.random() < SAME_PHONE_SHARE:
        identifiers["phones"] = _phones(original)
    return copy_record(
        record,
        attributes={**attributes, "identifiers": identifiers},
        name_normalized=name_variant(record.name_normalized, generic, rng),
        domain=None,
        linkedin=None,
        external_ids={},
    )


def _negative_names(rng: random.Random) -> list[tuple[str, str, str, bool]]:
    """(kind, first name, second name, same phone) for each near-miss pair."""
    pairs = []
    for first, second in NEAR_MISS_SURNAMES:
        trade = rng.choice(TRADES)
        pairs.append(("surname", f"{trade} {first}", f"{trade} {second}", False))
    for index, (first, second) in enumerate(GENDERED_FIRST_NAMES):
        trade, surname = rng.choice(TRADES), rng.choice(SURNAMES)
        family_phone = index % 2 == 0
        pairs.append(
            (
                "gendered",
                f"{trade} {first} {surname}",
                f"{trade} {second} {surname}",
                family_phone,
            )
        )
    for first, second in pairwise(SURNAMES):
        pairs.append(("swapped", f"{first} {second} avocats", f"{second} {first} avocats", False))
    for first, second in BRANCHES:
        trade, surname = rng.choice(TRADES), rng.choice(SURNAMES)
        pairs.append(("branch", f"{trade} {surname} {first}", f"{trade} {surname} {second}", False))
    return pairs


def near_miss_negatives(records: Sequence[SourceRecord], seed: int) -> list[SourceRecord]:
    """Pairs of distinct organizations in one city whose names are almost the same.

    None of them is in the ground truth, so every pair the matcher makes with them is a
    false positive.
    """
    if not records:
        return []
    rng = random.Random(f"{seed}:negatives")
    tenant_id = records[0].tenant_id
    base = tenant_id.int * 100_000 + NEGATIVE_ID_OFFSET
    cities = sorted({record.city for record in records if record.city})
    taken = {record.name_normalized for record in records}
    negatives = []
    for number, (kind, first, second, same_phone) in enumerate(_negative_names(rng)):
        city = rng.choice(cities)
        # A name the tenant already has would be a Tier 1 match, not a near miss.
        if {normalize_organization_name(first), normalize_organization_name(second)} & taken:
            continue
        phones = [f"+1418555{rng.randrange(10_000):04d}" for _ in range(2)]
        for side, name in enumerate((first, second)):
            phone = phones[0] if same_phone else phones[side]
            negatives.append(
                organization_record(
                    UUID(int=base + 2 * number + side),
                    tenant_id,
                    external_id=f"negative-{kind}-{number}-{side}",
                    external_ids={},
                    attributes={"cities": [city], "identifiers": {"phones": [phone]}},
                    name_normalized=normalize_organization_name(name),
                    city=city,
                )
            )
    return negatives


def hard_variant(
    records: Sequence[SourceRecord], ground_truth: dict, seed: int, generic: frozenset[str]
) -> list[SourceRecord]:
    """Copies where each duplicate shares no identifier and its name is spelled
    differently, plus the near-miss negatives."""
    duplicates = {
        d["record_ids"][1]: d["record_ids"][0] for d in ground_truth["organization_duplicates"]
    }
    by_external_id = {record.external_id: record for record in records}
    result = []
    for record in records:
        if record.external_id in duplicates and record.name_normalized:
            original = by_external_id.get(duplicates[record.external_id])
            result.append(_hard_duplicate(record, original, seed, generic))
        else:
            result.append(copy_record(record))
    return result + near_miss_negatives(records, seed)
