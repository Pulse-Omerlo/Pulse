# AI contribution: 50% or more AI-generated
"""Group matched records into organizations without touching the database."""

import heapq
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from itertools import groupby
from uuid import UUID

from app.models import SourceRecord
from app.processing.cluster_groups import Groups, Identifiers, record_group
from app.processing.fuzzy_profile import signals
from app.processing.match_rules import TIER1_RULES, MatchCandidateData, Pair, strength_key
from app.processing.organization_values import choose_display_values

LINKEDIN_COMPANY_PREFIX = "company/"


@dataclass(frozen=True)
class Member:
    """The fields of one current record that clustering looks at."""

    id: UUID
    # attributes["name"] only; the fallbacks are separate fields.
    name: str | None = None
    name_normalized: str | None = None
    external_id: str | None = None
    # Non-generic domain and company LinkedIn URL only.
    domain: str | None = None
    linkedin: str | None = None
    external_ids: frozenset[tuple[str, str]] = frozenset()
    updated: datetime | None = None
    # Cities and business email domains, which a fuzzy match must not contradict.
    cities: frozenset[str] = frozenset()
    email_domains: frozenset[str] = frozenset()


@dataclass(frozen=True)
class LinkFacts:
    """Where a record is linked now, with its organization id already resolved."""

    organization_id: UUID
    manual: bool = False
    # The link rests on a fuzzy match, directly or through another tentative link.
    tentative: bool = False
    # The organization the stored link names, when it is one that was merged away.
    stored_organization_id: UUID | None = None

    @property
    def stored(self) -> UUID:
        return self.stored_organization_id or self.organization_id


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
    # None means keep the stored value: a manual organization keeps both.
    display_name: str | None
    primary_domain: str | None
    # Linked members whose stored link names another organization than this one.
    repointed: tuple[UUID, ...] = ()
    # Members placed by a fuzzy match, directly or through another tentative member.
    tentative: frozenset[UUID] = frozenset()


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
    if record.linkedin and record.linkedin.startswith(LINKEDIN_COMPANY_PREFIX):
        return record.linkedin
    return None


def organization_domain(record: SourceRecord, generic_domains: frozenset[str]) -> str | None:
    """The record's domain unless it is shared by unrelated organizations."""
    if record.domain and record.domain not in generic_domains:
        return record.domain
    return None


def member_from_record(record: SourceRecord, generic_domains: frozenset[str]) -> Member:
    found = signals(record, generic_domains)
    return Member(
        id=record.id,
        name=(record.attributes or {}).get("name") or None,
        name_normalized=record.name_normalized,
        external_id=record.external_id,
        domain=organization_domain(record, generic_domains),
        linkedin=company_linkedin(record),
        external_ids=frozenset(external_keys(record)),
        updated=record.valid_from,
        cities=found.cities,
        email_domains=found.email_domains,
    )


def choose_survivor(counts: Mapping[UUID, int]) -> UUID:
    """The organization with the most current links; ties go to the smallest id."""
    return min(counts, key=lambda organization_id: (-counts[organization_id], organization_id))


def _new_links(
    group: frozenset[UUID],
    edges: Sequence[MatchCandidateData],
    links: Mapping[UUID, LinkFacts],
) -> tuple[NewLink, ...]:
    """Link each unlinked member through the strongest kept edge that reaches it.

    Like Prim's algorithm: from the linked members (or, for a new organization, the
    strongest edge), always take the strongest edge from a linked member to an unlinked
    one, so a record reachable through a Tier 1 edge is never linked through a fuzzy one.
    """
    adjacency = defaultdict(list)
    for edge in edges:
        adjacency[edge.left].append((strength_key(edge), edge.right, edge))
        adjacency[edge.right].append((strength_key(edge), edge.left, edge))
    linked = set(group & links.keys())
    result: list[NewLink] = []
    if not linked:
        # A new organization always has at least one kept edge.
        first = min(edges, key=strength_key)
        result += [
            NewLink(first.left, first, first.right),
            NewLink(first.right, first, first.left),
        ]
        linked = {first.left, first.right}
    frontier = [item for record_id in linked for item in adjacency[record_id]]
    heapq.heapify(frontier)
    while frontier:
        _, record_id, edge = heapq.heappop(frontier)
        if record_id in linked:
            continue
        neighbor = edge.right if edge.left == record_id else edge.left
        result.append(NewLink(record_id, edge, neighbor))
        linked.add(record_id)
        for item in adjacency[record_id]:
            if item[1] not in linked:
                heapq.heappush(frontier, item)
    return tuple(result)


