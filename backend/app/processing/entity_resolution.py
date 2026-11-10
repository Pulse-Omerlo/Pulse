# AI contribution: Below 50% AI-generated
import tomllib
from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cache
from itertools import combinations
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.tenancy import bound_to_tenant
from app.models import MatchCandidate, Organization, OrgLink, SourceRecord, Tenant
from app.models.resolution import RuleEvidence

# Naming the version of the matching rules used to make a decision
# If we need to change matching behavior later, we can create a new version and keep the old one for
# reference
RULE_VERSION = "tier1-v1"

# Priority of strong matching rules (Left = Highest priotity, Right = Lowest priority)
# Uses the highest priority rule that matches to make a decision, and ignores any lower priority
# rules that also match
RULE_PRIORITIES = ("domain", "linkedin", "external_id", "name_city")

# Dictionnary assigning a weight to each matching rule, used to compute a score for each match
# candidate
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


# Class for a match candidate, containing the two record version IDs and the matching rule that
# matched them
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

    # Distinguish rules that are strong enough to be used for automatic resolution from those that
    # are not
    # If other weak rules are added in the future, this property can be updated to reflect that
    @property
    def strong(self) -> bool:
        return self.rule != "name_only"


# Class for the result of a match resolution, containing the number of organizations, links, and
# candidates created
# Rerunning the matching on unchanged data should not create new organizations, links, or
# candidates, so these counts should be zero if the matching is idempotent
@dataclass
class MatchResult:
    # Counts new organizations.
    organizations_created: int = 0
    # Counts new links between organizations.
    links_created: int = 0
    # Counts new pairs set aside for review.
    candidates_created: int = 0


# Function to load the generic domains from the config file, used to filter out matches on generic
# domains
@cache
def load_generic_domains() -> frozenset[str]:
    with GENERIC_DOMAINS_PATH.open("rb") as handle:
        values = tomllib.load(handle).get("domains")
    if (
        not isinstance(values, list)
        or not values
        # An entry is invalid if it is not a string or if it is an empty string or whitespace only
        # string. This is to avoid matching on generic domains like "example.com" or "test.com"
        # which are not useful for matching.
        or any(not isinstance(value, str) or not value.strip() for value in values)
    ):
        raise ValueError("generic_domains.toml needs a non-empty domains list")
    # return the validated domains in a consistent, immutable collection
    return frozenset(value.strip().lower() for value in values)


# Function to extract external keys from a source record, used to identify matches based on external
# identifiers
def external_keys(record: SourceRecord) -> set[tuple[str, str]]:
    result = set()
    for namespace, values in (record.external_ids or {}).items():
        # Skip invalid namespaces or values. This is to avoid matching on invalid external
        # identifiers which are not useful for matching.
        if not isinstance(namespace, str) or not namespace.strip():
            continue
        # Skip invalid values. This is to avoid matching on invalid external identifiers which are
        # not useful for matching.
        if not isinstance(values, list):
            continue
        for value in values:
            # The goal is that the matcher can find shared identifiers using set intersection
            if isinstance(value, str) and value.strip():
                result.add((namespace.strip(), value.strip()))
    return result


# Produces a sequence of blocking keys for a record, used to group records by the fields that match
# AI Generated
def blocking_keys(
    record: SourceRecord,
    excluded_domains: frozenset[str],
) -> Iterable[Key]:
    # A domain is a strong identifier for an organization, but generic domains are not useful for
    # matching.
    if record.domain and record.domain not in excluded_domains:
        yield "domain", record.domain
    # A person's LinkedIn profile cannot identify an organization.
    if record.linkedin and record.linkedin.startswith("company/"):
        yield "linkedin", record.linkedin
    for namespace, value in sorted(external_keys(record)):
        yield "external_id", namespace, value
    if record.name_normalized:
        yield "name", record.name_normalized


