# AI contribution: 50% or more AI-generated
from collections import Counter

from app.processing.industry import CategoryMapping, classify
from synthetic.omerlo_schema import MODELS
from synthetic.pipeline.mixer import org_quotas, people_quotas
from synthetic.pipeline.plan import build_plan


def test_every_organization_name_classifies_to_its_planned_sector(plan):
    for org in plan.organizations:
        # Act
        result = classify(
            name=org.record["name"],
            description=org.record["description"][0]["blocks"][0]["content_text"],
            category_ids=[],
            mapping=CategoryMapping(),
        )

        # Assert
        assert result.primary == org.sector, org.record["name"]


def test_original_records_match_the_mixer_quotas(small_mix, plan):
    # Arrange
    duplicated_orgs = {d["external_ids"][1] for d in plan.ground_truth["organization_duplicates"]}
    duplicated_people = {d["external_ids"][1] for d in plan.ground_truth["person_duplicates"]}
    orgs = org_quotas(small_mix)

    # Act
    org_counts = Counter(
        o.sector for o in plan.organizations if o.external_id not in duplicated_orgs
    )
    people_counts = Counter(p.sector for p in plan.people if p.external_id not in duplicated_people)

    # Assert
    assert dict(org_counts) == orgs
    assert dict(people_counts) == {k: v for k, v in people_quotas(small_mix, orgs).items() if v}
    assert len(duplicated_orgs) == 2
    assert len(duplicated_people) == 3


def test_each_person_works_for_an_original_organization_of_their_sector(plan):
    # Arrange
    duplicates = {d["external_ids"][1] for d in plan.ground_truth["organization_duplicates"]}
    orgs = {o.external_id: o for o in plan.organizations}

    for person in plan.people:
        # Act
        employer = orgs[person.employer]

        # Assert
        assert employer.sector == person.sector
        assert person.employer not in duplicates


def test_the_same_mix_gives_the_same_plan(small_mix, plan):
    # Act
    again = build_plan(small_mix)

    # Assert
    assert [o.record for o in again.organizations] == [o.record for o in plan.organizations]
    assert [p.record for p in again.people] == [p.record for p in plan.people]
    assert again.ground_truth == plan.ground_truth


def test_external_ids_are_unique_and_name_the_mix_and_seed(plan):
    # Arrange
    ids = [o.external_id for o in plan.organizations] + [p.external_id for p in plan.people]

    # Assert
    assert len(ids) == len(set(ids))
    assert plan.organizations[0].external_id == "syn-small-7-org-0000"
    assert plan.people[0].external_id == "syn-small-7-person-0000"


def test_records_use_only_the_mix_locales_and_match_the_backoffice_schema(plan):
    # Assert
    assert plan.locales == {"fr"}
    for org in plan.organizations:
        MODELS["organizations"].model_validate(org.record)
    for person in plan.people:
        MODELS["people"].model_validate(person.record)


def test_ground_truth_pairs_duplicates_of_one_entity(plan):
    # Arrange
    keys = plan.ground_truth["organizations"]

    for pair in plan.ground_truth["organization_duplicates"]:
        # Act
        first, second = pair["external_ids"]

        # Assert
        assert keys[first] == keys[second] == pair["entity"]
        assert pair["changes"]
    assert plan.ground_truth["gaps"] == {"54": 0.1}
    assert set(plan.ground_truth["employers"]) == {p.external_id for p in plan.people}
