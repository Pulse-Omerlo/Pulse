# AI contribution: 50% or more AI-generated
"""Group matched records into organizations without touching the database."""

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
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


def company_linkedin(record: SourceRecord) -> str | None:
    """The record's company LinkedIn URL; a person's profile cannot identify an organization."""
    if record.linkedin and record.linkedin.startswith("company/"):
        return record.linkedin
    return None


def organization_domain(record: SourceRecord, generic_domains: frozenset[str]) -> str | None:
    """The record's domain unless it is shared by unrelated organizations."""
    if record.domain and record.domain not in generic_domains:
        return record.domain
    return None


def member_from_record(record: SourceRecord, generic_domains: frozenset[str]) -> Member:
    return Member(
        id=record.id,
        name=(record.attributes or {}).get("name") or record.name_normalized or record.external_id,
        domain=organization_domain(record, generic_domains),
        linkedin=company_linkedin(record),
        external_ids=frozenset(external_keys(record)),
        updated=record.valid_from,
    )


def _disagree(first: frozenset[str], second: frozenset[str]) -> bool:
    return bool(first) and bool(second) and first.isdisjoint(second)


@dataclass(frozen=True)
class Identifiers:
    """Every domain, company LinkedIn URL and external id seen in a group of records."""

    domains: frozenset[str] = frozenset()
    linkedins: frozenset[str] = frozenset()
    # Values by namespace; never mutated after construction.
    external: Mapping[str, frozenset[str]] = field(default_factory=dict)

    @classmethod
    def of(cls, member: Member) -> "Identifiers":
        external = defaultdict(set)
        for namespace, value in member.external_ids:
            external[namespace].add(value)
        return cls(
            domains=frozenset({member.domain} - {None}),
            linkedins=frozenset({member.linkedin} - {None}),
            external={namespace: frozenset(values) for namespace, values in external.items()},
        )

    def merge(self, other: "Identifiers") -> "Identifiers":
        external = dict(self.external)
        for namespace, values in other.external.items():
            external[namespace] = external.get(namespace, frozenset()) | values
        return Identifiers(self.domains | other.domains, self.linkedins | other.linkedins, external)

    def disagrees(self, other: "Identifiers") -> bool:
        """True when both have values of one kind and share none of them."""
        if _disagree(self.domains, other.domains) or _disagree(self.linkedins, other.linkedins):
            return True
        return any(
            _disagree(values, other.external.get(namespace, frozenset()))
            for namespace, values in self.external.items()
        )


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

    def members(self, item: UUID) -> frozenset[UUID]:
        return frozenset(self._members[self.find(item)])

    def groups(self) -> list[frozenset[UUID]]:
        return [frozenset(group) for _, group in sorted(self._members.items())]


@dataclass(frozen=True)
class _Group:
    """What the planner needs to know about one union-find group."""

    identifiers: Identifiers
    organizations: frozenset[UUID]
    manual: bool
    # Records that a reviewer rejected as a match for some member.
    rejected: frozenset[UUID]

    def merge(self, other: "_Group") -> "_Group":
        return _Group(
            self.identifiers.merge(other.identifiers),
            self.organizations | other.organizations,
            self.manual or other.manual,
            self.rejected | other.rejected,
        )