# Function to classify a pair of source records, used to identify matches based on the matching
# rules
def classify_pair(
    left: SourceRecord,
    right: SourceRecord,
    excluded_domains: frozenset[str],
) -> MatchCandidateData | None:
    # Return the first matching rule, or weaker same-name evidence.
    # Reject pairs that are not comparable, or that have already been invalidated.
    if (
        # Reject pairs that are the same record, or that belong to different tenants, or that are
        # not organizations, or that have been invalidated.
        left.id == right.id
        or left.tenant_id != right.tenant_id
        or left.kind != "organization"
        or right.kind != "organization"
        or left.valid_to is not None
        or right.valid_to is not None
    ):
        return None
    left, right = sorted((left, right), key=lambda record: record.id)
    if (
        # Match domain
        left.domain and left.domain == right.domain and left.domain not in excluded_domains
    ):
        return MatchCandidateData(left.id, right.id, "domain", (("domain", left.domain),))
    if (
        # Match LinkedIn profile
        left.linkedin and left.linkedin.startswith("company/") and left.linkedin == right.linkedin
    ):
        return MatchCandidateData(left.id, right.id, "linkedin", (("linkedin", left.linkedin),))
    # Match external identifiers using set intersection to find shared identifiers
    shared_ids = external_keys(left) & external_keys(right)
    if shared_ids:
        namespace, value = min(shared_ids)
        return MatchCandidateData(
            left.id,
            right.id,
            "external_id",
            ((f"external_ids.{namespace}", value),),
        )
    # Match normalized names, with matching cities
    if left.name_normalized and left.name_normalized == right.name_normalized:
        fields = (("name_normalized", left.name_normalized),)
        if left.city and left.city == right.city:
            return MatchCandidateData(
                left.id,
                right.id,
                "name_city",
                (*fields, ("city", left.city)),
            )
        return MatchCandidateData(left.id, right.id, "name_only", fields)
    # No matching rule applies.
    return None


# Function to find plausible record pairs, classify the pairs and returning the matcching evidence
# AI generated
def blocked_matches(
    records: Iterable[SourceRecord],
    excluded_domains: frozenset[str],
) -> list[MatchCandidateData]:
    """Compare shared-key pairs once, rather than every possible pair."""
    records = {
        record.id: record
        for record in records
        if record.kind == "organization" and record.valid_to is None
    }
    blocks = defaultdict(list)
    for record in records.values():
        for key in blocking_keys(record, excluded_domains):
            blocks[(record.tenant_id, *key)].append(record.id)

    pairs = {
        tuple(sorted(pair)) for members in blocks.values() for pair in combinations(members, 2)
    }

    matches = []
    for left, right in sorted(pairs):
        match = classify_pair(records[left], records[right], excluded_domains)
        if match:
            matches.append(match)

    order = (*RULE_PRIORITIES, "name_only")
    return sorted(
        matches,
        key=lambda match: (order.index(match.rule), match.left, match.right),
    )


# Function to generate evidence for a match candidate, used to provide context for the matching
# decision
def evidence(match: MatchCandidateData, matched_record_id: UUID) -> list[RuleEvidence]:
    return [
        {
            "rule": match.rule,
            "rule_set_version": RULE_VERSION,
            "field": field,
            "value": value,
            "matched_value": value,
            "matched_record_id": str(matched_record_id),
        }
        for field, value in match.fields
    ]


# Function to check for conflicting identifiers in a group of records, used to prevent merging
# records with contradictory information
def conflicting_identifiers(records: Iterable[SourceRecord]) -> bool:
    linkedin = set()
    external = defaultdict(list)

    for record in records:
        if record.linkedin and record.linkedin.startswith("company/"):
            linkedin.add(record.linkedin)

        by_namespace = defaultdict(set)
        for namespace, value in external_keys(record):
            by_namespace[namespace].add(value)
        for namespace, values in by_namespace.items():
            external[namespace].append(values)

    if len(linkedin) > 1:
        return True

    # Multiple aliases are allowed when the sets retain a common ID.
    return any(not set.intersection(*values) for values in external.values() if len(values) > 1)


# Function to find connected components in a graph of matches, used to group records that are
# strongly connected by matching rules
# AI Assisted
def components(matches: Iterable[MatchCandidateData]) -> list[set[UUID]]:
    graph = defaultdict(set)
    for match in matches:
        if match.strong:
            graph[match.left].add(match.right)
            graph[match.right].add(match.left)

    groups = []
    remaining = set(graph)
    while remaining:
        queue = [min(remaining)]
        group = set()
        while queue:
            record_id = queue.pop()
            if record_id in group:
                continue
            group.add(record_id)
            queue.extend(graph[record_id] - group)
        remaining -= group
        groups.append(group)
    return groups


