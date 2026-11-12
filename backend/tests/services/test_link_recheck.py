# AI contribution: 50% or more AI-generated
"""Orchestration tests for dropping automatic links; no database."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.models import OrgLink, PersonLink, SourceRecord, SyncRun
from app.processing.link_recheck import (
    REASON_HOLDS,
    REASON_UNVERIFIABLE,
    REASON_VALUE_CHANGED,
    LinkVerdict,
)
from app.services import link_recheck as service
from app.services.link_recheck import RecheckResult, recheck_links

TENANT = UUID(int=110)
RUN_ID = UUID(int=111)
OLD_ID = UUID(int=1)
NEW_ID = UUID(int=2)
ORG_ID = UUID(int=50)
PERSON_ID = UUID(int=60)
EXCLUDED = frozenset({"gmail.com"})
START = datetime(2026, 11, 1, tzinfo=UTC)


def run() -> SyncRun:
    return SyncRun(
        id=RUN_ID,
        tenant_id=TENANT,
        source_id=UUID(int=10),
        started_at=START,
    )


def version(record_id: UUID, **fields) -> SourceRecord:
    return SourceRecord(
        **(
            {
                "id": record_id,
                "tenant_id": TENANT,
                "kind": "organization",
                "external_id": str(record_id.int),
                "external_ids": {},
                "attributes": {},
                "domain": "acme.io",
            }
            | fields
        )
    )


def automatic_link(**fields) -> OrgLink:
    return OrgLink(
        **(
            {
                "source_record_id": NEW_ID,
                "organization_id": ORG_ID,
                "tenant_id": TENANT,
                "method": "domain",
                "confidence": 1.0,
                "decided_by": "tier1",
                "evidence": [
                    {
                        "rule": "domain",
                        "rule_set_version": "tier1-v1",
                        "field": "domain",
                        "value": "acme.io",
                        "matched_value": "acme.io",
                        "matched_record_id": str(UUID(int=3)),
                    }
                ],
            }
            | fields
        )
    )


@pytest.fixture
def session(mocker):
    return mocker.MagicMock(info={})


def test_empty_carried_does_nothing(session, mocker):
    # Arrange
    load = mocker.patch.object(service, "_load_versions")

    # Act
    result = recheck_links(session, run(), [])

    # Assert
    assert result == RecheckResult()
    load.assert_not_called()
    session.delete.assert_not_called()


def test_no_matching_field_change_skips_links(session, mocker):
    # Arrange
    before = version(OLD_ID)
    after = version(NEW_ID)
    mocker.patch.object(service, "_load_versions", return_value={OLD_ID: before, NEW_ID: after})
    links = mocker.patch.object(service, "_automatic_links")

    # Act
    result = recheck_links(session, run(), [(OLD_ID, NEW_ID)])

    # Assert
    assert result == RecheckResult()
    links.assert_not_called()
    session.delete.assert_not_called()


def test_person_matching_field_change_rechecks_person_links(session, mocker):
    # Arrange
    before = version(
        OLD_ID,
        kind="person",
        domain=None,
        name_normalized="bob",
        linkedin="in/bob",
        attributes={"identifiers": {"linkedin": ["in/bob"]}},
    )
    after = version(
        NEW_ID,
        kind="person",
        domain=None,
        name_normalized="alice",
        linkedin="in/bob",
        attributes={"identifiers": {"linkedin": ["in/bob"]}},
    )
    link = PersonLink(
        source_record_id=NEW_ID,
        person_id=PERSON_ID,
        tenant_id=TENANT,
        method="linkedin",
        confidence=1.0,
        decided_by="tier1",
        evidence=[
            {
                "rule": "linkedin",
                "rule_set_version": "tier1-v1",
                "field": "linkedin",
                "value": "in/bob",
                "matched_value": "in/bob",
                "matched_record_id": str(UUID(int=3)),
            }
        ],
    )
    mocker.patch.object(service, "_load_versions", return_value={OLD_ID: before, NEW_ID: after})
    mocker.patch.object(service, "_automatic_links", return_value=[link])
    mocker.patch.object(service, "_current_members", return_value={PERSON_ID: (after,)})
    mocker.patch.object(
        service,
        "evidence_holds",
        return_value=LinkVerdict(holds=False, reason=REASON_VALUE_CHANGED),
    )

    # Act
    result = recheck_links(session, run(), [(OLD_ID, NEW_ID)], excluded_domains=EXCLUDED)

    # Assert
    assert result.dropped == 1
    session.delete.assert_called_once_with(link)


def test_drops_failing_automatic_link(session, mocker):
    # Arrange
    before = version(OLD_ID, domain="acme.io")
    after = version(NEW_ID, domain="new.io")
    link = automatic_link()
    mocker.patch.object(service, "_load_versions", return_value={OLD_ID: before, NEW_ID: after})
    mocker.patch.object(service, "_automatic_links", return_value=[link])
    mocker.patch.object(service, "_current_members", return_value={ORG_ID: (after,)})
    mocker.patch.object(
        service,
        "evidence_holds",
        return_value=LinkVerdict(holds=False, reason=REASON_VALUE_CHANGED),
    )

    # Act
    result = recheck_links(session, run(), [(OLD_ID, NEW_ID)], excluded_domains=EXCLUDED)

    # Assert
    assert result == RecheckResult(
        checked=1, kept=0, dropped=1, unverifiable=0, dropped_ids=(NEW_ID,)
    )
    session.delete.assert_called_once_with(link)
    session.flush.assert_called_once()


def test_keeps_holding_link_without_delete(session, mocker):
    # Arrange
    before = version(OLD_ID, name_normalized="old")
    after = version(NEW_ID, name_normalized="new")
    link = automatic_link()
    mocker.patch.object(service, "_load_versions", return_value={OLD_ID: before, NEW_ID: after})
    mocker.patch.object(service, "_automatic_links", return_value=[link])
    mocker.patch.object(service, "_current_members", return_value={ORG_ID: (after,)})
    mocker.patch.object(
        service,
        "evidence_holds",
        return_value=LinkVerdict(holds=True, reason=REASON_HOLDS),
    )

    # Act
    result = recheck_links(session, run(), [(OLD_ID, NEW_ID)], excluded_domains=EXCLUDED)

    # Assert
    assert result == RecheckResult(checked=1, kept=1, dropped=0, unverifiable=0)
    session.delete.assert_not_called()
    session.flush.assert_not_called()


def test_unverifiable_evidence_is_kept_and_counted(session, mocker):
    # Arrange
    before = version(OLD_ID, domain="acme.io")
    after = version(NEW_ID, domain="other.io")
    link = automatic_link(evidence=[])
    mocker.patch.object(service, "_load_versions", return_value={OLD_ID: before, NEW_ID: after})
    mocker.patch.object(service, "_automatic_links", return_value=[link])
    mocker.patch.object(service, "_current_members", return_value={ORG_ID: (after,)})
    mocker.patch.object(
        service,
        "evidence_holds",
        return_value=LinkVerdict(holds=True, reason=REASON_UNVERIFIABLE),
    )

    # Act
    result = recheck_links(session, run(), [(OLD_ID, NEW_ID)], excluded_domains=EXCLUDED)

    # Assert
    assert result == RecheckResult(checked=1, kept=1, dropped=0, unverifiable=1)
    session.delete.assert_not_called()


def test_drop_uses_real_evidence_holds_when_domain_changes(session, mocker):
    # Arrange
    before = version(OLD_ID, domain="acme.io")
    after = version(NEW_ID, domain="new.io")
    partner = version(UUID(int=3), domain="acme.io")
    link = automatic_link()
    mocker.patch.object(service, "_load_versions", return_value={OLD_ID: before, NEW_ID: after})
    mocker.patch.object(service, "_automatic_links", return_value=[link])
    mocker.patch.object(service, "_current_members", return_value={ORG_ID: (after, partner)})

    # Act
    result = recheck_links(session, run(), [(OLD_ID, NEW_ID)], excluded_domains=EXCLUDED)

    # Assert
    assert result.dropped == 1
    session.delete.assert_called_once_with(link)
