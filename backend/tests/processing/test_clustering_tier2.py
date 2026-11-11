# AI contribution: 50% or more AI-generated
"""How the planner treats Tier 2 fuzzy edges, on plain data."""

from itertools import permutations
from uuid import UUID

import pytest

from app.processing.clustering import LinkFacts, Member, plan_clusters
from app.processing.match_rules import MatchCandidateData

ORG_1, ORG_2 = UUID(int=101), UUID(int=102)
# Every order of three record ids, since the planner must not depend on them.
ID_ORDERS = list(permutations((UUID(int=1), UUID(int=2), UUID(int=3))))


def edge(left, right, rule, score=None):
    return MatchCandidateData(left, right, rule, ((rule, "value"),), score)


def members(*items):
    return {item.id: item for item in items}


def link_rules(plan):
    return {link.record_id: link.edge.rule for c in plan.clusters for link in c.new_links}


@pytest.mark.parametrize("ids", ID_ORDERS)
def test_tier1_records_are_linked_through_their_tier1_edge(ids):
    # Arrange
    x, p, q = ids
    items = members(Member(x), Member(p, domain="p.ca"), Member(q, domain="p.ca"))
    edges = [edge(p, q, "domain"), edge(x, p, "fuzzy", 0.9)]
    # Act
    plan = plan_clusters(items, edges, {})
    # Assert
    assert link_rules(plan) == {p: "domain", q: "domain", x: "fuzzy"}


@pytest.mark.parametrize("ids", ID_ORDERS)
def test_new_records_join_an_organization_through_their_strongest_edge(ids):
    # Arrange
    a, p, x = ids
    items = members(Member(a, domain="a.ca"), Member(p, domain="a.ca"), Member(x))
    edges = [edge(a, x, "fuzzy", 0.9), edge(a, p, "domain"), edge(p, x, "fuzzy", 0.95)]
    # Act
    plan = plan_clusters(items, edges, {a: LinkFacts(ORG_1)})
    # Assert
    rules = link_rules(plan)
    assert rules == {p: "domain", x: "fuzzy"}
    (cluster,) = plan.clusters
    [through] = [link for link in cluster.new_links if link.record_id == x]
    assert through.edge.score == 0.95


def test_fuzzy_edge_inside_one_group_is_neither_kept_nor_cut():
    # Arrange
    a, b, c = UUID(int=1), UUID(int=2), UUID(int=3)
    items = members(*(Member(n, domain="d.ca") for n in (a, b, c)))
    edges = [edge(a, b, "domain"), edge(b, c, "domain"), edge(a, c, "fuzzy", 0.9)]
    # Act
    plan = plan_clusters(items, edges, {})
    # Assert
    assert plan.candidates == ()
    assert set(link_rules(plan).values()) == {"domain"}


A, B, C, D, X = (UUID(int=n) for n in (1, 2, 3, 4, 9))


def cluster_of(plan, record_id):
    return next((c for c in plan.clusters if record_id in c.members), None)


def test_two_unlinked_records_form_a_new_tentative_organization():
    # Arrange
    items = members(Member(A), Member(B))
    # Act
    plan = plan_clusters(items, [edge(A, B, "fuzzy", 0.9)], {})
    # Assert
    (cluster,) = plan.clusters
    assert cluster.members == {A, B} and cluster.tentative == {A, B}


def test_fuzzy_edges_do_not_chain():
    # Arrange
    # Marin is close to Martin and to Martine, but Martin and Martine are not close.
    items = members(Member(A), Member(B), Member(C))
    edges = [edge(A, B, "fuzzy", 0.95), edge(B, C, "fuzzy", 0.9)]
    # Act
    plan = plan_clusters(items, edges, {})
    # Assert
    assert [c.members for c in plan.clusters] == [{A, B}]
    assert [(e.left, e.right) for e in plan.candidates] == [(B, C)]


