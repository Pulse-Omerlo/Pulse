# AI contribution: 50% or more AI-generated
"""Look at synthetic data in a browser, from fixture files or the Omerlo test tenant.

uv run python -m synthetic.viewer --fixtures synthetic/out/mixes/mix-sector-gaps
uv run python -m synthetic.viewer --omerlo --mix synthetic/mixes/sector_gaps.toml
"""

import argparse
import logging
import sys
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from app.ingestion.omerlo.client import OmerloAuthError, create_omerlo_client
from app.ingestion.omerlo.settings import OmerloSettings
from synthetic.pipeline.__main__ import ROOT_ENV
from synthetic.pipeline.mixer import load_mix
from synthetic.pipeline.plan import build_plan
from synthetic.pipeline.sinks.omerlo import TenantGuardError
from synthetic.pipeline.target import DEFAULT_TARGET, load_target
from synthetic.pipeline.writers.api import ApiWriter
from synthetic.viewer.app import create_app
from synthetic.viewer.data import Dataset, load_fixtures, load_omerlo


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--fixtures", type=Path, help="a tenant folder from --sink files")
    source.add_argument("--omerlo", action="store_true", help="read the Omerlo test tenant")
    parser.add_argument("--mix", type=Path, help="mix file of the run, for gaps and the check")
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--port", type=int, default=8200)
    return parser.parse_args(argv)


def load(args: argparse.Namespace) -> Dataset:
    if args.fixtures:
        return load_fixtures(args.fixtures)
    load_dotenv(ROOT_ENV)
    target = load_target(args.target)
    plan = build_plan(load_mix(args.mix)) if args.mix else None
    settings = OmerloSettings(base_url=target.base_url)  # type: ignore[call-arg]
    with create_omerlo_client(settings) as client:
        return load_omerlo(ApiWriter(client), target, plan)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args(argv)
    try:
        data = load(args)
    except (TenantGuardError, OmerloAuthError) as error:
        print(f"Refused: {error}", file=sys.stderr)
        return 2
    print(
        f"{len(data.organizations)} organizations and {len(data.people)} people from {data.source}"
    )
    print(f"Open http://127.0.0.1:{args.port}")
    # Local only: the page is for whoever runs it, never shared on the network.
    uvicorn.run(create_app(data), host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
