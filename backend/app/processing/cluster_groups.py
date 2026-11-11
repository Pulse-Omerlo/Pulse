# AI contribution: 50% or more AI-generated
"""Union-find groups of records and the identifiers each group has seen."""

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import combinations
from typing import TYPE_CHECKING
from uuid import UUID

from app.processing.match_rules import MatchCandidateData, can_merge_organizations

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


@dataclass(frozen=True)
class Group:
    """What the planner needs to know about one union-find group."""

    identifiers: Identifiers
    organizations: frozenset[UUID] = frozenset()
    manual: bool = False
    # Records that a reviewer rejected as a match for some member.
    rejected: frozenset[UUID] = frozenset()

    def merge(self, other: "Group") -> "Group":
        return Group(
            self.identifiers.merge(other.identifiers),
            self.organizations | other.organizations,
            self.manual or other.manual,
            self.rejected | other.rejected,
        )


class Groups:
    """A union-find over records with the summary of each group kept at its root."""

    def __init__(self, groups: Mapping[UUID, Group], manual: frozenset[UUID]) -> None:
        self.sets = DisjointSet(sorted(groups))
        self._groups = dict(groups)
        # Organizations with a manual link in any version.
        self.manual = manual

    def root(self, record_id: UUID) -> UUID:
        return self.sets.find(record_id)

    def join(self, first: UUID, second: UUID) -> None:
        first, second = self.root(first), self.root(second)
        if first != second:
            merged = self._groups.pop(first).merge(self._groups.pop(second))
            self._groups[self.sets.union(first, second)] = merged

    def blocked(self, edge: MatchCandidateData, first: UUID, second: UUID) -> bool:
        """Whether joining the groups rooted at first and second through edge breaks a rule."""
        first_group, second_group = self._groups[first], self._groups[second]
        if len(first_group.rejected) <= len(second_group.rejected):
            smaller, other_root = first_group, second
        else:
            smaller, other_root = second_group, first
        if any(self.root(record_id) == other_root for record_id in smaller.rejected):
            return True
        merges = bool(first_group.organizations and second_group.organizations)
        if merges and (
            not can_merge_organizations(edge) or first_group.manual or second_group.manual
        ):
            return True
        return first_group.identifiers.disagrees(second_group.identifiers)

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
