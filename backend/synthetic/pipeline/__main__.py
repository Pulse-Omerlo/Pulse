# AI contribution: 50% or more AI-generated
"""Generate the data a mix file describes and write it to files or to Omerlo staging.

uv run python -m synthetic.pipeline --mix synthetic/mixes/sector_gaps.toml
uv run python -m synthetic.pipeline --mix ... --sink omerlo --dry-run
uv run python -m synthetic.pipeline --mix ... --sink omerlo --writer cli --cli-command "my-cli"
"""

import argparse
import os
import shlex
import sys
from pathlib import Path

from dotenv import load_dotenv

from app.core.logging import configure_logging, get_logger
from app.ingestion.omerlo.client import OmerloAuthError, create_omerlo_client
from app.ingestion.omerlo.settings import OmerloSettings
from synthetic.pipeline.mixer import MixError, load_mix
from synthetic.pipeline.plan import build_plan
from synthetic.pipeline.report import RunReport
from synthetic.pipeline.sinks.files import write_files
from synthetic.pipeline.sinks.omerlo import OmerloSink, RunStopped, TenantGuardError
from synthetic.pipeline.target import DEFAULT_TARGET, Target, load_target
from synthetic.pipeline.writers.api import ApiWriter
from synthetic.pipeline.writers.base import OmerloWriter
from synthetic.pipeline.writers.cli import CliWriter

ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"
# A folder of its own, so a run never rewrites the demo fixtures' manifest in synthetic/out.
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "out" / "mixes"

EXIT_REJECTIONS = 1
EXIT_REFUSED = 2
EXIT_AUTH = 3
EXIT_STOPPED = 4

logger = get_logger(__name__)


def make_writer(kind: str, target: Target, cli_command: str | None) -> OmerloWriter:
    if kind == "cli":
        command = cli_command or os.environ.get("OMERLO_CLI_COMMAND")
        if not command:
            raise SystemExit("--writer cli needs --cli-command or OMERLO_CLI_COMMAND")
        return CliWriter(shlex.split(command))
    # The target decides the host, whatever OMERLO_BASE_URL says; only the key comes from env.
    settings = OmerloSettings(base_url=target.base_url)  # type: ignore[call-arg]
    return ApiWriter(create_omerlo_client(settings))


def log_report(event: str, report: RunReport, reason: str | None = None) -> None:
    """Each rejection as a warning, then the counts; a stopped run logs at error with why."""
    for rejection in report.rejections:
        logger.warning(
            "record_rejected",
            kind=rejection.kind,
            external_id=rejection.external_id,
            reason=rejection.reason,
        )
    if reason is None:
        logger.info(event, **report.summary())
    else:
        logger.error(event, reason=reason, **report.summary())


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mix", type=Path, required=True, help="mix file (synthetic/mixes/)")
    parser.add_argument("--sink", choices=("files", "omerlo"), default="files")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="folder for --sink files")
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET, help="Omerlo target file")
    parser.add_argument("--writer", choices=("api", "cli"), default="api")
    parser.add_argument("--cli-command", help="command for --writer cli")
    parser.add_argument("--dry-run", action="store_true", help="check and plan, write nothing")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, writer: OmerloWriter | None = None) -> int:
    args = parse_args(argv)
    try:
        mix = load_mix(args.mix)
    except MixError as error:
        logger.error("mix_refused", mix=str(args.mix), reason=str(error))
        return EXIT_REFUSED
    plan = build_plan(mix)

    if args.sink == "files":
        folder = write_files(plan, args.out)
        logger.info(
            "fixtures_written",
            mix=mix.name,
            seed=mix.seed,
            folder=str(folder),
            organizations=len(plan.organizations),
            people=len(plan.people),
        )
        return 0

    load_dotenv(ROOT_ENV)
    target = load_target(args.target)
    sink = OmerloSink(writer or make_writer(args.writer, target, args.cli_command), target)
    try:
        report = sink.run(plan, dry_run=args.dry_run)
    except TenantGuardError as error:
        logger.error("omerlo_run_refused", reason=str(error))
        return EXIT_REFUSED
    except OmerloAuthError as error:
        log_report("omerlo_run_stopped", sink.report, str(error))
        return EXIT_AUTH
    except RunStopped as error:
        log_report("omerlo_run_stopped", sink.report, str(error))
        return EXIT_STOPPED
    log_report("omerlo_run_completed", report)
    return EXIT_REJECTIONS if report.rejections else 0


if __name__ == "__main__":
    configure_logging()
    sys.exit(main())