def _cluster(
    members: Mapping[UUID, Member],
    links: Mapping[UUID, LinkFacts],
    manual: frozenset[UUID],
    group: frozenset[UUID],
    edges: Sequence[MatchCandidateData],
    tentative: frozenset[UUID] = frozenset(),
) -> Cluster:
    counts = Counter(links[r].organization_id for r in group if r in links)
    survivor = choose_survivor(counts) if counts else None
    name, domain = None, None
    if survivor not in manual:
        name, domain = choose_display_values(members[r] for r in sorted(group))
    return Cluster(
        members=group,
        organization_id=survivor,
        merged=tuple(sorted(set(counts) - {survivor})),
        new_links=_new_links(group, edges, links),
        display_name=name,
        primary_domain=domain,
        repointed=tuple(sorted(r for r in group if r in links and links[r].stored != survivor)),
        tentative=tentative,
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
    manual_organizations: Iterable[UUID],
) -> Groups:
    """One group per record, then records already in one organization joined together."""
    rejected_by = defaultdict(set)
    for left, right in rejected:
        rejected_by[left].add(right)
        rejected_by[right].add(left)
    manual = {facts.organization_id for facts in links.values() if facts.manual}
    manual.update(manual_organizations)
    summaries = {}
    places = {r: (member.cities, member.email_domains) for r, member in members.items()}
    for record_id, member in members.items():
        facts = links.get(record_id)
        organizations = frozenset({facts.organization_id}) if facts else frozenset()
        summaries[record_id] = record_group(
            record_id,
            Identifiers.of(member),
            places[record_id],
            bool(facts and facts.tentative),
            organizations,
            bool(organizations & manual),
            frozenset(rejected_by.get(record_id, ())),
        )
    groups = Groups(summaries, frozenset(manual), places)
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
    manual_organizations: Iterable[UUID] = (),
) -> ClusterPlan:
    """Cluster records with union-find, cutting the edges whose join would break a rule.

    Guarantee: no join brings together a rejected pair, organizations that may not merge,
    or two groups that both have values of one identifier and share none of them.
    An organization with a manual link in any version counts as manual. Records already
    in one organization start as one group. Strong edges are applied
    strongest first (Tier 1 rule priority, weight, then pair ids); a record whose equally
    strong edges reach groups that could not join each other joins none of them. A cut
    edge becomes a candidate.
    """
    groups = _initial_groups(members, links, rejected, manual_organizations)
    kept, cut = [], []
    strong = [e for e in edges if e.strong and e.left in members and e.right in members]
    for _, tier in groupby(sorted(strong, key=strength_key), key=_tier):
        tier = list(tier)
        ties = groups.tie_cuts(tier)
        for edge in tier:
            first, second = groups.root(edge.left), groups.root(edge.right)
            # A weaker path between records already together would only be a worse link.
            if first == second and edge.rule not in TIER1_RULES:
                continue
            if edge in ties or (first != second and groups.blocked(edge, first, second)):
                cut.append(edge)
                continue
            groups.join(first, second, edge)
            kept.append(edge)

    edges_by_root = defaultdict(list)
    for edge in kept:
        edges_by_root[groups.root(edge.left)].append(edge)
    clusters = tuple(
        _cluster(
            members,
            links,
            groups.manual,
            group,
            edges_by_root[min(group)],
            groups.tentative_members(min(group)),
        )
        for group in groups.sets.groups()
        if len(group) > 1 or group & links.keys()
    )
    return ClusterPlan(clusters=clusters, candidates=tuple(cut))
