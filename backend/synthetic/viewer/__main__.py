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
from synthetic.pipeline.plan import Plan, build_plan
from synthetic.pipeline.sinks.omerlo import TenantGuardError, check_host
from synthetic.pipeline.target import DEFAULT_TARGET, Target, load_target
from synthetic.pipeline.writers.api import ApiWriter
from synthetic.viewer.app import Source, create_app
from synthetic.viewer.data import Dataset, load_fixtures, load_omerlo, read_records, save_records

logger = logging.getLogger(__name__)

# Git-ignored, like the rest of synthetic/out/.
CACHE_DIR = Path(__file__).resolve().parents[1] / "out" / "viewer"


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--fixtures", type=Path, help="a tenant folder from --sink files")
    source.add_argument("--omerlo", action="store_true", help="read the Omerlo test tenant")
    parser.add_argument("--mix", type=Path, help="mix file of the run, for gaps and the check")
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--port", type=int, default=8200)
    return parser.parse_args(argv)


def cache_path(target: Target, plan: Plan | None) -> Path:
    run = f"{plan.mix.name}-{plan.mix.seed}" if plan else "all"
    return CACHE_DIR / f"omerlo-{target.workspace_id}-{run}.json"


def cached(path: Path, plan: Plan | None) -> Dataset | None:
    if not path.exists():
        return None
    try:
        return read_records(path, plan.ground_truth if plan else {})
    except (ValueError, KeyError, TypeError) as error:
        logger.warning("Ignoring the saved copy %s: %s", path, error)
        return None


def fixtures_source(folder: Path) -> Source:
    # Files are quick to read, so each sync reads them again, for example after a new run.
    return Source(lambda: load_fixtures(folder), load_fixtures(folder))


def omerlo_source(args: argparse.Namespace) -> Source:
    """The last copy read from the test tenant, if any; a sync reads the tenant again."""
    load_dotenv(ROOT_ENV)
    target = load_target(args.target)
    check_host(target.base_url)
    plan = build_plan(load_mix(args.mix)) if args.mix else None
    settings = OmerloSettings(base_url=target.base_url)  # type: ignore[call-arg]
    path = cache_path(target, plan)

    def read() -> Dataset:
        with create_omerlo_client(settings) as client:
            data = load_omerlo(ApiWriter(client), target, plan)
        save_records(data, path)
        return data

    source = Source(read, cached(path, plan))
    if source.view is None:
        source.sync()
    return source


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args(argv)
    try:
        source = fixtures_source(args.fixtures) if args.fixtures else omerlo_source(args)
    except (TenantGuardError, OmerloAuthError) as error:
        print(f"Refused: {error}", file=sys.stderr)
        return 2
    if source.view:
        data = source.view.data
        print(
            f"{len(data.organizations)} organizations and {len(data.people)} people "
            f"from {data.source}, read {data.read_at}. Press Sync on the page to read again."
        )
    else:
        print("Reading the test tenant in the background; the page fills in when it is done.")
    print(f"Open http://127.0.0.1:{args.port}")
    # Local only: the page is for whoever runs it, never shared on the network.
    uvicorn.run(create_app(source), host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
