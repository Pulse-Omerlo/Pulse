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
