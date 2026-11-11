# AI contribution: 50% or more AI-generated
"""Group records with Tier 1 and Tier 2, as the matcher does, for the dev-only report."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from app.models import SourceRecord
from app.processing.clustering import member_from_record, plan_clusters
from app.processing.entity_resolution import blocked_matches, load_generic_domains
from app.processing.fuzzy_config import FuzzyConfig
from app.processing.fuzzy_matching import fuzzy_matches
from app.processing.match_rules import FUZZY_RULES, MatchCandidateData

# The record columns the report reads, for building and copying unsaved records.
RECORD_FIELDS = (
    "external_id",
    "external_ids",
    "attributes",
    "name_normalized",
    "domain",
    "linkedin",
    "city",
)


@dataclass(frozen=True)
class Grouping:
    """External ids grouped into organizations, and what the Tier 2 edges did."""

    groups: tuple[frozenset[str], ...] = ()
    # Records the plan links through a Tier 2 edge.
    tier2_linked: int = 0
    # Tier 2 pairs set aside for review.
    tier2_review: int = 0
    # Pairs that the bounded blocking did not compare.
    pairs_left_out: int = 0


def organization_record(record_id: UUID, tenant_id: UUID, **fields: object) -> SourceRecord:
    return SourceRecord(id=record_id, tenant_id=tenant_id, kind="organization", **fields)


def copy_record(record: SourceRecord, **changes: object) -> SourceRecord:
    fields = {name: getattr(record, name) for name in RECORD_FIELDS}
    return organization_record(record.id, record.tenant_id, **(fields | changes))


def _together(cluster_of: Mapping[UUID, int], edge: MatchCandidateData) -> bool:
    return edge.left in cluster_of and cluster_of[edge.left] == cluster_of.get(edge.right)


def grouping(
    records: Sequence[SourceRecord], edges: Sequence[MatchCandidateData], pairs_left_out: int = 0
) -> Grouping:
    """Plan the edges as match_organizations does, on records that have no links yet."""
    by_id = {record.id: record for record in records}
    excluded = load_generic_domains()
    members = {record.id: member_from_record(record, excluded) for record in records}
    plan = plan_clusters(members, edges, {})
    cluster_of = {
        key: index for index, cluster in enumerate(plan.clusters) for key in cluster.members
    }
    cut = {edge.providePair for edge in plan.candidates}
    tier2 = [edge for edge in edges if edge.rule in FUZZY_RULES]
    return Grouping(
        groups=tuple(
            frozenset(by_id[key].external_id for key in cluster.members)
            for cluster in plan.clusters
        ),
        tier2_linked=sum(
            link.edge.rule in FUZZY_RULES for cluster in plan.clusters for link in cluster.new_links
        ),
        tier2_review=sum(
            edge.providePair in cut or (not edge.strong and not _together(cluster_of, edge))
            for edge in tier2
        ),
        pairs_left_out=pairs_left_out,
    )


def tier_groupings(
    records: Sequence[SourceRecord], config: FuzzyConfig
) -> tuple[Grouping, Grouping]:
    """Tier 1 alone, then Tier 1 plus Tier 2."""
    excluded = load_generic_domains()
    tier1 = blocked_matches(records, excluded)
    tier2 = fuzzy_matches(records, excluded, {edge.providePair for edge in tier1}, config)
    return (
        grouping(records, tier1),
        grouping(records, [*tier1, *tier2.matches], tier2.blocking.pairs_left_out),
    )
