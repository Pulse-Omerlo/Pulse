# AI contribution: 50% or more AI-generated
"""Group matched records into organizations without touching the database."""

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.models import SourceRecord
from app.processing.match_rules import (
    MatchCandidateData,
    Pair,
    can_merge_organizations,
    strength_key,
)
from app.processing.organization_values import choose_display_values


@dataclass(frozen=True)
class Member:
    """The fields of one current record that clustering looks at."""

    id: UUID
    name: str | None = None
    # Non-generic domain and company LinkedIn URL only.
    domain: str | None = None
    linkedin: str | None = None
    external_ids: frozenset[tuple[str, str]] = frozenset()
    updated: datetime | None = None


@dataclass(frozen=True)
class LinkFacts:
    """Where a record is linked now, with its organization id already resolved."""

    organization_id: UUID
    manual: bool = False


@dataclass(frozen=True)
class NewLink:
    """Link record_id through edge, whose other end is neighbor."""

    record_id: UUID
    edge: MatchCandidateData
    neighbor: UUID


@dataclass(frozen=True)
class Cluster:
    members: frozenset[UUID]
    # None means a new organization.
    organization_id: UUID | None
    merged: tuple[UUID, ...]
    new_links: tuple[NewLink, ...]
    display_name: str | None
    primary_domain: str | None

    @property
    def changed(self) -> bool:
        return self.organization_id is None or bool(self.merged or self.new_links)


@dataclass(frozen=True)
class ClusterPlan:
    clusters: tuple[Cluster, ...]
    # Edges cut to keep the clusters apart, to store as match candidates.
    candidates: tuple[MatchCandidateData, ...]


def external_keys(record: SourceRecord) -> set[tuple[str, str]]:
    """Valid (namespace, value) pairs of a record's external ids."""
    result = set()
    for namespace, values in (record.external_ids or {}).items():
        if not isinstance(namespace, str) or not namespace.strip():
            continue
        if not isinstance(values, list):
            continue
        for value in values:
            if isinstance(value, str) and value.strip():
                result.add((namespace.strip(), value.strip()))
    return result


def member_from_record(record: SourceRecord, generic_domains: frozenset[str]) -> Member:
    # A person's LinkedIn profile cannot identify an organization.
    company = bool(record.linkedin and record.linkedin.startswith("company/"))
    linkedin = record.linkedin if company else None
    return Member(
        id=record.id,
        name=(record.attributes or {}).get("name") or record.name_normalized or record.external_id,
        domain=record.domain if record.domain not in generic_domains else None,
        linkedin=linkedin,
        external_ids=frozenset(external_keys(record)),
        updated=record.valid_from,
    )


def conflicting(members: Iterable[Member]) -> bool:
    """True when the members disagree on a domain, a company LinkedIn URL or an external id.

    Two id sets in one namespace agree while they share a value, so aliases are allowed.
    """
    domains, linkedins = set(), set()
    external = defaultdict(list)
    for member in members:
        if member.domain:
            domains.add(member.domain)
        if member.linkedin:
            linkedins.add(member.linkedin)
        by_namespace = defaultdict(set)
        for namespace, value in member.external_ids:
            by_namespace[namespace].add(value)
        for namespace, values in by_namespace.items():
            external[namespace].append(values)
    if len(domains) > 1 or len(linkedins) > 1:
        return True
    return any(not set.intersection(*values) for values in external.values() if len(values) > 1)


class DisjointSet:
    """Union-find whose root is always the smallest id, so the result is deterministic."""

    def __init__(self, items: Iterable[UUID]) -> None:
        self._parent = {item: item for item in items}
        self._members = {item: {item} for item in self._parent}

    def find(self, item: UUID) -> UUID:
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[item] != root:
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, first: UUID, second: UUID) -> UUID:
        first, second = sorted((self.find(first), self.find(second)))
        if first != second:
            self._parent[second] = first
            self._members[first] |= self._members.pop(second)
        return first

    def members(self, item: UUID) -> set[UUID]:
        return self._members[self.find(item)]

    def groups(self) -> list[frozenset[UUID]]:
        return [frozenset(group) for _, group in sorted(self._members.items())]


def components(matches: Iterable[MatchCandidateData]) -> list[set[UUID]]:
    """Records joined by a chain of strong matches, ignoring conflicts."""
    strong = [match for match in matches if match.strong]
    sets = DisjointSet(sorted({record for match in strong for record in match.providePair}))
    for match in strong:
        sets.union(match.left, match.right)
    return [set(group) for group in sets.groups() if len(group) > 1]


