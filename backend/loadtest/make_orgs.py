# AI contribution: 50% or more AI-generated
"""Write a CSV of synthetic organizations to load a tenant before a load test.

uv run python -m loadtest.make_orgs --count 5000 --out loadtest/out/organizations.csv
uv run python -m app.import_file --tenant demo-capitale --kind organization \
    loadtest/out/organizations.csv
"""

import argparse
import csv
import random
from collections.abc import Sequence
from pathlib import Path

COLUMNS = ["id", "name", "website", "linkedin", "email", "phone", "city", "address"]

FIRST_WORDS = ["Atelier", "Boulangerie", "Clinique", "Garage", "Studio", "Librairie", "Ferme"]
SECOND_WORDS = ["du Nord", "Laurentien", "des Rives", "Boréal", "du Vieux-Port", "Saint-Laurent"]
CITIES = ["Québec", "Lévis", "Sherbrooke", "Magog", "Granby", "Trois-Rivières", "Rimouski"]

# One organization in this many also gets a second row with the same website,
# so matching has some duplicates to group.
DUPLICATE_EVERY = 10


def organization_row(number: int, rng: random.Random) -> dict[str, str]:
    """One organization; the number keeps the name, website and id unique."""
    name = f"{rng.choice(FIRST_WORDS)} {rng.choice(SECOND_WORDS)} {number}"
    # Each organization needs its own registered domain: "org1.example.ca" and
    # "org2.example.ca" would both be read as the domain "example.ca".
    domain = f"loadorg{number}.ca"
    return {
        "id": f"load-{number}",
        "name": name,
        "website": f"https://www.{domain}",
        "linkedin": f"https://www.linkedin.com/company/org{number}",
        "email": f"info@{domain}",
        "phone": f"418-555-{number % 10000:04d}",
        "city": rng.choice(CITIES),
        "address": f"{rng.randint(1, 9999)} rue Principale",
    }


def duplicate_row(row: dict[str, str]) -> dict[str, str]:
    """The same organization written another way, as a second source system would."""
    copy = dict(row)
    copy["id"] = row["id"] + "-dup"
    copy["name"] = row["name"].upper()
    copy["linkedin"] = ""
    copy["phone"] = ""
    return copy


def make_rows(count: int, seed: int = 490) -> list[dict[str, str]]:
    """`count` organizations, plus a duplicate row for one in every DUPLICATE_EVERY."""
    rng = random.Random(seed)
    rows = []
    for number in range(count):
        row = organization_row(number, rng)
        rows.append(row)
        if number % DUPLICATE_EVERY == 0:
            rows.append(duplicate_row(row))
    return rows


def write_csv(rows: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Write synthetic organizations for a load test.")
    parser.add_argument("--count", type=int, default=5000, help="number of organizations")
    parser.add_argument("--seed", type=int, default=490)
    parser.add_argument("--out", type=Path, default=Path("loadtest/out/organizations.csv"))
    args = parser.parse_args(argv)
    if args.count < 1:
        parser.error("--count must be at least 1")

    rows = make_rows(args.count, args.seed)
    write_csv(rows, args.out)
    print(f"Wrote {len(rows)} rows ({args.count} organizations) to {args.out}")


if __name__ == "__main__":
    main()
