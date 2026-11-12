# AI contribution: 50% or more AI-generated
"""Create a PULSE user in one tenant.

    uv run python -m app.create_user --tenant demo-capitale --email ana@example.ca

The password is asked for without echo, or read from PULSE_USER_PASSWORD when set (for
scripts). It is never taken as an argument, so it stays out of the shell history.
"""

import argparse
import getpass
import os
from collections.abc import Sequence

from app.core.database import get_session_factory
from app.core.errors import DataValidationError
from app.core.tenancy import system_access
from app.services.auth import create_user
from app.sync import SyncCommandError, find_tenant


def read_password() -> str:
    password = os.environ.get("PULSE_USER_PASSWORD")
    if password:
        return password
    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Same password again: "):
        raise DataValidationError("The two passwords differ")
    return password


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Create a PULSE user in one tenant.")
    parser.add_argument("--tenant", required=True, help="tenant id or Omerlo media slug")
    parser.add_argument("--email", required=True)
    args = parser.parse_args(argv)

    with get_session_factory()() as session:
        try:
            with system_access(session):
                tenant = find_tenant(session, args.tenant)
            user = create_user(session, tenant.id, args.email, read_password())
            session.commit()
        except (SyncCommandError, DataValidationError) as error:
            parser.exit(2, f"error: {error}\n")
        print(f"Created {user.email} in {tenant.name}")


if __name__ == "__main__":
    main()
