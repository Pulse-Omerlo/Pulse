# AI contribution: 50% or more AI-generated
"""Group matched records into organizations without touching the database."""

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from itertools import groupby
from uuid import UUID

from app.models import SourceRecord
from app.processing.cluster_groups import Group, Groups, Identifiers
from app.processing.match_rules import MatchCandidateData, Pair, strength_key
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
    members: Mapping[UUID, Member],
    links: Mapping[UUID, LinkFacts],
    group: frozenset[UUID],
    edges: Sequence[MatchCandidateData],
) -> Cluster:
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


def _initial_groups(
    members: Mapping[UUID, Member],
    links: Mapping[UUID, LinkFacts],
    rejected: Iterable[Pair],
) -> Groups:
    """One group per record, then records already in one organization joined together."""
    rejected_by = defaultdict(set)
    for left, right in rejected:
        rejected_by[left].add(right)
        rejected_by[right].add(left)
    manual = {facts.organization_id for facts in links.values() if facts.manual}
    summaries = {}
    for record_id, member in members.items():
        facts = links.get(record_id)
        organizations = frozenset({facts.organization_id}) if facts else frozenset()
        summaries[record_id] = Group(
            Identifiers.of(member),
            organizations,
            bool(organizations & manual),
            frozenset(rejected_by.get(record_id, ())),
        )
    groups = Groups(summaries)
    for record_ids in _by_organization(links, members).values():
        for record_id in record_ids[1:]:
            groups.join(record_ids[0], record_id)
    return groups


def _tier(edge: MatchCandidateData) -> tuple[int, float]:
    return strength_key(edge)[:2]


def plan_clusters(
    members: Mapping[UUID, Member],
    edges: Sequence[MatchCandidateData],
    links: Mapping[UUID, LinkFacts],
    rejected: Iterable[Pair] = (),
) -> ClusterPlan:
    """Cluster records with union-find, cutting the edges whose join would break a rule.

    Guarantee: no join brings together a rejected pair, organizations that may not merge,
    or two groups that both have values of one identifier and share none of them.
    Records already in one organization start as one group. Strong edges are applied
    strongest first (Tier 1 rule priority, weight, then pair ids); a record whose equally
    strong edges reach groups that could not join each other joins none of them. A cut
    edge becomes a candidate.
    """
    groups = _initial_groups(members, links, rejected)
    kept, cut = [], []
    strong = [e for e in edges if e.strong and e.left in members and e.right in members]
    for _, tier in groupby(sorted(strong, key=strength_key), key=_tier):
        tier = list(tier)
        ties = groups.tie_cuts(tier)
        for edge in tier:
            first, second = groups.root(edge.left), groups.root(edge.right)
            if edge in ties or (first != second and groups.blocked(edge, first, second)):
                cut.append(edge)
                continue
            groups.join(first, second)
            kept.append(edge)

    edges_by_root = defaultdict(list)
    for edge in kept:
        edges_by_root[groups.root(edge.left)].append(edge)
    clusters = tuple(
        _cluster(members, links, group, edges_by_root[min(group)])
        for group in groups.sets.groups()
        if len(group) > 1
    )
    return ClusterPlan(clusters=clusters, candidates=tuple(cut))
