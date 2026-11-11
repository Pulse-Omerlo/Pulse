# AI contribution: 50% or more AI-generated
"""Clustering plans built from plain data, without a database."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.models import SourceRecord
from app.processing import clustering
from app.processing.clustering import (
    DisjointSet,
    Identifiers,
    LinkFacts,
    Member,
    choose_survivor,
    member_from_record,
    plan_clusters,
)
from app.processing.match_rules import MatchCandidateData

A, B, C, D, E = (UUID(int=n) for n in range(1, 6))
ORG_1, ORG_2, ORG_3 = (UUID(int=n) for n in range(101, 104))


def edge(left, right, rule):
    return MatchCandidateData(left, right, rule, ((rule, "value"),))


def members(*items):
    return {item.id: item for item in items}


def test_organization_that_already_disagrees_can_still_gain_a_record():
    # Arrange: a reviewer put old.ca and new.ca in one organization.
    items = members(
        Member(A, domain="old.ca"), Member(B, domain="new.ca"), Member(C, domain="new.ca")
    )
    links = {A: LinkFacts(ORG_1, manual=True), B: LinkFacts(ORG_1, manual=True)}
    # Act
    plan = plan_clusters(items, [edge(B, C, "domain")], links)
    # Assert
    assert plan.candidates == ()
    assert [link.record_id for link in plan.clusters[0].new_links] == [C]


def test_join_that_adds_a_new_disagreement_is_still_cut():
    # Arrange
    items = members(
        Member(A, domain="old.ca"),
        Member(B, domain="new.ca", linkedin="company/exemple"),
        Member(C, domain="other.ca", linkedin="company/exemple"),
    )
    links = {A: LinkFacts(ORG_1), B: LinkFacts(ORG_1)}
    # Act
    plan = plan_clusters(items, [edge(B, C, "linkedin")], links)
    # Assert
    assert [e.rule for e in plan.candidates] == ["linkedin"]


def test_external_ids_disagree_per_namespace():
    # Arrange: crm ids are shared, neq ids are not.
    first = Member(A, external_ids=frozenset({("crm", "1"), ("neq", "10")}))
    second = Member(B, external_ids=frozenset({("crm", "1"), ("neq", "20")}))
    third = Member(C, external_ids=frozenset({("crm", "1"), ("other", "20")}))
    # Act / Assert
    assert Identifiers.of(first).disagrees(Identifiers.of(second))
    assert not Identifiers.of(first).disagrees(Identifiers.of(third))


def test_large_shared_domain_block_is_planned_without_mixing_linkedin_pages():
    # Arrange: 600 records on one domain, half on each of two LinkedIn pages.
    ids = [UUID(int=1000 + n) for n in range(600)]
    items = members(
        *(Member(i, domain="groupe.ca", linkedin=f"company/{n % 2}") for n, i in enumerate(ids))
    )
    edges = [edge(x, y, "domain") for n, x in enumerate(ids) for y in ids[n + 1 :]]
    # Act
    plan = plan_clusters(items, edges, {})
    # Assert
    for cluster in plan.clusters:
        assert len({items[r].linkedin for r in cluster.members}) == 1
    kept = len(edges) - len(plan.candidates)
    assert kept >= sum(len(c.members) - 1 for c in plan.clusters)


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


def test_three_record_chain_becomes_one_new_cluster():
    # Arrange
    items = members(
        Member(A, domain="exemple.ca"),
        Member(B, domain="exemple.ca", linkedin="company/exemple"),
        Member(C, linkedin="company/exemple"),
    )
    edges = [edge(B, C, "linkedin"), edge(A, B, "domain")]
    # Act
    plan = plan_clusters(items, edges, {})
    # Assert
    assert plan.candidates == ()
    (cluster,) = plan.clusters
    assert cluster.members == {A, B, C} and cluster.organization_id is None
    assert cluster.changed and cluster.merged == ()
    assert [(link.record_id, link.neighbor) for link in cluster.new_links] == [
        (A, B),
        (B, A),
        (C, B),
    ]
    assert cluster.new_links[2].edge.rule == "linkedin"


def test_two_organizations_merge_into_the_one_with_most_links():
    # Arrange
    items = members(*(Member(n, domain="exemple.ca") for n in (A, B, C, D)))
    links = {A: LinkFacts(ORG_2), B: LinkFacts(ORG_1), C: LinkFacts(ORG_1)}
    # Act
    plan = plan_clusters(items, [edge(A, B, "domain"), edge(C, D, "domain")], links)
    # Assert
    (cluster,) = plan.clusters
    assert cluster.organization_id == ORG_1 and cluster.merged == (ORG_2,)
    assert [link.record_id for link in cluster.new_links] == [D]


def test_survivor_ties_go_to_the_smallest_id():
    # Act / Assert
    assert choose_survivor({ORG_3: 2, ORG_2: 2, ORG_1: 1}) == ORG_2


def test_conflicting_chain_cuts_its_weakest_link_and_keeps_the_clean_part():
    # Arrange: A and C have different domains, joined through B.
    items = members(
        Member(A, domain="parent.ca", linkedin="company/group"),
        Member(B, domain="parent.ca"),
        Member(C, domain="other.ca", linkedin="company/group"),
        Member(D, domain="other.ca"),
    )
    edges = [
        edge(A, B, "domain"),
        edge(A, C, "linkedin"),
        edge(C, D, "domain"),
    ]
    # Act
    plan = plan_clusters(items, edges, {})
    # Assert
    assert [(e.left, e.right) for e in plan.candidates] == [(A, C)]
    assert [cluster.members for cluster in plan.clusters] == [{A, B}, {C, D}]


def test_parent_and_subsidiary_sharing_a_linkedin_page_are_not_merged():
    # Arrange
    items = members(
        Member(A, domain="parent.ca", linkedin="company/parent"),
        Member(B, domain="subsidiary.ca", linkedin="company/parent"),
    )
    links = {A: LinkFacts(ORG_1), B: LinkFacts(ORG_2)}
    # Act
    plan = plan_clusters(items, [edge(A, B, "linkedin")], links)
    # Assert
    assert [e.rule for e in plan.candidates] == ["linkedin"]
    assert plan.clusters == ()


def test_weakest_link_is_cut_even_when_listed_first():
    # Arrange: A and C have different ids in one namespace; B links to both.
    items = members(
        Member(A, external_ids=frozenset({("crm", "1")})),
        Member(B),
        Member(C, external_ids=frozenset({("crm", "2")})),
    )
    edges = [edge(A, B, "name_city"), edge(B, C, "linkedin")]
    # Act
    plan = plan_clusters(items, edges, {})
    # Assert
    assert [e.rule for e in plan.candidates] == ["name_city"]
    assert [cluster.members for cluster in plan.clusters] == [{B, C}]


def test_equal_rules_are_applied_in_pair_order():
    # Arrange
    items = members(Member(A, domain="a.ca"), Member(B), Member(C, domain="c.ca"))
    edges = [edge(B, C, "linkedin"), edge(A, B, "linkedin")]
    # Act
    plan = plan_clusters(items, edges, {})
    # Assert
    assert [(e.left, e.right) for e in plan.candidates] == [(B, C)]


def test_manual_organization_is_never_merged():
    # Arrange
    items = members(*(Member(n, domain="exemple.ca") for n in (A, B, C)))
    links = {A: LinkFacts(ORG_1, manual=True), B: LinkFacts(ORG_2)}
    # Act
    plan = plan_clusters(items, [edge(A, B, "domain"), edge(B, C, "domain")], links)
    # Assert
    assert [(e.left, e.right) for e in plan.candidates] == [(A, B)]
    (cluster,) = plan.clusters
    assert cluster.organization_id == ORG_2 and cluster.members == {B, C}


def test_manual_organization_can_still_gain_an_unlinked_record():
    # Arrange
    items = members(Member(A, domain="exemple.ca"), Member(B, domain="exemple.ca"))
    links = {A: LinkFacts(ORG_1, manual=True)}
    # Act
    plan = plan_clusters(items, [edge(A, B, "domain")], links)
    # Assert
    assert plan.candidates == ()
    assert plan.clusters[0].organization_id == ORG_1
    assert [link.record_id for link in plan.clusters[0].new_links] == [B]


def test_rule_outside_tier1_never_merges_two_organizations():
    # Arrange
    items = members(Member(A), Member(B), Member(C))
    links = {A: LinkFacts(ORG_1), B: LinkFacts(ORG_2)}
    edges = [edge(A, B, "fuzzy"), edge(B, C, "fuzzy")]
    # Act
    plan = plan_clusters(items, edges, links)
    # Assert
    assert [(e.left, e.right) for e in plan.candidates] == [(A, B)]
    (cluster,) = plan.clusters
    assert cluster.organization_id == ORG_2 and cluster.merged == ()
    assert [link.record_id for link in cluster.new_links] == [C]


def test_name_city_edge_attaches_records_but_never_merges_organizations():
    # Arrange: two branches of one franchise, each already its own organization.
    items = members(Member(A), Member(B), Member(C))
    links = {A: LinkFacts(ORG_1), B: LinkFacts(ORG_2)}
    edges = [edge(A, B, "name_city"), edge(B, C, "name_city")]
    # Act
    plan = plan_clusters(items, edges, links)
    # Assert
    assert [(e.left, e.right) for e in plan.candidates] == [(A, B)]
    assert [c.organization_id for c in plan.clusters if C in c.members] == [ORG_2]


def test_rejected_pair_is_never_joined_through_a_third_record():
    # Arrange
    items = members(Member(A), Member(B), Member(C))
    edges = [edge(A, B, "domain"), edge(B, C, "linkedin")]
    # Act
    plan = plan_clusters(items, edges, {}, rejected=[(A, C)])
    # Assert
    assert [e.rule for e in plan.candidates] == ["linkedin"]
    assert [cluster.members for cluster in plan.clusters] == [{A, B}]


def test_unchanged_organization_is_not_marked_changed():
    # Arrange
    items = members(Member(A, domain="exemple.ca"), Member(B, domain="exemple.ca"))
    links = {A: LinkFacts(ORG_1), B: LinkFacts(ORG_1)}
    # Act
    plan = plan_clusters(items, [edge(A, B, "domain"), edge(A, B, "name_only")], links)
    # Assert
    assert plan.candidates == ()
    assert not plan.clusters[0].changed


def test_edges_to_unknown_records_are_ignored():
    # Act
    plan = plan_clusters(members(Member(A)), [edge(A, B, "domain")], {})
    # Assert
    assert plan == clustering.ClusterPlan((), ())


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


def test_member_drops_generic_domains_and_person_profiles():
    # Arrange
    when = datetime(2026, 11, 1, tzinfo=UTC)
    record = SourceRecord(
        id=A,
        external_id="42",
        external_ids={"crm": [" 7 "]},
        attributes={},
        name_normalized="exemple",
        domain="gmail.com",
        linkedin="in/person",
        valid_from=when,
    )
    # Act
    member = member_from_record(record, frozenset({"gmail.com"}))
    # Assert
    assert member == Member(A, "exemple", None, None, frozenset({("crm", "7")}), when)


def test_external_ids_ignore_bad_entries_but_preserve_case():
    # Arrange
    item = SourceRecord(
        id=A,
        external_ids={
            " crm ": [" ABC ", "ABC", "abc", "", None, 12],
            "neq": ["ABC"],
            " ": ["bad"],
            4: ["bad"],
            "broken": "123",
        },
    )
    # Act
    keys = clustering.external_keys(item)
    # Assert
    assert keys == {("crm", "ABC"), ("crm", "abc"), ("neq", "ABC")}
    assert clustering.external_keys(SourceRecord(id=B, external_ids=None)) == set()
