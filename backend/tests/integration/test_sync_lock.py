# AI contribution: 50% or more AI-generated
"""The per-source sync lock against a real database."""

import uuid

import pytest

from app.services.sync_lock import source_sync_lock

pytestmark = pytest.mark.integration


def test_a_second_holder_of_the_same_source_is_refused(test_database):
    # Arrange
    source_id = uuid.uuid4()

    # Act
    with (
        source_sync_lock(test_database, source_id) as first,
        source_sync_lock(test_database, source_id) as second,
    ):
        pass

    # Assert
    assert first is True
    assert second is False


def test_the_lock_is_free_again_once_released(test_database):
    # Arrange
    source_id = uuid.uuid4()
    with source_sync_lock(test_database, source_id):
        pass

    # Act
    with source_sync_lock(test_database, source_id) as again:
        pass

    # Assert
    assert again is True


def test_different_sources_do_not_block_each_other(test_database):
    # Act
    with (
        source_sync_lock(test_database, uuid.uuid4()) as first,
        source_sync_lock(test_database, uuid.uuid4()) as second,
    ):
        pass

    # Assert
    assert first is True
    assert second is True