# Function to match organizations for a given tenant, used to create organizations and links based
# on the matching rules
# Overall goal: 1->Load a tenant's current organization records and exisitng decisions, 2->Find
# matches and connected groups, 3->Check each group for conflicts, 4->Create organizations, links or
# review candidates as appropriate, 5->Return a summary of the results
# AI Assisted with debugging and optimization
def match_organizations(session: Session, tenant_id: UUID) -> MatchResult:
    # Load the tenant's current organization records and existing decisions, and lock the tenant row
    # to prevent concurrent matching for the same tenant.
    with bound_to_tenant(session, tenant_id):
        # Serialize matcher invocations for this tenant.
        tenant = session.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
        if tenant is None:
            raise ValueError(f"Unknown tenant {tenant_id}")

        session.flush()
        records = {
            record.id: record
            for record in session.scalars(
                select(SourceRecord)
                .where(
                    SourceRecord.tenant_id == tenant_id,
                    SourceRecord.kind == "organization",
                    SourceRecord.valid_to.is_(None),
                )
                .order_by(SourceRecord.id)
            )
        }

        # Joining current records avoids loading historical link versions.
        links = {
            link.source_record_id: link
            for link in session.scalars(
                select(OrgLink)
                .join(SourceRecord, SourceRecord.id == OrgLink.source_record_id)
                .where(
                    OrgLink.tenant_id == tenant_id,
                    SourceRecord.tenant_id == tenant_id,
                    SourceRecord.kind == "organization",
                    SourceRecord.valid_to.is_(None),
                )
            )
        }
        decisions = {
            (candidate.left_record_id, candidate.right_record_id): candidate
            for candidate in session.scalars(
                select(MatchCandidate).where(
                    MatchCandidate.tenant_id == tenant_id,
                )
            )
            if candidate.left_record_id in records and candidate.right_record_id in records
        }

        matches = blocked_matches(records.values(), load_generic_domains())
        organizations_created = links_created = candidates_created = 0

        def add_candidate(match: MatchCandidateData) -> None:
            nonlocal candidates_created
            if match.providePair in decisions:
                return

            candidate = MatchCandidate(
                tenant_id=tenant_id,
                left_record_id=match.left,
                right_record_id=match.right,
                score=RULE_WEIGHTS[match.rule],
                matched_fields=evidence(match, match.right),
                status="pending",
            )
            session.add(candidate)
            decisions[match.providePair] = candidate
            candidates_created += 1

        for group in components(matches):
            edges = [
                match
                for match in matches
                if match.strong and match.left in group and match.right in group
            ]
            organization_ids = {
                links[record_id].organization_id for record_id in group if record_id in links
            }
            rejected = any(
                left in group and right in group and candidate.status == "rejected"
                for (left, right), candidate in decisions.items()
            )

            # Include current members of an existing organization when checking
            # contradictions, even if they have no direct edge to this group.
            members = group | {
                record_id
                for record_id, link in links.items()
                if link.organization_id in organization_ids
            }
            unsafe = (
                rejected
                or len(organization_ids) > 1
                or conflicting_identifiers(records[key] for key in members)
            )
            if unsafe:
                for match in edges:
                    add_candidate(match)
                continue

            if organization_ids:
                organization_id = next(iter(organization_ids))
            else:
                first = records[min(group)]
                organization_id = uuid4()
                session.add(
                    Organization(
                        id=organization_id,
                        tenant_id=tenant_id,
                        display_name=(
                            (first.attributes or {}).get("name")
                            or first.name_normalized
                            or first.external_id
                        ),
                        primary_domain=(
                            first.domain if first.domain not in load_generic_domains() else None
                        ),
                    )
                )
                # Organization must exist before links reference it.
                session.flush()
                organizations_created += 1

            adjacency = defaultdict(list)
            for match in edges:
                adjacency[match.left].append((match.right, match))
                adjacency[match.right].append((match.left, match))

            # Start with existing members, preserving their decisions.
            roots = sorted(group & links.keys())
            queue = deque(roots or [min(group)])
            visited = set(queue)

            while queue:
                record_id = queue.popleft()
                for neighbor, match in adjacency[record_id]:
                    if neighbor in visited:
                        continue

                    if record_id not in links:
                        link = OrgLink(
                            tenant_id=tenant_id,
                            source_record_id=record_id,
                            organization_id=organization_id,
                            method=match.rule,
                            confidence=RULE_WEIGHTS[match.rule],
                            decided_by=f"tier1:{RULE_VERSION}",
                            evidence=evidence(match, neighbor),
                        )
                        session.add(link)
                        links[record_id] = link
                        links_created += 1

                    if neighbor not in links:
                        link = OrgLink(
                            tenant_id=tenant_id,
                            source_record_id=neighbor,
                            organization_id=organization_id,
                            method=match.rule,
                            confidence=RULE_WEIGHTS[match.rule],
                            decided_by=f"tier1:{RULE_VERSION}",
                            evidence=evidence(match, record_id),
                        )
                        session.add(link)
                        links[neighbor] = link
                        links_created += 1

                    visited.add(neighbor)
                    queue.append(neighbor)

        for match in matches:
            if match.strong:
                continue
            left, right = links.get(match.left), links.get(match.right)
            if left and right and left.organization_id == right.organization_id:
                continue
            add_candidate(match)

        session.flush()
        return MatchResult(
            organizations_created=organizations_created,
            links_created=links_created,
            candidates_created=candidates_created,
        )
