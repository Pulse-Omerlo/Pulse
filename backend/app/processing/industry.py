# AI contribution: 50% or more AI-generated
"""Place organizations in NAICS sectors from their categories, name and description.

Pure functions: callers read the inputs from source records and store the result.
"""

from __future__ import annotations

import hashlib
import re
import tomllib
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app.processing.normalization import normalize_text

_SECTORS_PATH = Path(__file__).with_name("naics_sectors.toml")
_KEYWORDS_PATH = Path(__file__).with_name("industry_keywords.toml")

METHOD_CATEGORY = "category"
METHOD_KEYWORD = "keyword"

REASON_NO_INPUT = "no categories, name or description to classify from"
REASON_NO_MATCH = "no mapped category and no keyword matched"

# A keyword found in the name says more about the organization than one in its description.
NAME_WEIGHT = 2
DESCRIPTION_WEIGHT = 1


@dataclass(frozen=True)
class Sector:
    code: str
    en: str
    fr: str


@dataclass(frozen=True)
class CategoryMapping:
    """One network's own categories mapped to NAICS sectors.

    A category listed with no sectors is known and deliberately left out (for example
    "News"); a category missing from the mapping is reported as unmapped.
    """

    sectors_by_category: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    version: str = "none"


@dataclass(frozen=True)
class SectorMatch:
    code: str
    method: str
    # What produced the match: {"field": "category" | "name" | "description", "value": ...}
    evidence: tuple[dict[str, str], ...]


@dataclass(frozen=True)
class Classification:
    sectors: tuple[SectorMatch, ...]
    primary: str | None
    reason: str | None
    unmapped_categories: tuple[str, ...]

    @property
    def classified(self) -> bool:
        return self.primary is not None


@lru_cache(maxsize=1)
def sectors() -> dict[str, Sector]:
    with _SECTORS_PATH.open("rb") as handle:
        data = tomllib.load(handle)
    return {
        str(code): Sector(code=str(code), en=names["en"], fr=names["fr"])
        for code, names in data["sectors"].items()
    }


@lru_cache(maxsize=1)
def keyword_rules() -> dict[str, tuple[str, ...]]:
    """Normalized keywords by sector code; a keyword for an unknown sector is an error."""
    with _KEYWORDS_PATH.open("rb") as handle:
        data = tomllib.load(handle)
    known = sectors()
    rules: dict[str, tuple[str, ...]] = {}
    for code, words in data["keywords"].items():
        code = str(code)
        if code not in known:
            raise ValueError(f"unknown sector {code!r} in {_KEYWORDS_PATH.name}")
        normalized = {normalize_text(word) for word in words}
        if None in normalized:
            raise ValueError(f"empty keyword for sector {code} in {_KEYWORDS_PATH.name}")
        rules[code] = tuple(sorted(normalized))
    return rules


@lru_cache(maxsize=1)
def _keyword_patterns() -> dict[str, tuple[tuple[str, re.Pattern[str]], ...]]:
    return {
        code: tuple((word, re.compile(rf"\b{re.escape(word)}[sx]?\b")) for word in words)
        for code, words in keyword_rules().items()
    }


def rules_version(mapping: CategoryMapping) -> str:
    """Changes when the sectors, the keywords or the network's mapping change."""
    digest = hashlib.sha256()
    for path in (_SECTORS_PATH, _KEYWORDS_PATH):
        digest.update(path.read_bytes())
    digest.update(mapping.version.encode())
    return digest.hexdigest()[:16]


def load_category_mapping(path: Path) -> CategoryMapping:
    """Read a network's ``[categories."<id>"]`` tables; a missing file maps nothing."""
    if not path.exists():
        return CategoryMapping()
    raw = path.read_bytes()
    data = tomllib.loads(raw.decode())
    known = sectors()
    mapped: dict[str, tuple[str, ...]] = {}
    for category_id, entry in data.get("categories", {}).items():
        codes = entry.get("sectors") if isinstance(entry, dict) else None
        if not isinstance(codes, list) or any(str(code) not in known for code in codes):
            raise ValueError(f"invalid sectors for category {category_id!r} in {path.name}")
        mapped[category_id] = tuple(str(code) for code in codes)
    return CategoryMapping(sectors_by_category=mapped, version=hashlib.sha256(raw).hexdigest())


def classify(
    *,
    name: str | None,
    description: str | None,
    category_ids: Iterable[str],
    mapping: CategoryMapping,
) -> Classification:
    """Categories decide first; keywords only apply when no category maps to a sector."""
    categories = list(dict.fromkeys(category_ids))
    unmapped = tuple(c for c in categories if c not in mapping.sectors_by_category)

    by_category = _from_categories(categories, mapping)
    if by_category:
        return Classification(
            sectors=tuple(by_category),
            primary=by_category[0].code,
            reason=None,
            unmapped_categories=unmapped,
        )

    by_keyword = _from_keywords(name, description)
    if by_keyword:
        return Classification(
            sectors=tuple(by_keyword),
            primary=by_keyword[0].code,
            reason=None,
            unmapped_categories=unmapped,
        )

    has_input = bool(categories) or bool(normalize_text(name)) or bool(normalize_text(description))
    return Classification(
        sectors=(),
        primary=None,
        reason=REASON_NO_MATCH if has_input else REASON_NO_INPUT,
        unmapped_categories=unmapped,
    )


def _from_categories(categories: list[str], mapping: CategoryMapping) -> list[SectorMatch]:
    """Sectors ordered by how many categories point to them, then by first appearance."""
    evidence: dict[str, list[dict[str, str]]] = {}
    for category_id in categories:
        for code in mapping.sectors_by_category.get(category_id, ()):
            evidence.setdefault(code, []).append({"field": "category", "value": category_id})
    order = list(evidence)
    ranked = sorted(order, key=lambda code: (-len(evidence[code]), order.index(code)))
    return [SectorMatch(code, METHOD_CATEGORY, tuple(evidence[code])) for code in ranked]


def _from_keywords(name: str | None, description: str | None) -> list[SectorMatch]:
    """Sectors ordered by weighted keyword hits, then by sector code, so ties are stable."""
    texts = {"name": normalize_text(name) or "", "description": normalize_text(description) or ""}
    weights = {"name": NAME_WEIGHT, "description": DESCRIPTION_WEIGHT}
    scores: Counter[str] = Counter()
    evidence: dict[str, list[dict[str, str]]] = {}
    for code, patterns in _keyword_patterns().items():
        for source, text in texts.items():
            for word, pattern in patterns:
                if text and pattern.search(text):
                    scores[code] += weights[source]
                    evidence.setdefault(code, []).append({"field": source, "value": word})
    ranked = sorted(scores, key=lambda code: (-scores[code], code))
    return [SectorMatch(code, METHOD_KEYWORD, tuple(evidence[code])) for code in ranked]
