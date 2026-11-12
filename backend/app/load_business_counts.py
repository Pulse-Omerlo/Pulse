# AI contribution: 50% or more AI-generated
"""Refresh the employer business counts of Quebec's metropolitan areas from Statistics Canada.

    uv run python -m app.load_business_counts            # writes the extract next to the module
    uv run python -m app.load_business_counts --out x.json

Finds the newest release of the table by its title, asks only for the sectors of Quebec's
census metropolitan areas, and writes them with the release date and attribution. Run it
after Statistics Canada publishes a new release (February and August); commit the result.
"""

import argparse
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx

from app.processing.industry import sectors
from app.processing.regional_baseline import (
    BASELINE_PATH,
    build_extract,
    newest_table,
    plan_request,
)

WDS = "https://www150.statcan.gc.ca/t1/wds/rest"
# The data service answers at most this many coordinates per call.
BATCH = 100


def post(client: httpx.Client, method: str, body: Any) -> Any:
    response = client.post(f"{WDS}/{method}", json=body)
    response.raise_for_status()
    return response.json()


def data_points(client: httpx.Client, product_id: int, coordinates: list[str]) -> Iterator[dict]:
    for start in range(0, len(coordinates), BATCH):
        body = [
            {"productId": product_id, "coordinate": coordinate, "latestN": 1}
            for coordinate in coordinates[start : start + BATCH]
        ]
        for answer in post(client, "getDataFromCubePidCoordAndLatestNPeriods", body):
            if answer.get("status") != "SUCCESS":
                raise RuntimeError(f"Statistics Canada refused a data point: {answer}")
            yield answer["object"]


def fetch_extract(client: httpx.Client) -> dict[str, Any]:
    cubes = client.get(f"{WDS}/getAllCubesListLite")
    cubes.raise_for_status()
    table = newest_table(cubes.json())
    (answer,) = post(client, "getCubeMetadata", [{"productId": table["productId"]}])
    metadata = answer["object"]
    request = plan_request(metadata, sectors())
    points = data_points(client, request.product_id, list(request.coordinates))
    return build_extract(metadata, request, points)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=BASELINE_PATH)
    args = parser.parse_args(argv)
    with httpx.Client(timeout=60) as client:
        extract = fetch_extract(client)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(extract, ensure_ascii=False, indent=2) + "\n", "utf-8")
    regions = ", ".join(region["name"] for region in extract["regions"].values())
    print(f"{extract['table']} released {extract['release']}: {regions} -> {args.out}")


if __name__ == "__main__":
    main()
