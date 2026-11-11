# AI contribution: 50% or more AI-generated
"""Load synthetic records from fixture files or the Omerlo test tenant, and summarize them."""

import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.processing.industry import CategoryMapping, classify, sectors
from synthetic.pipeline.plan import EXTERNAL_ID_KEY, Plan
from synthetic.pipeline.sinks.omerlo import TenantGuardError, check_host
from synthetic.pipeline.target import Target
from synthetic.pipeline.writers.base import ORGANIZATIONS, PEOPLE, OmerloWriter

logger = logging.getLogger(__name__)

UNCLASSIFIED = "unclassified"


@dataclass(frozen=True)
class Org:
    external_id: str
    name: str
    city: str | None = None
    crm_id: str | None = None


@dataclass(frozen=True)
class Person:
    external_id: str
    name: str
    job_title: str | None = None
    # External id of the organization the person works for, as the source says.
    employer: str | None = None


@dataclass
class Dataset:
    source: str
    organizations: list[Org]
    people: list[Person]
    # What the run meant to create; empty when no plan or ground truth is known.
    truth: dict[str, Any] = field(default_factory=dict)
    # Records in the source without an external id from this mix.
    untouched: int = 0


def classify_name(name: str) -> str:
    result = classify(name=name, description=None, category_ids=[], mapping=CategoryMapping())
    return result.primary or UNCLASSIFIED


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_fixtures(folder: Path) -> Dataset:
    """A tenant folder written by the pipeline's files sink."""
    metadata = read_json(folder / "metadata.json")

    def external_id(record_id: str) -> str | None:
        entries = metadata.get(record_id, [])
        return next((e["value"] for e in entries if e["key"] == EXTERNAL_ID_KEY), None)

    def crm_id(record_id: str) -> str | None:
        entries = metadata.get(record_id, [])
        return next((e["value"] for e in entries if e["key"] == "crm_id"), None)

    truth_file = folder / "ground_truth.dev_only.json"
    truth = read_json(truth_file) if truth_file.exists() else {}
    employers = truth.get("employers", {})
    orgs, people, untouched = [], [], 0
    for record in read_json(folder / "organizations.json"):
        ext = external_id(record["id"])
        if ext is None:
            untouched += 1
            continue
        city = record["address"][0]["city"] if record["address"] else None
        orgs.append(Org(ext, record["name"], city, crm_id(record["id"])))
    for record in read_json(folder / "people.json"):
        ext = external_id(record["id"])
        if ext is None:
            untouched += 1
            continue
        title = record["locales"][0]["job_title"] if record["locales"] else None
        name = f"{record['first_name']} {record['last_name']}"
        people.append(Person(ext, name, title, employers.get(ext)))
    return Dataset(f"files: {folder.name}", orgs, people, truth, untouched)


def load_omerlo(writer: OmerloWriter, target: Target, plan: Plan | None) -> Dataset:
    """Records of the test workspace carrying an external id; only reads."""
    check_host(target.base_url)
    if target.workspace_id not in {w["id"] for w in writer.list_workspaces()}:
        raise TenantGuardError(f"workspace {target.workspace_id} is not visible to this key")
    prefix = f"syn-{plan.mix.name}-{plan.mix.seed}-" if plan else "syn-"

    def ours(kind: str) -> tuple[list[tuple[str, dict[str, Any]]], int]:
        found, others = [], 0
        records = writer.list_records(kind, target.workspace_id)
        logger.info("Reading metadata of %d %s", len(records), kind)
        for record in records:
            entries = writer.get_metadata(kind, record["id"])
            ext = next((e["value"] for e in entries if e["key"] == EXTERNAL_ID_KEY), None)
            if ext and ext.startswith(prefix):
                found.append((ext, record))
            else:
                others += 1
        return found, others

    org_records, other_orgs = ours(ORGANIZATIONS)
    person_records, other_people = ours(PEOPLE)
    by_omerlo_id = {record["id"]: ext for ext, record in org_records}
    orgs = [Org(ext, record["name"]) for ext, record in org_records]
    people = []
    logger.info("Reading the employer of %d people", len(person_records))
    for ext, record in person_records:
        employer_id = writer.get_employer(record["id"], target.employer_block_type)
        employer = by_omerlo_id.get(employer_id) if employer_id else None
        name = " ".join(filter(None, (record.get("first_name"), record.get("last_name"))))
        people.append(Person(ext, name, employer=employer))
    truth = plan.ground_truth if plan else {}
    return Dataset("omerlo: " + target.base_url, orgs, people, truth, other_orgs + other_people)


def duplicate_copies(truth: dict[str, Any], kind: str) -> set[str]:
    """External ids of the second record of each planted pair."""
    return {pair["external_ids"][1] for pair in truth.get(f"{kind}_duplicates", [])}


