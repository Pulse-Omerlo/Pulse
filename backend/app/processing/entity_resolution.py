# AI contribution: Below 50% AI-generated
import tomllib
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import cache
from itertools import combinations
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import case, select, update
from sqlalchemy.orm import Session, aliased

from app.core.logging import get_logger
from app.core.tenancy import bound_to_tenant
from app.models import MatchCandidate, Organization, OrgLink, SourceRecord, Tenant
from app.models.resolution import RuleEvidence

# The LinkedIn prefix stays importable from here for existing callers.
from app.processing.clustering import LINKEDIN_COMPANY_PREFIX as LINKEDIN_COMPANY_PREFIX
from app.processing.clustering import (
    Cluster,
    LinkFacts,
    NewLink,
    company_linkedin,
    external_keys,
    member_from_record,
    organization_domain,
    plan_clusters,
)
from app.processing.fuzzy_matching import FuzzyResult, fuzzy_matches
from app.processing.match_rules import (
    FUZZY_RULES,
    MatchCandidateData,
    Pair,
    candidate_score,
    decided_by,
    link_confidence,
    rule_set_version,
    strength_key,
)

# Rule names stay importable from here for existing callers.
from app.processing.match_rules import RULE_PRIORITIES as RULE_PRIORITIES
from app.processing.match_rules import RULE_VERSION as RULE_VERSION
from app.processing.match_rules import RULE_WEIGHTS as RULE_WEIGHTS

logger = get_logger(__name__)

# Get the domains from the config file
GENERIC_DOMAINS_PATH = Path(__file__).with_name("generic_domains.toml")

# Blocking key for a match candidate, used to group match candidates by the fields that matched
Key = tuple[str, ...]


# Class for the result of a match resolution, containing the number of organizations, links, and
# candidates created
# Rerunning the matching on unchanged data should not create new organizations, links, or
# candidates, so these counts should be zero if the matching is idempotent
@dataclass
class MatchResult:
    # Counts new organizations.
    organizations_created: int = 0
    # Counts new links between organizations.
    links_created: int = 0
    # Counts new pairs set aside for review.
    candidates_created: int = 0
    # Counts organizations merged into another one.
    organizations_merged: int = 0


# Function to load the generic domains from the config file, used to filter out matches on generic
# domains
@cache
def load_generic_domains() -> frozenset[str]:
    with GENERIC_DOMAINS_PATH.open("rb") as handle:
        values = tomllib.load(handle).get("domains")
    if (
        not isinstance(values, list)
        or not values
        # An entry is invalid if it is not a string or if it is an empty string or whitespace only
        # string. This is to avoid matching on generic domains like "example.com" or "test.com"
        # which are not useful for matching.
        or any(not isinstance(value, str) or not value.strip() for value in values)
    ):
        raise ValueError("generic_domains.toml needs a non-empty domains list")
    # return the validated domains in a consistent, immutable collection
    return frozenset(value.strip().lower() for value in values)


# Produces a sequence of blocking keys for a record, used to group records by the fields that match
# AI Generated
def blocking_keys(
    record: SourceRecord,
    excluded_domains: frozenset[str],
) -> Iterable[Key]:
    domain = organization_domain(record, excluded_domains)
    if domain:
        yield "domain", domain
    linkedin = company_linkedin(record)
    if linkedin:
        yield "linkedin", linkedin
    for namespace, value in sorted(external_keys(record)):
        yield "external_id", namespace, value
    if record.name_normalized:
        yield "name", record.name_normalized


