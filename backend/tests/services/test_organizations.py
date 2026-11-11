# AI contribution: 50% or more AI-generated
"""Resolving merged organization ids. The session is mocked."""

from uuid import UUID

import pytest

from app.models import Organization
from app.services.organizations import surviving_organization_id

TENANT = UUID(int=100)
OLD, NEW = UUID(int=1), UUID(int=2)


@pytest.fixture
def session(mocker):
    return mocker.MagicMock(info={})


def organizations(session, *items):
    by_id = {item.id: item for item in items}
    session.get.side_effect = lambda _, organization_id: by_id.get(organization_id)


def test_merged_id_resolves_to_its_survivor_inside_the_tenant(session):
    # Arrange
    organizations(
        session,
        Organization(id=OLD, tenant_id=TENANT, merged_into=NEW),
        Organization(id=NEW, tenant_id=TENANT),
    )
    # Act
    result = surviving_organization_id(session, TENANT, OLD)
    # Assert
    assert result == NEW
    assert session.info == {}


def test_unknown_id_resolves_to_none(session):
    # Arrange
    organizations(session)
    # Act / Assert
    assert surviving_organization_id(session, TENANT, OLD) is None


def test_merge_loop_is_reported(session):
    # Arrange
    organizations(
        session,
        Organization(id=OLD, tenant_id=TENANT, merged_into=NEW),
        Organization(id=NEW, tenant_id=TENANT, merged_into=OLD),
    )
    # Act
    with pytest.raises(ValueError) as error:
        surviving_organization_id(session, TENANT, OLD)
    # Assert
    assert "loop" in str(error.value)
