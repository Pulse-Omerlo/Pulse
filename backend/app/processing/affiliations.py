# AI contribution: 50% or more AI-generated
"""Work out which organization each person works for, from the signals on their record.

Three signals, strongest first:
- relation: the record names an organization of the same source (a file import's
  `organization_id` column). Each one is linked, so a person can have several.
- email_domain: a work email whose domain is an organization's domain.
- job_title: an organization's full name inside the job title.

The two inferred signals are only used when the record has no relation. When they point
at one organization it is linked; when they point at more than one, nothing is linked
and every candidate is kept as a conflict for review. Generic and personal email domains
(gmail.com, videotron.ca...) never create a link.
"""

import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from app.models.affiliation import AffiliationEvidence
from app.processing.normalization import email_domain, normalize_domain, normalize_organization_name

RELATION = "relation"
EMAIL_DOMAIN = "email_domain"
JOB_TITLE = "job_title"
CONFIDENCE = {RELATION: 1.0, EMAIL_DOMAIN: 0.9, JOB_TITLE: 0.6}
STRENGTH = (RELATION, EMAIL_DOMAIN, JOB_TITLE)

LINKED = "linked"
CONFLICT = "conflict"

# Personal mail providers people in Quebec use; added to the generic domains of matching.
PERSONAL_EMAIL_DOMAINS = frozenset(
    {
        "aol.com",
        "bell.net",
        "cgocable.ca",
        "globetrotter.net",
        "gmx.com",
        "hotmail.ca",
        "hotmail.fr",
        "live.ca",
        "me.com",
        "msn.com",
        "outlook.fr",
        "proton.me",
        "protonmail.com",
        "sympatico.ca",
        "videotron.ca",
        "yahoo.ca",
        "yahoo.fr",
    }
)
# A single-word organization name shorter than this is too likely to be an ordinary word.
MIN_SINGLE_WORD_NAME = 5


@dataclass(frozen=True)
class OrganizationFacts:
    record_id: uuid.UUID
    # The matched organization's id, or the record's own id when it matched none.
    key: uuid.UUID
    source_id: uuid.UUID
    external_id: str
    name_normalized: str | None
    domains: frozenset[str]


@dataclass(frozen=True)
class PersonFacts:
    record_id: uuid.UUID
    source_id: uuid.UUID
    emails: tuple[str, ...] = ()
    job_titles: tuple[str, ...] = ()
    # External ids of organizations of the same source the record names.
    organization_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class AffiliationDecision:
    person_record_id: uuid.UUID
    organization_record_id: uuid.UUID
    status: str
    method: str
    confidence: float
    evidence: tuple[AffiliationEvidence, ...]


@dataclass
class _Candidate:
    """Everything pointing a person at one organization."""

    records: set[uuid.UUID] = field(default_factory=set)
    evidence: list[AffiliationEvidence] = field(default_factory=list)

    @property
    def method(self) -> str:
        signals = {item["signal"] for item in self.evidence}
        return next(method for method in STRENGTH if method in signals)

    def add(self, record_id: uuid.UUID, item: AffiliationEvidence) -> None:
        self.records.add(record_id)
        if item not in self.evidence:
            self.evidence.append(item)


# The candidate organizations of one person, by organization key.
type Candidates = dict[uuid.UUID, _Candidate]


class OrganizationIndex:
    """The tenant's current organizations, looked up by each signal."""

    def __init__(self, organizations: Iterable[OrganizationFacts], excluded: frozenset[str]):
        self.by_ref: dict[tuple[uuid.UUID, str], list[OrganizationFacts]] = defaultdict(list)
        self.by_domain: dict[str, list[OrganizationFacts]] = defaultdict(list)
        # Names that can be found in a job title, by their first word.
        self.by_first_word: dict[str, list[tuple[tuple[str, ...], OrganizationFacts]]] = (
            defaultdict(list)
        )
        self.excluded = excluded
        for organization in organizations:
            self.by_ref[(organization.source_id, organization.external_id)].append(organization)
            for domain in organization.domains - excluded:
                self.by_domain[domain].append(organization)
            words = tuple((organization.name_normalized or "").split())
            if findable_name(words):
                self.by_first_word[words[0]].append((words, organization))

    def named_in(self, title: str) -> list[OrganizationFacts]:
        words = tuple((normalize_organization_name(title) or "").split())
        found = []
        for start, word in enumerate(words):
            for name, organization in self.by_first_word.get(word, []):
                if words[start : start + len(name)] == name:
                    found.append(organization)
        return found


def findable_name(words: tuple[str, ...]) -> bool:
    return len(words) > 1 or (len(words) == 1 and len(words[0]) >= MIN_SINGLE_WORD_NAME)


def work_domain(email: str, excluded: frozenset[str]) -> str | None:
    """The registered domain of a work email; None for a personal or generic one."""
    domain = normalize_domain(email_domain(email))
    if domain is None or domain in excluded:
        return None
    return domain


def affiliate(
    people: Iterable[PersonFacts],
    organizations: Iterable[OrganizationFacts],
    generic_domains: frozenset[str],
) -> list[AffiliationDecision]:
    """The affiliations and conflicts of every person, in a stable order."""
    index = OrganizationIndex(organizations, generic_domains | PERSONAL_EMAIL_DOMAINS)
    decisions = [
        decision
        for person in sorted(people, key=lambda p: p.record_id)
        for decision in decide(person, index)
    ]
    return decisions


def decide(person: PersonFacts, index: OrganizationIndex) -> list[AffiliationDecision]:
    relations = relation_candidates(person, index)
    if relations:
        return [as_decision(person, candidate, LINKED) for candidate in relations.values()]
    inferred = inferred_candidates(person, index)
    status = LINKED if len(inferred) == 1 else CONFLICT
    return [as_decision(person, candidate, status) for candidate in inferred.values()]


def relation_candidates(person: PersonFacts, index: OrganizationIndex) -> Candidates:
    candidates: dict[uuid.UUID, _Candidate] = defaultdict(_Candidate)
    for ref in person.organization_refs:
        for organization in index.by_ref.get((person.source_id, ref), []):
            candidates[organization.key].add(
                organization.record_id,
                evidence(RELATION, ref, "organization_id", ref),
            )
    return dict(candidates)


def inferred_candidates(person: PersonFacts, index: OrganizationIndex) -> Candidates:
    candidates: dict[uuid.UUID, _Candidate] = defaultdict(_Candidate)
    for email in person.emails:
        domain = work_domain(email, index.excluded)
        for organization in index.by_domain.get(domain, []) if domain else []:
            candidates[organization.key].add(
                organization.record_id, evidence(EMAIL_DOMAIN, domain, "email", email)
            )
    for title in person.job_titles:
        for organization in index.named_in(title):
            candidates[organization.key].add(
                organization.record_id,
                evidence(JOB_TITLE, organization.name_normalized or "", "job_title", title),
            )
    return dict(candidates)


def evidence(signal: str, value: str, field_name: str, source: str) -> AffiliationEvidence:
    return {"signal": signal, "value": value, "field": field_name, "source": source}


def as_decision(person: PersonFacts, candidate: _Candidate, status: str) -> AffiliationDecision:
    method = candidate.method
    return AffiliationDecision(
        person_record_id=person.record_id,
        # Any record of the organization will do; the lowest id keeps reruns stable.
        organization_record_id=min(candidate.records),
        status=status,
        method=method,
        confidence=CONFIDENCE[method] if status == LINKED else 0.0,
        evidence=tuple(candidate.evidence),
    )
