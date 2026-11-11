# AI contribution: 50% or more AI-generated
"""The migration that allows fuzzy organization links, up and down."""

import pytest
from alembic import command
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.tenancy import SYSTEM_KEY, TenantSession
from app.models import Organization, OrgLink
from tests.integration.test_entity_resolution import setup_records

pytestmark = pytest.mark.integration


def organization(session, tenant_id, name, merged_into=None):
    item = Organization(tenant_id=tenant_id, display_name=name, merged_into=merged_into)
    session.add(item)
    session.flush()
    return item.id


def link(session, tenant_id, record_id, organization_id, method):
    session.add(
        OrgLink(
            tenant_id=tenant_id,
            source_record_id=record_id,
            organization_id=organization_id,
            method=method,
            confidence=0.8,
            decided_by="test",
            evidence=[],
        )
    )
    session.flush()


def system_session(database):
    return TenantSession(bind=database, info={SYSTEM_KEY: True})


def linked_organizations(database):
    """Three organizations: fuzzy links only, a fuzzy and a domain link, and fuzzy links
    only but with another organization merged into it."""
    with system_session(database) as session:
        tenant, _, _, records = setup_records(session, [{}, {}, {}, {}])
        ids = [record.id for record in records]
        only_fuzzy = organization(session, tenant.id, "Only fuzzy")
        mixed = organization(session, tenant.id, "Mixed")
        survivor = organization(session, tenant.id, "Survivor")
        organization(session, tenant.id, "Merged", merged_into=survivor)
        link(session, tenant.id, ids[0], only_fuzzy, "fuzzy")
        link(session, tenant.id, ids[1], mixed, "domain")
        link(session, tenant.id, ids[2], mixed, "fuzzy")
        link(session, tenant.id, ids[3], survivor, "fuzzy")
        session.commit()
        return tenant.id, ids, (only_fuzzy, mixed, survivor)


def test_downgrade_removes_fuzzy_links_and_the_organizations_they_leave_empty(
    migrated_database, alembic_config
):
    # Arrange
    _, ids, (only_fuzzy, mixed, survivor) = linked_organizations(migrated_database)
    # Act
    command.downgrade(alembic_config, "0009")
    # Assert
    with system_session(migrated_database) as session:
        links = session.execute(select(OrgLink.source_record_id, OrgLink.method)).all()
        organizations = set(session.scalars(select(Organization.id)))
    assert links == [(ids[1], "domain")]
    assert only_fuzzy not in organizations
    assert {mixed, survivor} <= organizations


def test_downgrade_restores_the_constraint_without_fuzzy(migrated_database, alembic_config):
    # Arrange
    tenant_id, ids, (_, mixed, _) = linked_organizations(migrated_database)
    command.downgrade(alembic_config, "0009")
    # Act
    with system_session(migrated_database) as session, pytest.raises(IntegrityError) as error:
        link(session, tenant_id, ids[0], mixed, "fuzzy")
    # Assert
    assert "ck_org_link_method" in str(error.value)