@dataclass(frozen=True)
class _Context:
    members: Mapping[UUID, Member]
    links: Mapping[UUID, LinkFacts]
    rejected: Mapping[UUID, frozenset[UUID]]
    manual_organizations: frozenset[UUID]

    def organizations(self, records: Iterable[UUID]) -> set[UUID]:
        return {self.links[r].organization_id for r in records if r in self.links}


def _blocked(context: _Context, edge: MatchCandidateData, first: set, second: set) -> bool:
    """Whether joining two groups through edge would break a rule."""
    if any(context.rejected.get(record, frozenset()) & second for record in first):
        return True
    first_orgs, second_orgs = context.organizations(first), context.organizations(second)
    if first_orgs and second_orgs:
        if not can_merge_organizations(edge):
            return True
        if (first_orgs | second_orgs) & context.manual_organizations:
            return True
    return conflicting(context.members[record] for record in first | second)


def choose_survivor(counts: Mapping[UUID, int]) -> UUID:
    """The organization with the most current links; ties go to the smallest id."""
    return min(counts, key=lambda organization_id: (-counts[organization_id], organization_id))


def _new_links(group, edges, links) -> tuple[NewLink, ...]:
    """Link each unlinked member through the edge that first reaches it from a linked one."""
    adjacency = defaultdict(list)
    for edge in edges:
        adjacency[edge.left].append((edge.right, edge))
        adjacency[edge.right].append((edge.left, edge))
    roots = sorted(group & links.keys()) or [min(group)]
    linked, queue, result = set(roots) & links.keys(), list(roots), []
    for record_id in queue:
        for neighbor, edge in adjacency[record_id]:
            if record_id not in linked:
                result.append(NewLink(record_id, edge, neighbor))
                linked.add(record_id)
            if neighbor not in linked:
                result.append(NewLink(neighbor, edge, record_id))
                linked.add(neighbor)
                queue.append(neighbor)
    return tuple(result)


def _cluster(context: _Context, group: frozenset[UUID], edges) -> Cluster:
    counts = Counter(context.links[r].organization_id for r in group if r in context.links)
    survivor = choose_survivor(counts) if counts else None
    name, domain = choose_display_values(context.members[r] for r in sorted(group))
    return Cluster(
        members=group,
        organization_id=survivor,
        merged=tuple(sorted(set(counts) - {survivor})),
        new_links=_new_links(group, edges, context.links),
        display_name=name,
        primary_domain=domain,
    )


def plan_clusters(
    members: Mapping[UUID, Member],
    edges: Sequence[MatchCandidateData],
    links: Mapping[UUID, LinkFacts],
    rejected: Iterable[Pair] = (),
) -> ClusterPlan:
    """Cluster records with union-find, cutting the weakest edges that would break a rule.

    Records already in one organization start in the same group. Strong edges are then
    applied strongest first (Tier 1 rule priority, weight, then pair ids). An edge whose
    two groups would contain a rejected pair, would show conflicting identifiers, or
    would merge organizations when one has a manual link or the edge is not Tier 1, is
    cut and becomes a candidate. Every rule only gets harder to meet as a group grows,
    so a cut edge always ends up between two different clusters.
    """
    rejected_by = defaultdict(set)
    for left, right in rejected:
        rejected_by[left].add(right)
        rejected_by[right].add(left)
    context = _Context(
        members=members,
        links=links,
        rejected={record: frozenset(others) for record, others in rejected_by.items()},
        manual_organizations=frozenset(f.organization_id for f in links.values() if f.manual),
    )
    sets = DisjointSet(sorted(members))
    by_organization = defaultdict(list)
    for record_id in sorted(links.keys() & members.keys()):
        by_organization[links[record_id].organization_id].append(record_id)
    for record_ids in by_organization.values():
        for record_id in record_ids[1:]:
            sets.union(record_ids[0], record_id)

    kept, cut = [], []
    strong = [e for e in edges if e.strong and e.left in members and e.right in members]
    for edge in sorted(strong, key=strength_key):
        apart = sets.find(edge.left) != sets.find(edge.right)
        if apart and _blocked(context, edge, sets.members(edge.left), sets.members(edge.right)):
            cut.append(edge)
            continue
        sets.union(edge.left, edge.right)
        kept.append(edge)

    edges_by_root = defaultdict(list)
    for edge in kept:
        edges_by_root[sets.find(edge.left)].append(edge)
    clusters = tuple(
        _cluster(context, group, edges_by_root[min(group)])
        for group in sets.groups()
        if len(group) > 1
    )
    return ClusterPlan(clusters=clusters, candidates=tuple(cut))
