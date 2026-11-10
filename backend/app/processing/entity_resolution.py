from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cache
from itertools import combinations
from pathlib import Path
import tomllib
from uuid import UUID, uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.tenancy import bound_to_tenant
from app.models import MatchCandidate, Organization, OrgLink, SourceRecord, Tenant
from app.models.resolution import RuleEvidence

# Naming the version of the matching rules used to make a decision
# If we need to change matching behavior later, we can create a new version and keep the old one for reference
RULE_VERSION = "tier1-v1"

# Priority of strong matching rules (Left = Highest priotity, Right = Lowest priority)
# Uses the highest priority rule that matches to make a decision, and ignores any lower priority rules that also match
RULE_PRIORITIES = ("domain", "linkedin", "external_id", "name_city")

# Dictionnary assigning a weight to each matching rule, used to compute a score for each match candidate
RULE_WEIGHTS = {
    "domain": 1.0,
    "linkedin": 1.0,
    "external_id": 1.0,
    "name_city": 0.95,
    "name_only": 0.5,
}

# Get the domains from the config file
GENERIC_DOMAINS_PATH = Path(__file__).with_name("generic_domains.toml")

# Tupple containing the two record version IDs
Pair = tuple[UUID, UUID]
# Blocking key for a match candidate, used to group match candidates by the fields that matched
Key = tuple[str, ...]
# Inner tupples containing the field name and value for each matching field
Fields = tuple[tuple[str, str], ...]


# Class for a match candidate, containing the two record version IDs and the matching rule that matched them
@dataclass(frozen=True)
class MatchCandidateData:
    left: UUID
    right: UUID
    rule: str
    fields: Fields

    @property
    def providePair(self) -> Pair:
        """Return the pair of record version IDs in a consistent order."""
        return (self.left, self.right) if self.left < self.right else (self.right, self.left)

    # Distinguish rules that are strong enough to be used for automatic resolution from those that are not
    # If other weak rules are added in the future, this property can be updated to reflect that
    @property
    def strong(self) -> bool:
        return self.rule != "name_only"


# Class for the result of a match resolution, containing the number of organizations, links, and candidates created
# Rerunning the matching on unchanged data should not create new organizations, links, or candidates, so these counts should be zero if the matching is idempotent
@dataclass
class MatchResult:
    # Counts new organizations.
    organizations_created: int = 0
    # Counts new links between organizations.
    links_created: int = 0
    # Counts new pairs set aside for review.
    candidates_created: int = 0


# Function to load the generic domains from the config file, used to filter out matches on generic domains
@cache
def load_generic_domains() -> frozenset[str]:
    with GENERIC_DOMAINS_PATH.open("rb") as handle:
        values = tomllib.load(handle).get("domains")
    if (
        not isinstance(values, list)
        or not values
        # An entry is invalid if it is not a string or if it is an empty string or whitespace only string. This is to avoid matching on generic domains like "example.com" or "test.com" which are not useful for matching.
        or any(not isinstance(value, str) or not value.strip() for value in values)
    ):
        raise ValueError("generic_domains.toml needs a non-empty domains list")
    # return the validated domains in a consistent, immutable collection
    return frozenset(value.strip().lower() for value in values)
