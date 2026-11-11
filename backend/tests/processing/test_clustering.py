# AI contribution: 50% or more AI-generated
"""Clustering plans built from plain data, without a database."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.models import SourceRecord
from app.processing import clustering
from app.processing.clustering import (
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
    assert [(c.members, c.merged) for c in plan.clusters] == [({A}, ()), ({B}, ())]


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


def test_chain_whose_ends_conflict_cuts_the_edge_with_the_larger_pair():
    # Arrange: no record touches both ends, so this is not a tie.
    items = members(Member(A, domain="a.ca"), Member(B), Member(C), Member(D, domain="d.ca"))
    edges = [edge(C, D, "linkedin"), edge(A, B, "linkedin"), edge(B, C, "linkedin")]
    # Act
    plan = plan_clusters(items, edges, {})
    # Assert
    assert [(e.left, e.right) for e in plan.candidates] == [(C, D)]
    assert [cluster.members for cluster in plan.clusters] == [{A, B, C}]


def test_manual_organization_is_never_merged():
    # Arrange
    items = members(*(Member(n, domain="exemple.ca") for n in (A, B, C)))
    links = {A: LinkFacts(ORG_1, manual=True), B: LinkFacts(ORG_2)}
    # Act
    plan = plan_clusters(items, [edge(A, B, "domain"), edge(B, C, "domain")], links)
    # Assert
    assert [(e.left, e.right) for e in plan.candidates] == [(A, B)]
    assert [(c.organization_id, c.members) for c in plan.clusters] == [
        (ORG_1, {A}),
        (ORG_2, {B, C}),
    ]


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
    cluster = plan.clusters[1]
    assert cluster.organization_id == ORG_2 and cluster.merged == ()
    assert [link.record_id for link in cluster.new_links] == [C]
    assert all(not c.merged for c in plan.clusters)


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


ORDERS = pytest.mark.parametrize("ids", [(A, B, C), (C, B, A), (B, A, C)])


@ORDERS
def test_parent_sharing_a_domain_with_two_conflicting_groups_joins_neither(ids):
    # Arrange
    parent, radio, tele = ids
    items = members(
        Member(parent, domain="groupe-medias.ca"),
        Member(radio, domain="groupe-medias.ca", linkedin="company/radio-nord"),
        Member(tele, domain="groupe-medias.ca", linkedin="company/tele-nord"),
    )
    edges = [
        edge(*sorted(pair), "domain") for pair in ((parent, radio), (parent, tele), (radio, tele))
    ]
    # Act
    plan = plan_clusters(items, edges, {})
    # Assert
    assert plan.clusters == ()
    assert sorted(e.providePair for e in plan.candidates) == sorted(e.providePair for e in edges)


@ORDERS
def test_tie_between_two_existing_organizations_links_the_record_to_neither(ids):
    # Arrange
    record, radio, tele = ids
    items = members(
        Member(record, domain="groupe-medias.ca"),
        Member(radio, domain="groupe-medias.ca", linkedin="company/radio-nord"),
        Member(tele, domain="groupe-medias.ca", linkedin="company/tele-nord"),
    )
    links = {radio: LinkFacts(ORG_1), tele: LinkFacts(ORG_2)}
    edges = [edge(*sorted(pair), "domain") for pair in ((record, radio), (record, tele))]
    # Act
    plan = plan_clusters(items, edges, links)
    # Assert
    assert all(not cluster.new_links for cluster in plan.clusters)
    assert len(plan.candidates) == 2


@ORDERS
def test_record_holding_two_aliases_of_disagreeing_records_joins_neither(ids):
    # Arrange
    bridge, first, second = ids
    items = members(
        Member(bridge, external_ids=frozenset({("neq", "1"), ("neq", "2")})),
        Member(first, external_ids=frozenset({("neq", "1")})),
        Member(second, external_ids=frozenset({("neq", "2")})),
    )
    edges = [edge(*sorted(pair), "external_id") for pair in ((bridge, first), (bridge, second))]
    # Act
    plan = plan_clusters(items, edges, {})
    # Assert
    assert plan.clusters == ()
    assert len(plan.candidates) == 2


@ORDERS
def test_record_that_can_only_join_one_side_is_not_a_tie(ids):
    # Arrange
    record, same, other = ids
    items = members(
        Member(record, domain="groupe-medias.ca", linkedin="company/radio-nord"),
        Member(same, domain="groupe-medias.ca", linkedin="company/radio-nord"),
        Member(other, domain="groupe-medias.ca", linkedin="company/tele-nord"),
    )
    edges = [edge(*sorted(pair), "domain") for pair in ((record, same), (record, other))]
    # Act
    plan = plan_clusters(items, edges, {})
    # Assert
    assert [cluster.members for cluster in plan.clusters] == [{record, same}]
    assert [set(e.providePair) for e in plan.candidates] == [{record, other}]


def test_link_still_on_a_merged_organization_is_repointed():
    # Arrange: A's stored link points at ORG_3, which was merged into ORG_1.
    items = members(Member(A), Member(B))
    links = {A: LinkFacts(ORG_1, stored_organization_id=ORG_3), B: LinkFacts(ORG_1)}
    # Act
    plan = plan_clusters(items, [], links)
    # Assert
    (cluster,) = plan.clusters
    assert cluster.organization_id == ORG_1 and cluster.repointed == (A,)
    assert cluster.merged == () and cluster.changed


def test_lone_record_on_a_merged_organization_is_repointed():
    # Arrange
    items = members(Member(A))
    links = {A: LinkFacts(ORG_1, stored_organization_id=ORG_3)}
    # Act
    plan = plan_clusters(items, [], links)
    # Assert
    assert [(c.members, c.repointed) for c in plan.clusters] == [({A}, (A,))]


def test_merge_repoints_the_links_of_the_merged_organization():
    # Arrange
    items = members(*(Member(n, domain="exemple.ca") for n in (A, B, C)))
    links = {A: LinkFacts(ORG_2), B: LinkFacts(ORG_1), C: LinkFacts(ORG_1)}
    # Act
    plan = plan_clusters(items, [edge(A, B, "domain")], links)
    # Assert
    assert plan.clusters[0].repointed == (A,)