class View:
    """What the viewer API answers, computed once from a dataset."""

    def __init__(self, data: Dataset):
        self.data = data
        self.names = sectors()
        self.sector_of = {o.external_id: classify_name(o.name) for o in data.organizations}
        self.org_names = {o.external_id: o.name for o in data.organizations}
        self.copies = duplicate_copies(data.truth, "organization") | duplicate_copies(
            data.truth, "person"
        )

    def person_sector(self, person: Person) -> str | None:
        return self.sector_of.get(person.employer) if person.employer else None

    def summary(self) -> dict[str, Any]:
        truth = self.data.truth
        return {
            "source": self.data.source,
            "mix": truth.get("mix"),
            "seed": truth.get("seed"),
            "organizations": len(self.data.organizations),
            "people": len(self.data.people),
            "linked_people": sum(1 for p in self.data.people if p.employer),
            "duplicate_pairs": {
                "organizations": len(truth.get("organization_duplicates", [])),
                "people": len(truth.get("person_duplicates", [])),
            },
            "untouched": self.data.untouched,
        }

    def sectors(self) -> list[dict[str, Any]]:
        """Each sector's share of organizations and of linked people, counting each entity once."""
        orgs = Counter(
            self.sector_of[o.external_id]
            for o in self.data.organizations
            if o.external_id not in self.copies
        )
        people = Counter(
            self.person_sector(p)
            for p in self.data.people
            if p.external_id not in self.copies and p.employer
        )
        total_orgs, total_people = sum(orgs.values()), sum(people.values())
        gaps = self.data.truth.get("gaps", {})
        rows = []
        for code in sorted(set(orgs) | set(people) | set(gaps), key=lambda c: -orgs[c]):
            sector = self.names.get(code)
            rows.append(
                {
                    "code": code,
                    "name": sector.en if sector else "Unclassified",
                    "organizations": orgs[code],
                    "people": people[code],
                    "organization_share": orgs[code] / total_orgs if total_orgs else 0,
                    "people_share": people[code] / total_people if total_people else 0,
                    "planted_gap": gaps.get(code),
                }
            )
        return rows

    def organizations(self, sector: str | None = None) -> list[dict[str, Any]]:
        employees = Counter(p.employer for p in self.data.people if p.employer)
        twins = self.twins("organization")
        return [
            {
                "external_id": o.external_id,
                "name": o.name,
                "sector": self.sector_of[o.external_id],
                "city": o.city,
                "crm_id": o.crm_id,
                "employees": employees[o.external_id],
                "duplicate_of": twins.get(o.external_id),
            }
            for o in sorted(self.data.organizations, key=lambda o: o.name.casefold())
            if sector is None or self.sector_of[o.external_id] == sector
        ]

    def people(
        self, sector: str | None = None, employer: str | None = None
    ) -> list[dict[str, Any]]:
        twins = self.twins("person")
        return [
            {
                "external_id": p.external_id,
                "name": p.name,
                "job_title": p.job_title,
                "employer": p.employer,
                "employer_name": self.org_names.get(p.employer) if p.employer else None,
                "sector": self.person_sector(p),
                "duplicate_of": twins.get(p.external_id),
            }
            for p in sorted(self.data.people, key=lambda p: p.name.casefold())
            if (sector is None or self.person_sector(p) == sector)
            and (employer is None or p.employer == employer)
        ]

    def twins(self, kind: str) -> dict[str, str]:
        return {
            pair["external_ids"][1]: pair["external_ids"][0]
            for pair in self.data.truth.get(f"{kind}_duplicates", [])
        }

    def duplicates(self) -> list[dict[str, Any]]:
        people = {p.external_id: p.name for p in self.data.people}
        rows = []
        for kind, names in (("organization", self.org_names), ("person", people)):
            for pair in self.data.truth.get(f"{kind}_duplicates", []):
                first, second = pair["external_ids"]
                rows.append(
                    {
                        "kind": kind,
                        "first": names.get(first, first),
                        "second": names.get(second, second),
                        "changes": pair["changes"],
                    }
                )
        return rows

    def check(self) -> dict[str, Any]:
        """The plan against what the source holds: missing records and wrong employers."""
        truth = self.data.truth
        if not truth:
            return {"available": False}
        planned_orgs = set(truth.get("organizations", {}))
        planned_people = set(truth.get("people", {}))
        have_orgs = {o.external_id for o in self.data.organizations}
        have_people = {p.external_id: p for p in self.data.people}
        wrong = [
            {
                "external_id": ext,
                "name": person.name,
                "expected": truth["employers"].get(ext),
                "found": person.employer,
            }
            for ext, person in sorted(have_people.items())
            if person.employer != truth.get("employers", {}).get(ext)
        ]
        return {
            "available": True,
            "planned": {"organizations": len(planned_orgs), "people": len(planned_people)},
            "missing_organizations": sorted(planned_orgs - have_orgs),
            "missing_people": sorted(planned_people - set(have_people)),
            "unplanned": sorted((have_orgs - planned_orgs) | (set(have_people) - planned_people)),
            "wrong_employer": wrong,
            "untouched": self.data.untouched,
        }
