# AI contribution: 50% or more AI-generated
"""The migration that allows fuzzy organization links, up and down."""

import pytest
from alembic import command
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from app.core.tenancy import SYSTEM_KEY, TenantSession
from app.models import Organization, OrgLink
from tests.integration.test_entity_resolution import setup_records

pytestmark = pytest.mark.integration


def fuzzy_link(session, tenant_id, record_id):
    organization = Organization(tenant_id=tenant_id, display_name="Boulangerie Tremblay")
    session.add(organization)
    session.flush()
    session.add(
        OrgLink(
            tenant_id=tenant_id,
            source_record_id=record_id,
            organization_id=organization.id,
            method="fuzzy",
            confidence=0.8,
            decided_by="tier2:tier2-v1",
            evidence=[],
        )
    )
    session.flush()


def system_session(database):
    return TenantSession(bind=database, info={SYSTEM_KEY: True})


def delete_fuzzy_links(database):
    with system_session(database) as session:
        session.execute(delete(OrgLink).where(OrgLink.method == "fuzzy"))
        session.commit()


def test_downgrade_refuses_to_drop_fuzzy_links(migrated_database, alembic_config):
    # Arrange
    with system_session(migrated_database) as session:
        _, _, _, records = setup_records(session, [{}])
        ids = (records[0].tenant_id, records[0].id)
        fuzzy_link(session, *ids)
        session.commit()
    # Act
    try:
        with pytest.raises(RuntimeError) as error:
            command.downgrade(alembic_config, "0009")
    finally:
        # The fixture downgrades to base afterwards, which needs the link gone.
        delete_fuzzy_links(migrated_database)
    # Assert
    assert "1 organization links use the fuzzy method" in str(error.value)


def test_downgrade_without_fuzzy_links_restores_the_old_constraint(
    migrated_database, alembic_config
):
    # Arrange
    with system_session(migrated_database) as session:
        _, _, _, records = setup_records(session, [{}])
        ids = (records[0].tenant_id, records[0].id)
        fuzzy_link(session, *ids)
        session.commit()
    delete_fuzzy_links(migrated_database)
    command.downgrade(alembic_config, "0009")
    # Act
    with system_session(migrated_database) as session, pytest.raises(IntegrityError) as error:
        fuzzy_link(session, *ids)
    # Assert
    assert "ck_org_link_method" in str(error.value)
    command.upgrade(alembic_config, "head")