class _Planner:
    """Union-find over the records, with a summary of each group kept at its root."""

    def __init__(
        self,
        members: Mapping[UUID, Member],
        links: Mapping[UUID, LinkFacts],
        rejected: Iterable[Pair],
    ) -> None:
        rejected_by = defaultdict(set)
        for left, right in rejected:
            rejected_by[left].add(right)
            rejected_by[right].add(left)
        self.links = links
        self.manual = frozenset(f.organization_id for f in links.values() if f.manual)
        self.sets = DisjointSet(sorted(members))
        self.groups = {}
        for record_id, member in members.items():
            facts = links.get(record_id)
            organizations = frozenset({facts.organization_id}) if facts else frozenset()
            self.groups[record_id] = _Group(
                Identifiers.of(member),
                organizations,
                bool(organizations & self.manual),
                frozenset(rejected_by.get(record_id, ())),
            )

    def root(self, record_id: UUID) -> UUID:
        return self.sets.find(record_id)

    def join(self, first: UUID, second: UUID) -> None:
        first, second = self.root(first), self.root(second)
        if first == second:
            return
        merged = self.groups.pop(first).merge(self.groups.pop(second))
        self.groups[self.sets.union(first, second)] = merged

    def blocked(self, edge: MatchCandidateData, first: UUID, second: UUID) -> bool:
        """Whether joining the groups rooted at first and second through edge breaks a rule."""
        a, b = self.groups[first], self.groups[second]
        smaller, other_root = (a, second) if len(a.rejected) <= len(b.rejected) else (b, first)
        if any(self.root(record_id) == other_root for record_id in smaller.rejected):
            return True
        merges = bool(a.organizations and b.organizations)
        if merges and (not can_merge_organizations(edge) or a.manual or b.manual):
            return True
        return a.identifiers.disagrees(b.identifiers)


def choose_survivor(counts: Mapping[UUID, int]) -> UUID:
    """The organization with the most current links; ties go to the smallest id."""
    return min(counts, key=lambda organization_id: (-counts[organization_id], organization_id))


def _new_links(
    group: frozenset[UUID],
    edges: Sequence[MatchCandidateData],
    links: Mapping[UUID, LinkFacts],
) -> tuple[NewLink, ...]:
    """Link each unlinked member through the kept edge that reaches it first.

    The graph search starts from the linked members, or from the smallest id for a new
    organization, and follows edges strongest first.
    """
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


def _cluster(
    planner: _Planner,
    members: Mapping[UUID, Member],
    group: frozenset[UUID],
    edges: Sequence[MatchCandidateData],
) -> Cluster:
    links = planner.links
    counts = Counter(links[r].organization_id for r in group if r in links)
    survivor = choose_survivor(counts) if counts else None
    name, domain = choose_display_values(members[r] for r in sorted(group))
    return Cluster(
        members=group,
        organization_id=survivor,
        merged=tuple(sorted(set(counts) - {survivor})),
        new_links=_new_links(group, edges, links),
        display_name=name,
        primary_domain=domain,
    )


def _by_organization(
    links: Mapping[UUID, LinkFacts], members: Mapping[UUID, Member]
) -> dict[UUID, list[UUID]]:
    by_organization = defaultdict(list)
    for record_id in sorted(links.keys() & members.keys()):
        by_organization[links[record_id].organization_id].append(record_id)
    return by_organization


def plan_clusters(
    members: Mapping[UUID, Member],
    edges: Sequence[MatchCandidateData],
    links: Mapping[UUID, LinkFacts],
    rejected: Iterable[Pair] = (),
) -> ClusterPlan:
    """Cluster records with union-find, cutting the edges whose join would break a rule.

    Guarantee: no join brings together a rejected pair, organizations that may not merge,
    or two groups that both have values of one identifier and share none of them.
    Records already in one organization start as one group; strong edges are then applied
    strongest first (Tier 1 rule priority, weight, then pair ids), and a cut edge becomes
    a candidate.
    """
    planner = _Planner(members, links, rejected)
    for record_ids in _by_organization(links, members).values():
        for record_id in record_ids[1:]:
            planner.join(record_ids[0], record_id)

    kept, cut = [], []
    strong = [e for e in edges if e.strong and e.left in members and e.right in members]
    for edge in sorted(strong, key=strength_key):
        first, second = planner.root(edge.left), planner.root(edge.right)
        if first != second and planner.blocked(edge, first, second):
            cut.append(edge)
            continue
        planner.join(first, second)
        kept.append(edge)

    edges_by_root = defaultdict(list)
    for edge in kept:
        edges_by_root[planner.root(edge.left)].append(edge)
    clusters = tuple(
        _cluster(planner, members, group, edges_by_root[min(group)])
        for group in planner.sets.groups()
        if len(group) > 1
    )
    return ClusterPlan(clusters=clusters, candidates=tuple(cut))