# Function to classify a pair of source records, used to identify matches based on the matching
# rules
def classify_pair(
    left: SourceRecord,
    right: SourceRecord,
    excluded_domains: frozenset[str],
) -> MatchCandidateData | None:
    # Return the first matching rule, or weaker same-name evidence.
    # Reject pairs that are not comparable, or that have already been invalidated.
    if (
        # Reject pairs that are the same record, or that belong to different tenants, or that are
        # not organizations, or that have been invalidated.
        left.id == right.id
        or left.tenant_id != right.tenant_id
        or left.kind != "organization"
        or right.kind != "organization"
        or left.valid_to is not None
        or right.valid_to is not None
    ):
        return None
    left, right = sorted((left, right), key=lambda record: record.id)
    # Match domain
    domain = organization_domain(left, excluded_domains)
    if domain and domain == right.domain:
        return MatchCandidateData(left.id, right.id, "domain", (("domain", domain),))
    # Match company LinkedIn page
    linkedin = company_linkedin(left)
    if linkedin and linkedin == right.linkedin:
        return MatchCandidateData(left.id, right.id, "linkedin", (("linkedin", linkedin),))
    # Match external identifiers using set intersection to find shared identifiers
    shared_ids = external_keys(left) & external_keys(right)
    if shared_ids:
        namespace, value = min(shared_ids)
        return MatchCandidateData(
            left.id,
            right.id,
            "external_id",
            ((f"external_ids.{namespace}", value),),
        )
    # Match normalized names, with matching cities
    if left.name_normalized and left.name_normalized == right.name_normalized:
        fields = (("name_normalized", left.name_normalized),)
        if left.city and left.city == right.city:
            return MatchCandidateData(
                left.id,
                right.id,
                "name_city",
                (*fields, ("city", left.city)),
            )
        return MatchCandidateData(left.id, right.id, "name_only", fields)
    # No matching rule applies.
    return None


# Function to find plausible record pairs, classify the pairs and returning the matcching evidence
# AI generated
def blocked_matches(
    records: Iterable[SourceRecord],
    excluded_domains: frozenset[str],
) -> list[MatchCandidateData]:
    """Compare shared-key pairs once, rather than every possible pair."""
    records = {
        record.id: record
        for record in records
        if record.kind == "organization" and record.valid_to is None
    }
    blocks = defaultdict(list)
    for record in records.values():
        for key in blocking_keys(record, excluded_domains):
            blocks[(record.tenant_id, *key)].append(record.id)

    pairs = {
        tuple(sorted(pair)) for members in blocks.values() for pair in combinations(members, 2)
    }

    matches = []
    for left, right in sorted(pairs):
        match = classify_pair(records[left], records[right], excluded_domains)
        if match:
            matches.append(match)

    return sorted(matches, key=strength_key)


# Function to generate evidence for a match candidate, used to provide context for the matching
# decision
def evidence(
    match: MatchCandidateData, matched_record_id: UUID, tentative: bool = False
) -> list[RuleEvidence]:
    # Sides are written from this record's point of view: its value first.
    this_is_left = matched_record_id == match.right
    values = [(field, value, value) for field, value in match.fields] + [
        (field, left, right) if this_is_left else (field, right, left)
        for field, left, right in match.sides
    ]
    marker: dict[str, bool] = {"tentative": True} if tentative else {}
    return [
        {
            "rule": match.rule,
            "rule_set_version": rule_set_version(match.rule),
            "field": field,
            "value": value,
            "matched_value": matched_value,
            "matched_record_id": str(matched_record_id),
            **marker,
        }
        for field, value, matched_value in values
    ]


def is_tentative(link: OrgLink) -> bool:
    """Whether a stored link rests on a fuzzy match; versioning copies the evidence."""
    if link.method in FUZZY_RULES:
        return True
    return any(isinstance(entry, dict) and entry.get("tentative") for entry in link.evidence or [])


def log_tier2(tenant_id: UUID, result: FuzzyResult) -> None:
    """How much Tier 2 compared, and what its bounds left out, for big tenants."""
    logger.info(
        "tier2_matching",
        tenant_id=str(tenant_id),
        matches=len(result.matches),
        comparisons=result.comparisons,
        split_blocks=result.blocking.split_blocks,
        windowed_blocks=result.blocking.windowed_blocks,
        pairs_left_out=result.blocking.pairs_left_out,
    )


# State of one matching run: the rows it loaded and what it wrote so far.
@dataclass
class _Run:
    session: Session
    tenant_id: UUID
    links: dict[UUID, OrgLink]
    decisions: dict[Pair, MatchCandidate]
    organizations: dict[UUID, Organization]
    # Organization merged in this run -> the one it was merged into.
    merges: dict[UUID, UUID] = field(default_factory=dict)
    result: MatchResult = field(default_factory=MatchResult)