def test_fuzzy_edge_attaches_a_record_to_a_tier1_member():
    # Arrange
    items = members(Member(A), Member(X))
    # Act
    plan = plan_clusters(items, [edge(A, X, "fuzzy", 0.9)], {A: LinkFacts(ORG_1)})
    # Assert
    (cluster,) = plan.clusters
    assert cluster.organization_id == ORG_1 and cluster.tentative == {X}


def test_fuzzy_edge_attaches_to_a_group_formed_by_tier1_in_this_run():
    # Arrange
    items = members(Member(A, domain="a.ca"), Member(B, domain="a.ca"), Member(X))
    # Act
    plan = plan_clusters(items, [edge(A, B, "domain"), edge(B, X, "fuzzy", 0.9)], {})
    # Assert
    (cluster,) = plan.clusters
    assert cluster.members == {A, B, X} and cluster.tentative == {X}


def test_a_fuzzy_linked_record_cannot_anchor_another_fuzzy_edge():
    # Arrange
    items = members(Member(A), Member(B), Member(X))
    links = {A: LinkFacts(ORG_1), B: LinkFacts(ORG_1, tentative=True)}
    # Act
    plan = plan_clusters(items, [edge(B, X, "fuzzy", 0.9)], links)
    # Assert
    assert [(e.left, e.right) for e in plan.candidates] == [(B, X)]
    assert cluster_of(plan, X) is None


def test_fuzzy_edge_never_joins_two_groups_of_several_records():
    # Arrange
    items = members(
        Member(A, domain="a.ca"),
        Member(B, domain="a.ca"),
        Member(C, linkedin="company/c"),
        Member(D, linkedin="company/c"),
    )
    edges = [edge(A, B, "domain"), edge(C, D, "linkedin"), edge(B, C, "fuzzy", 0.9)]
    # Act
    plan = plan_clusters(items, edges, {})
    # Assert
    assert [(e.left, e.right) for e in plan.candidates] == [(B, C)]
    assert [c.members for c in plan.clusters] == [{A, B}, {C, D}]


@pytest.mark.parametrize(
    "joiner",
    [Member(X, cities=frozenset({"quebec"})), Member(X, email_domains=frozenset({"x.ca"}))],
)
def test_fuzzy_edge_is_cut_when_the_record_disagrees_with_another_member(joiner):
    # Arrange
    items = members(
        Member(A, cities=frozenset({"quebec"}), email_domains=frozenset({"x.ca"})),
        Member(B, cities=frozenset({"levis"}), email_domains=frozenset({"b.ca"})),
        joiner,
    )
    links = {A: LinkFacts(ORG_1), B: LinkFacts(ORG_1)}
    # Act
    plan = plan_clusters(items, [edge(A, X, "fuzzy", 0.9)], links)
    # Assert
    assert [(e.left, e.right) for e in plan.candidates] == [(A, X)]


def test_a_tentative_member_does_not_block_a_fuzzy_edge_by_its_city():
    # Arrange
    items = members(
        Member(A, cities=frozenset({"quebec"})),
        Member(B, cities=frozenset({"levis"})),
        Member(X, cities=frozenset({"quebec"})),
    )
    links = {A: LinkFacts(ORG_1), B: LinkFacts(ORG_1, tentative=True)}
    # Act
    plan = plan_clusters(items, [edge(A, X, "fuzzy", 0.9)], links)
    # Assert
    assert plan.candidates == () and X in cluster_of(plan, A).members


