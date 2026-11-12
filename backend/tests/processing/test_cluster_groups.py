# AI contribution: 50% or more AI-generated
from uuid import UUID

import pytest

from app.processing.cluster_groups import DisjointSet, Identifiers, any_disagree, has_disjoint_pair
from app.processing.clustering import Member

A, B, C, D = (UUID(int=n) for n in range(1, 5))


def test_union_find_keeps_the_smallest_id_as_root():
    # Arrange
    sets = DisjointSet([A, B, C, D])
    # Act
    sets.union(D, C)
    sets.union(C, B)
    # Assert
    assert sets.find(D) == B
    assert sets.members(C) == frozenset({B, C, D})
    assert sets.groups() == [frozenset({A}), frozenset({B, C, D})]


@pytest.mark.parametrize(
    ("first", "second", "conflict"),
    [
        (Member(A, domain="parent.ca"), Member(B, domain="subsidiary.ca"), True),
        (Member(A, linkedin="company/parent"), Member(B, linkedin="company/subsidiary"), True),
        (
            Member(A, external_ids=frozenset({("neq", "123")})),
            Member(B, external_ids=frozenset({("neq", "456")})),
            True,
        ),
        (
            Member(A, external_ids=frozenset({("crm", "old"), ("crm", "123")})),
            Member(B, external_ids=frozenset({("crm", "123")})),
            False,
        ),
        (Member(A, external_ids=frozenset({("crm", "123")})), Member(B), False),
        (Member(A, domain="exemple.ca"), Member(B), False),
    ],
)
def test_identifiers_disagree_only_with_no_shared_value(first, second, conflict):
    # Act
    result = Identifiers.of(first).disagrees(Identifiers.of(second))
    # Assert
    assert result is conflict


def test_external_ids_disagree_per_namespace():
    # Arrange: crm ids are shared, neq ids are not.
    first = Member(A, external_ids=frozenset({("crm", "1"), ("neq", "10")}))
    second = Member(B, external_ids=frozenset({("crm", "1"), ("neq", "20")}))
    third = Member(C, external_ids=frozenset({("crm", "1"), ("other", "20")}))
    # Act / Assert
    assert Identifiers.of(first).disagrees(Identifiers.of(second))
    assert not Identifiers.of(first).disagrees(Identifiers.of(third))


@pytest.mark.parametrize(
    ("sets", "expected"),
    [
        ([frozenset({"a"}), frozenset({"a"}), frozenset()], False),
        ([frozenset({"a"}), frozenset({"b"})], True),
        ([frozenset({"a", "b"}), frozenset({"a"}), frozenset({"b"})], True),
        ([frozenset({"a", "b"}), frozenset({"a", "c"}), frozenset({"a"})], False),
        ([frozenset({"a", "b"}), frozenset({"b", "c"}), frozenset({"c", "a"})], False),
    ],
)
def test_disjoint_pair_is_found_without_comparing_every_pair_when_possible(sets, expected):
    # Act / Assert
    assert has_disjoint_pair(sets) is expected


def test_any_disagree_checks_each_namespace_on_its_own():
    # Arrange
    groups = [
        Identifiers.of(Member(A, external_ids=frozenset({("crm", "1"), ("neq", "9")}))),
        Identifiers.of(Member(B, external_ids=frozenset({("crm", "2")}))),
    ]
    # Act / Assert
    assert any_disagree(groups)
    assert not any_disagree([groups[0], Identifiers.of(Member(C, domain="exemple.ca"))])
