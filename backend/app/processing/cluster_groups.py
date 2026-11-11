# AI contribution: 50% or more AI-generated
"""Union-find groups of records and the identifiers each group has seen."""

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from itertools import combinations
from typing import TYPE_CHECKING
from uuid import UUID

from app.processing.match_rules import TIER1_RULES, MatchCandidateData, can_merge_organizations

if TYPE_CHECKING:
    from app.processing.clustering import Member


def _disagree(first: frozenset[str], second: frozenset[str]) -> bool:
    return bool(first) and bool(second) and first.isdisjoint(second)


def has_disjoint_pair(value_sets: Iterable[frozenset[str]]) -> bool:
    """Whether two of the non-empty sets share no value."""
    distinct = {values for values in value_sets if values}
    if len(distinct) < 2 or frozenset.intersection(*distinct):
        return False
    if all(len(values) == 1 for values in distinct):
        return True
    return any(first.isdisjoint(second) for first, second in combinations(distinct, 2))


@dataclass(frozen=True)
class Identifiers:
    """Every domain, company LinkedIn URL and external id seen in a group of records."""

    domains: frozenset[str] = frozenset()
    linkedins: frozenset[str] = frozenset()
    # Values by namespace; never mutated after construction.
    external: Mapping[str, frozenset[str]] = field(default_factory=dict)

    @classmethod
    def of(cls, member: "Member") -> "Identifiers":
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


def any_disagree(identifiers: Sequence[Identifiers]) -> bool:
    """Whether two of the groups disagree, without comparing every pair."""
    if has_disjoint_pair(i.domains for i in identifiers):
        return True
    if has_disjoint_pair(i.linkedins for i in identifiers):
        return True
    by_namespace = defaultdict(list)
    for item in identifiers:
        for namespace, values in item.external.items():
            by_namespace[namespace].append(values)
    return any(has_disjoint_pair(values) for values in by_namespace.values())


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


# How many members of a group have each set of cities (or email domains).
Counts = Mapping[frozenset[str], int]
# A record's cities and email domains.
Places = tuple[frozenset[str], frozenset[str]]
NO_PLACES: Places = (frozenset(), frozenset())


def _add(first: Counts, second: Counts, sign: int = 1) -> dict[frozenset[str], int]:
    total = dict(first)
    for values, count in second.items():
        total[values] = total.get(values, 0) + sign * count
    return {values: count for values, count in total.items() if values and count > 0}


def _against(values: frozenset[str], counts: Counts) -> bool:
    """Whether values disagree with the values of some member."""
    return bool(values) and any(other.isdisjoint(values) for other in counts)


@dataclass(frozen=True)
class Group:
    """What the planner needs to know about one union-find group."""

    identifiers: Identifiers
    organizations: frozenset[UUID] = frozenset()
    manual: bool = False
    # Records that a reviewer rejected as a match for some member.
    rejected: frozenset[UUID] = frozenset()
    size: int = 1
    # Members placed by a fuzzy match, directly or through another tentative member.
    tentative: frozenset[UUID] = frozenset()
    # Cities and email domains of the members that are not tentative.
    cities: Counts = field(default_factory=dict)
    email_domains: Counts = field(default_factory=dict)

    @property
    def single_unlinked(self) -> bool:
        return self.size == 1 and not self.organizations

    def merge(self, other: "Group") -> "Group":
        return Group(
            self.identifiers.merge(other.identifiers),
            self.organizations | other.organizations,
            self.manual or other.manual,
            self.rejected | other.rejected,
            self.size + other.size,
            self.tentative | other.tentative,
            _add(self.cities, other.cities),
            _add(self.email_domains, other.email_domains),
        )

    def with_tentative(self, record_ids: frozenset[UUID], places: Mapping[UUID, Places]) -> "Group":
        """The group with record_ids marked tentative."""
        added = record_ids - self.tentative
        cities = Counter(places.get(r, NO_PLACES)[0] for r in added)
        email_domains = Counter(places.get(r, NO_PLACES)[1] for r in added)
        return replace(
            self,
            tentative=self.tentative | added,
            cities=_add(self.cities, cities, -1),
            email_domains=_add(self.email_domains, email_domains, -1),
        )


def record_group(
    record_id: UUID,
    identifiers: Identifiers,
    places: Places,
    tentative: bool,
    organizations: frozenset[UUID],
    manual: bool,
    rejected: frozenset[UUID],
) -> Group:
    """The group of one record, before any join."""
    cities, email_domains = NO_PLACES if tentative else places
    return Group(
        identifiers,
        organizations,
        manual,
        rejected,
        tentative=frozenset({record_id}) if tentative else frozenset(),
        cities={cities: 1} if cities else {},
        email_domains={email_domains: 1} if email_domains else {},
    )


