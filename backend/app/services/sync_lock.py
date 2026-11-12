# AI contribution: 50% or more AI-generated
"""A database lock that keeps two syncs of one source from running at the same time."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, text

# A transaction-level lock, because a session-level one is lost behind PgBouncer's
# transaction pooling. It is released when the transaction ends or the connection drops.
TRY_LOCK = text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))")


@contextmanager
def source_sync_lock(engine: Engine, source_id: uuid.UUID) -> Iterator[bool]:
    """Yield whether this process holds the source's sync lock, without waiting for it."""
    with engine.connect() as connection, connection.begin():
        yield bool(connection.scalar(TRY_LOCK, {"key": f"sync:{source_id}"}))