def _current_records(session: Session, tenant_id: UUID) -> dict[UUID, SourceRecord]:
    query = (
        select(SourceRecord)
        .where(
            SourceRecord.tenant_id == tenant_id,
            SourceRecord.kind == "organization",
            SourceRecord.valid_to.is_(None),
        )
        .order_by(SourceRecord.id)
    )
    return {record.id: record for record in session.scalars(query)}


def _current_links(session: Session, tenant_id: UUID) -> dict[UUID, OrgLink]:
    # Joining current records avoids loading historical link versions.
    query = (
        select(OrgLink)
        .join(SourceRecord, SourceRecord.id == OrgLink.source_record_id)
        .where(
            OrgLink.tenant_id == tenant_id,
            SourceRecord.tenant_id == tenant_id,
            SourceRecord.kind == "organization",
            SourceRecord.valid_to.is_(None),
        )
    )
    return {link.source_record_id: link for link in session.scalars(query)}


def _decisions(session: Session, tenant_id: UUID) -> dict[Pair, MatchCandidate]:
    """Candidates between two current organization records; older versions are skipped."""
    left, right = aliased(SourceRecord), aliased(SourceRecord)
    query = (
        select(MatchCandidate)
        .join(left, left.id == MatchCandidate.left_record_id)
        .join(right, right.id == MatchCandidate.right_record_id)
        .where(
            MatchCandidate.tenant_id == tenant_id,
            *(
                condition
                for record in (left, right)
                for condition in (
                    record.tenant_id == tenant_id,
                    record.kind == "organization",
                    record.valid_to.is_(None),
                )
            ),
        )
    )
    return {
        (candidate.left_record_id, candidate.right_record_id): candidate
        for candidate in session.scalars(query)
    }


def _manual_organizations(session: Session, tenant_id: UUID) -> set[UUID]:
    """Organizations with a manual link on any version, current or not."""
    query = (
        select(OrgLink.organization_id)
        .where(OrgLink.tenant_id == tenant_id, OrgLink.method == "manual")
        .distinct()
    )
    return set(session.scalars(query))


def _organizations(session: Session, tenant_id: UUID, ids: set[UUID]) -> dict[UUID, Organization]:
    """The linked organizations, plus the ones they were merged into."""
    found = {}
    while ids:
        query = select(Organization).where(
            Organization.tenant_id == tenant_id, Organization.id.in_(sorted(ids))
        )
        found |= {organization.id: organization for organization in session.scalars(query)}
        ids = {o.merged_into for o in found.values() if o.merged_into} - found.keys()
    return found


def _link_facts(run: _Run) -> dict[UUID, LinkFacts]:
    facts = {}
    for record_id, link in run.links.items():
        organization = run.organizations.get(link.organization_id)
        target = organization.merged_into if organization and organization.merged_into else None
        facts[record_id] = LinkFacts(
            target or link.organization_id,
            manual=link.method == "manual",
            stored_organization_id=link.organization_id if target else None,
            tentative=is_tentative(link),
        )
    return facts


def _add_candidate(run: _Run, match: MatchCandidateData) -> None:
    if match.providePair in run.decisions:
        return
    left, right = match.providePair
    candidate = MatchCandidate(
        tenant_id=run.tenant_id,
        left_record_id=left,
        right_record_id=right,
        score=candidate_score(match),
        matched_fields=evidence(match, match.right),
        status="pending",
    )
    run.session.add(candidate)
    run.decisions[match.providePair] = candidate
    run.result.candidates_created += 1


def _new_organization(run: _Run, cluster: Cluster) -> Organization:
    organization = Organization(
        id=uuid4(),
        tenant_id=run.tenant_id,
        display_name=cluster.display_name or "",
        primary_domain=cluster.primary_domain,
    )
    run.session.add(organization)
    # Organization must exist before links reference it.
    run.session.flush()
    run.organizations[organization.id] = organization
    run.result.organizations_created += 1
    return organization


def _merge(run: _Run, merged_id: UUID, survivor_id: UUID) -> None:
    run.organizations[merged_id].merged_into = survivor_id
    run.merges[merged_id] = survivor_id
    run.result.organizations_merged += 1


