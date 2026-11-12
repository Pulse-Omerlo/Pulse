# AI contribution: 50% or more AI-generated
"""Build the records a mix asks for, with external ids and each person's employer."""

from dataclasses import dataclass, field
from typing import Any

from synthetic import vocabulary as vocab
from synthetic.omerlo_fixtures import Context, OmerloFixtureGenerator, OrgEntity, Sizes, Tenant
from synthetic.pipeline.mixer import Mix, MixError, org_quotas, people_quotas

EXTERNAL_ID_KEY = "pulse_external_id"
# How many draws a sector may take per name before its patterns are judged too few.
NAME_TRIES_PER_RECORD = 50


@dataclass(frozen=True)
class PlannedOrg:
    external_id: str
    sector: str
    entity_key: int
    # Detail record in the backoffice shape, as the fixture files hold it.
    record: dict[str, Any]
    # Metadata entries the generator gave the record (a CRM id for some companies).
    metadata: list[dict[str, str]]


@dataclass(frozen=True)
class PlannedPerson:
    external_id: str
    sector: str
    person_key: int
    record: dict[str, Any]
    employer: str  # external id of the organization the person works for


@dataclass
class Plan:
    mix: Mix
    tenant: Tenant
    workspaces: list[dict[str, Any]]
    categories: list[dict[str, Any]]
    organizations: list[PlannedOrg] = field(default_factory=list)
    people: list[PlannedPerson] = field(default_factory=list)
    ground_truth: dict[str, Any] = field(default_factory=dict)

    @property
    def locales(self) -> set[str]:
        records = [o.record for o in self.organizations] + [p.record for p in self.people]
        return {entry["locale"] for record in records for entry in record["locales"]}


def external_id(mix: Mix, kind: str, index: int) -> str:
    return f"syn-{mix.name}-{mix.seed}-{kind}-{index:04d}"


def mix_tenant(mix: Mix) -> Tenant:
    return Tenant(f"mix-{mix.name}", f"Synthetic mix {mix.name}", f"{mix.name}.example", ("Mix",))


class SectorAwareGenerator(OmerloFixtureGenerator):
    """The fixture generator, driven by a mix: names per sector and employers per sector."""

    def __init__(self, mix: Mix):
        super().__init__(
            mix.seed,
            Sizes(
                duplicate_org_share=mix.duplicate_org_share,
                duplicate_person_share=mix.duplicate_person_share,
            ),
        )
        self.mix = mix

    def locales(self) -> tuple[str, ...]:
        return self.mix.locales or super().locales()

    def sector_entities(self, quotas: dict[str, int]) -> list[OrgEntity]:
        entities: list[OrgEntity] = []
        names: set[str] = set()
        for code, count in quotas.items():
            made, tries = 0, 0
            while made < count:
                tries += 1
                if tries > (count + 1) * NAME_TRIES_PER_RECORD:
                    raise MixError(f"sector {code}: could not build {count} distinct names")
                surname, surname2 = self.rng.sample(vocab.LAST_NAMES, 2)
                city, region = self.rng.choice(vocab.CITIES)
                pattern = self.rng.choice(vocab.SECTOR_PATTERNS[code])
                base = pattern.format(s=surname, s2=surname2, c=city)
                if base in names:
                    continue
                names.add(base)
                entities.append(self.org_entity(len(entities), base, city, region, code))
                made += 1
        return entities

    def build(self) -> Plan:
        mix = self.mix
        tenant = mix_tenant(mix)
        ctx = Context(
            tenant=tenant,
            workspaces=[self.workspace(tenant, tenant.workspaces[0], 0)],
            categories=self.categories(),
            profile_types=self.profile_types(),
        )
        plan = Plan(mix, tenant, ctx.workspaces, ctx.categories)
        quotas = org_quotas(mix)
        entities = self.sector_entities(quotas)

        originals: dict[int, PlannedOrg] = {}
        for entity in entities:
            record, metadata = self.organization(entity, self.org_name(entity), ctx)
            org = self.planned_org(len(plan.organizations), entity, record, metadata)
            plan.organizations.append(org)
            originals[entity.key] = org
        org_duplicates = []
        for entity in self.rng.sample(entities, round(len(entities) * mix.duplicate_org_share)):
            record, metadata, changes = self.duplicate_organization(entity, ctx)
            org = self.planned_org(len(plan.organizations), entity, record, metadata)
            plan.organizations.append(org)
            org_duplicates.append(
                {
                    "entity": entity.key,
                    "external_ids": [originals[entity.key].external_id, org.external_id],
                    "changes": changes,
                }
            )

        by_sector: dict[str, list[PlannedOrg]] = {}
        for org in originals.values():
            by_sector.setdefault(org.sector, []).append(org)
        person_quotas = people_quotas(mix, quotas)
        for code, count in person_quotas.items():
            for _ in range(count):
                employer = self.rng.choice(by_sector[code])
                record = self.person(ctx, employer.record, journalist=False)
                plan.people.append(self.planned_person(plan, code, record, employer))
        person_duplicates = []
        originals_count = len(plan.people)
        picks = round(originals_count * mix.duplicate_person_share)
        for index in self.rng.sample(range(originals_count), picks):
            original = plan.people[index]
            record, changes = self.duplicate_person(original.record)
            person = PlannedPerson(
                external_id(mix, "person", len(plan.people)),
                original.sector,
                original.person_key,
                record,
                original.employer,
            )
            plan.people.append(person)
            person_duplicates.append(
                {
                    "person": original.person_key,
                    "external_ids": [original.external_id, person.external_id],
                    "changes": changes,
                }
            )

        plan.ground_truth = {
            "dev_only": True,
            "mix": mix.name,
            "seed": mix.seed,
            "quotas": {"organizations": quotas, "people": person_quotas},
            "gaps": dict(mix.gaps),
            "organizations": {o.external_id: o.entity_key for o in plan.organizations},
            "organization_duplicates": org_duplicates,
            "sectors": {o.external_id: o.sector for o in plan.organizations},
            "people": {p.external_id: p.person_key for p in plan.people},
            "person_duplicates": person_duplicates,
            "employers": {p.external_id: p.employer for p in plan.people},
        }
        return plan

    def planned_org(
        self, index: int, entity: OrgEntity, record: dict[str, Any], metadata: list
    ) -> PlannedOrg:
        assert entity.sector is not None
        return PlannedOrg(
            external_id(self.mix, "org", index), entity.sector, entity.key, record, metadata
        )

    def planned_person(
        self, plan: Plan, code: str, record: dict[str, Any], employer: PlannedOrg
    ) -> PlannedPerson:
        index = len(plan.people)
        return PlannedPerson(
            external_id(self.mix, "person", index), code, index, record, employer.external_id
        )


def build_plan(mix: Mix) -> Plan:
    return SectorAwareGenerator(mix).build()