class Groups:
    """A union-find over records with the summary of each group kept at its root."""

    def __init__(
        self,
        groups: Mapping[UUID, Group],
        manual: frozenset[UUID],
        places: Mapping[UUID, Places] | None = None,
    ) -> None:
        self.sets = DisjointSet(sorted(groups))
        self._groups = dict(groups)
        # Organizations with a manual link in any version.
        self.manual = manual
        self._places = places or {}

    def root(self, record_id: UUID) -> UUID:
        return self.sets.find(record_id)

    def tentative(self, record_id: UUID) -> bool:
        return record_id in self._groups[self.root(record_id)].tentative

    def tentative_members(self, record_id: UUID) -> frozenset[UUID]:
        return self._groups[self.root(record_id)].tentative

    def join(self, first: UUID, second: UUID, edge: MatchCandidateData | None = None) -> None:
        first, second = self.root(first), self.root(second)
        if first != second:
            a, b = self._groups.pop(first), self._groups.pop(second)
            if edge is not None:
                a, b = self._placed(edge, first, a, second, b)
            self._groups[self.sets.union(first, second)] = a.merge(b)

    def _ends(self, edge: MatchCandidateData, first: UUID) -> tuple[UUID, UUID]:
        """The edge's record in the group rooted at first, then its other record."""
        if self.root(edge.left) == first:
            return edge.left, edge.right
        return edge.right, edge.left

    def _placed(
        self, edge: MatchCandidateData, first: UUID, a: Group, second: UUID, b: Group
    ) -> tuple[Group, Group]:
        """Mark tentative the side that edge places in the other's group.

        A fuzzy edge places its lone unlinked records. Any edge places a group without
        an organization when it reaches it through a tentative member.
        """
        if edge.rule not in TIER1_RULES:
            mark_a, mark_b = a.single_unlinked, b.single_unlinked
        else:
            in_first, in_second = self._ends(edge, first)
            mark_a = in_second in b.tentative and not a.organizations
            mark_b = in_first in a.tentative and not b.organizations
        if mark_a:
            a = a.with_tentative(self.sets.members(first), self._places)
        if mark_b:
            b = b.with_tentative(self.sets.members(second), self._places)
        return a, b

    def _fuzzy_blocked(self, edge: MatchCandidateData, first: UUID, second: UUID) -> bool:
        """A fuzzy edge adds one unlinked record to a member that is not tentative, and only
        when the record agrees on cities and email domains with every such member. Two lone
        records may also start a new organization."""
        a, b = self._groups[first], self._groups[second]
        if a.single_unlinked and b.single_unlinked:
            return False
        if not (a.single_unlinked or b.single_unlinked):
            return True
        joiner_root, target = (first, b) if a.single_unlinked else (second, a)
        joiner, anchor = self._ends(edge, joiner_root)
        cities, email_domains = self._places.get(joiner, NO_PLACES)
        return (
            anchor in target.tentative
            or _against(cities, target.cities)
            or _against(email_domains, target.email_domains)
        )

    def blocked(self, edge: MatchCandidateData, first: UUID, second: UUID) -> bool:
        """Whether joining the groups rooted at first and second through edge breaks a rule."""
        a, b = self._groups[first], self._groups[second]
        smaller, other_root = (a, second) if len(a.rejected) <= len(b.rejected) else (b, first)
        if any(self.root(record_id) == other_root for record_id in smaller.rejected):
            return True
        merges = bool(a.organizations and b.organizations)
        if merges and (not can_merge_organizations(edge) or a.manual or b.manual):
            return True
        # A record placed by a fuzzy match is not trusted to merge two organizations.
        if merges and any(self.tentative(record_id) for record_id in edge.providePair):
            return True
        if edge.rule not in TIER1_RULES and self._fuzzy_blocked(edge, first, second):
            return True
        return a.identifiers.disagrees(b.identifiers)

    def tie_cuts(self, tier: Sequence[MatchCandidateData]) -> set[MatchCandidateData]:
        """Edges of one strength from a record into groups that could not join each other.

        Joining either group first would decide where the record goes by id order, so
        the record keeps out of all of them.
        """
        neighbours = defaultdict(list)
        for edge in tier:
            neighbours[edge.left].append((edge.right, edge))
            neighbours[edge.right].append((edge.left, edge))
        cut = set()
        for record_id, pairs in neighbours.items():
            own = self.root(record_id)
            reachable = defaultdict(list)
            for other, edge in pairs:
                target = self.root(other)
                if target != own and not self.blocked(edge, own, target):
                    reachable[target].append(edge)
            if len(reachable) > 1 and self._conflicting(tier[0], set(reachable)):
                cut.update(edge for edges in reachable.values() for edge in edges)
        return cut

    def _conflicting(self, edge: MatchCandidateData, roots: set[UUID]) -> bool:
        # A fuzzy edge never joins two groups that each already hold a record, so a record
        # equally close to two groups goes to neither.
        if edge.rule not in TIER1_RULES:
            return True
        groups = [self._groups[root] for root in sorted(roots)]
        with_organizations = [group for group in groups if group.organizations]
        if len(with_organizations) > 1 and (
            not can_merge_organizations(edge) or any(g.manual for g in with_organizations)
        ):
            return True
        for root in roots:
            if any(
                self.root(record_id) in roots - {root} for record_id in self._groups[root].rejected
            ):
                return True
        return any_disagree([group.identifiers for group in groups])