def test_record_reached_through_a_tentative_member_cannot_bridge_two_organizations():
    # Arrange
    a2, c2 = UUID(int=11), UUID(int=13)
    items = members(
        Member(A),
        Member(a2),
        Member(B, linkedin="company/q"),
        Member(C, external_ids=frozenset({("crm", "2")})),
        Member(c2, external_ids=frozenset({("crm", "2")})),
        Member(X, linkedin="company/q", external_ids=frozenset({("crm", "2")})),
    )
    links = {
        A: LinkFacts(ORG_1),
        a2: LinkFacts(ORG_1),
        B: LinkFacts(ORG_1, tentative=True),
        C: LinkFacts(ORG_2),
        c2: LinkFacts(ORG_2),
    }
    edges = [edge(B, X, "linkedin"), edge(C, X, "external_id"), edge(c2, X, "external_id")]
    # Act
    plan = plan_clusters(items, edges, links)
    # Assert
    # X joins through B's linkedin first, so its crm id may not merge ORG_1 into ORG_2.
    assert all(c.merged == () for c in plan.clusters)
    assert {(e.left, e.right) for e in plan.candidates} == {(C, X), (c2, X)}
    assert cluster_of(plan, X).organization_id == ORG_1


def test_a_record_attached_through_a_tentative_member_is_tentative():
    # Arrange
    items = members(Member(A), Member(B, domain="b.ca"), Member(X, domain="b.ca"))
    links = {A: LinkFacts(ORG_1), B: LinkFacts(ORG_1, tentative=True)}
    # Act
    plan = plan_clusters(items, [edge(B, X, "domain")], links)
    # Assert
    (cluster,) = plan.clusters
    assert cluster.tentative == {B, X}


def test_a_group_attached_through_a_tentative_member_is_tentative():
    # Arrange
    items = members(
        Member(A),
        Member(B, domain="b.ca"),
        Member(C, domain="b.ca", linkedin="company/c"),
        Member(D, linkedin="company/c"),
    )
    links = {A: LinkFacts(ORG_1), B: LinkFacts(ORG_1, tentative=True)}
    edges = [edge(C, D, "linkedin"), edge(B, C, "domain")]
    # Act
    plan = plan_clusters(items, edges, links)
    # Assert
    (cluster,) = plan.clusters
    assert cluster.tentative == {B, C, D}


def test_a_record_attached_through_a_tier1_member_is_not_tentative():
    # Arrange
    items = members(Member(A, domain="a.ca"), Member(B), Member(X, domain="a.ca"))
    links = {A: LinkFacts(ORG_1), B: LinkFacts(ORG_1, tentative=True)}
    # Act
    plan = plan_clusters(items, [edge(A, X, "domain")], links)
    # Assert
    (cluster,) = plan.clusters
    assert cluster.tentative == {B}


@pytest.mark.parametrize("ids", ID_ORDERS)
def test_equally_close_fuzzy_edges_into_two_organizations_attach_neither(ids):
    # Arrange
    a, c, x = ids
    items = members(Member(a), Member(c), Member(x))
    links = {a: LinkFacts(ORG_1), c: LinkFacts(ORG_2)}
    edges = [edge(x, c, "fuzzy", 0.95), edge(a, x, "fuzzy", 0.95)]
    # Act
    plan = plan_clusters(items, edges, links)
    # Assert
    assert len(plan.candidates) == 2
    assert all(x not in c.members for c in plan.clusters)


@pytest.mark.parametrize("ids", ID_ORDERS)
def test_equally_close_fuzzy_edges_to_two_lone_records_join_none(ids):
    # Arrange
    x, y, z = ids
    items = members(Member(x), Member(y), Member(z))
    edges = [edge(x, y, "fuzzy", 0.9), edge(x, z, "fuzzy", 0.9)]
    # Act
    plan = plan_clusters(items, edges, {})
    # Assert
    assert plan.clusters == () and len(plan.candidates) == 2


@pytest.mark.parametrize("ids", ID_ORDERS)
def test_equal_name_and_city_edges_into_two_organizations_still_attach_neither(ids):
    # Arrange
    a, c, x = ids
    items = members(Member(a), Member(c), Member(x))
    links = {a: LinkFacts(ORG_1), c: LinkFacts(ORG_2)}
    # Act
    plan = plan_clusters(items, [edge(x, c, "name_city"), edge(a, x, "name_city")], links)
    # Assert
    assert len(plan.candidates) == 2