def _repoint_earlier_merges(run: _Run) -> None:
    """Point organizations merged earlier into one merged in this run at its survivor.

    One statement for the whole run, so merged_into stays one hop.
    """
    if not run.merges:
        return
    run.session.execute(
        update(Organization)
        .where(
            Organization.tenant_id == run.tenant_id,
            Organization.merged_into.in_(sorted(run.merges)),
        )
        .values(merged_into=case(run.merges, value=Organization.merged_into))
        .execution_options(synchronize_session="fetch")
    )


def _add_link(run: _Run, new_link: NewLink, organization_id: UUID, tentative: bool) -> None:
    match = new_link.edge
    link = OrgLink(
        tenant_id=run.tenant_id,
        source_record_id=new_link.record_id,
        organization_id=organization_id,
        method=match.rule,
        confidence=link_confidence(match),
        decided_by=decided_by(match),
        evidence=evidence(match, new_link.neighbor, tentative),
    )
    run.session.add(link)
    run.links[new_link.record_id] = link
    run.result.links_created += 1


def _apply(run: _Run, cluster: Cluster) -> None:
    if cluster.organization_id is None:
        organization = _new_organization(run, cluster)
    else:
        organization = run.organizations[cluster.organization_id]
    for merged_id in cluster.merged:
        _merge(run, merged_id, organization.id)
    # Moves the current links of merged organizations; closed versions keep theirs.
    for record_id in cluster.repointed:
        run.links[record_id].organization_id = organization.id
    for new_link in cluster.new_links:
        _add_link(run, new_link, organization.id, new_link.record_id in cluster.tentative)
    # A value is only written when it changes, and never replaced by nothing.
    if cluster.display_name and cluster.display_name != organization.display_name:
        organization.display_name = cluster.display_name
    if cluster.primary_domain and cluster.primary_domain != organization.primary_domain:
        organization.primary_domain = cluster.primary_domain


def has_changed_record(record_ids: Iterable[UUID], changed_ids: set[UUID] | None) -> bool:
    """Whether one of the records changed; with no changed_ids every record counts."""
    return changed_ids is None or not changed_ids.isdisjoint(record_ids)


# Loads a tenant's current organization records and past decisions, plans the clusters, then
# creates or merges organizations, links and review candidates in the caller's transaction.
# With changed_ids, only clusters and pairs holding one of those records are applied; the other
# current records are still loaded so the changed ones can be compared to them.
def match_organizations(
    session: Session, tenant_id: UUID, changed_ids: set[UUID] | None = None
) -> MatchResult:
    with bound_to_tenant(session, tenant_id):
        # Serialize matcher invocations for this tenant.
        tenant = session.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
        if tenant is None:
            raise ValueError(f"Unknown tenant {tenant_id}")

        session.flush()
        records = _current_records(session, tenant_id)
        links = _current_links(session, tenant_id)
        run = _Run(
            session=session,
            tenant_id=tenant_id,
            links=links,
            decisions=_decisions(session, tenant_id),
            organizations=_organizations(
                session, tenant_id, {link.organization_id for link in links.values()}
            ),
        )

        generic_domains = load_generic_domains()
        matches = blocked_matches(records.values(), generic_domains)
        tier1_pairs = {match.providePair for match in matches}
        tier2 = fuzzy_matches(records.values(), generic_domains, tier1_pairs)
        log_tier2(tenant_id, tier2)
        matches += tier2.matches
        plan = plan_clusters(
            {key: member_from_record(record, generic_domains) for key, record in records.items()},
            matches,
            _link_facts(run),
            [pair for pair, decision in run.decisions.items() if decision.status == "rejected"],
            _manual_organizations(session, tenant_id),
        )
        for cluster in plan.clusters:
            if has_changed_record(cluster.members, changed_ids):
                _apply(run, cluster)
        _repoint_earlier_merges(run)
        for match in plan.candidates:
            if has_changed_record(match.providePair, changed_ids):
                _add_candidate(run, match)

        for match in matches:
            if match.strong or not has_changed_record(match.providePair, changed_ids):
                continue
            left, right = run.links.get(match.left), run.links.get(match.right)
            if left and right and left.organization_id == right.organization_id:
                continue
            _add_candidate(run, match)

        session.flush()
        return run.result
