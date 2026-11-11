# AI contribution: 50% or more AI-generated
import re
from dataclasses import replace

import pytest

from app.ingestion.omerlo.client import OmerloAuthError
from synthetic.pipeline.plan import EXTERNAL_ID_KEY
from synthetic.pipeline.sinks.omerlo import OmerloSink, TenantGuardError
from synthetic.pipeline.writers.base import ORGANIZATIONS, PEOPLE, WriteRejected
from tests.synthetic.pipeline.conftest import TENANT_ID, WORKSPACE_ID


def run(writer, target, plan, **options):
    return OmerloSink(writer, target, sleep=lambda _: None).run(plan, **options)


def writes(writer):
    return [call for call in writer.calls if call[0].startswith(("create", "set_"))]


def person_name(person):
    return f"{person.record['first_name']} {person.record['last_name']}"


def test_organizations_are_created_before_people_and_links_last(writer, target, plan):
    # Act
    run(writer, target, plan)

    # Assert
    order = [op if op != "create" else name.split(":")[0] for op, name in writes(writer)]
    last_org = max(i for i, step in enumerate(order) if step == ORGANIZATIONS)
    first_person = order.index(PEOPLE)
    last_person = max(i for i, step in enumerate(order) if step == PEOPLE)
    assert last_org < first_person
    assert last_person < order.index("set_employer")


def test_a_first_run_creates_every_record_and_tags_it_with_its_external_id(writer, target, plan):
    # Act
    report = run(writer, target, plan)

    # Assert
    assert report.counts[ORGANIZATIONS].created == len(plan.organizations)
    assert report.counts[PEOPLE].created == len(plan.people)
    assert report.counts["links"].created == len(plan.people)
    assert not report.rejections
    tagged = {
        entry["value"]
        for entries in writer.metadata.values()
        for entry in entries
        if entry["key"] == EXTERNAL_ID_KEY
    }
    assert len(tagged) == len(plan.organizations) + len(plan.people)


def test_a_person_is_linked_to_the_organization_planned_as_employer(writer, target, plan):
    # Act
    run(writer, target, plan)

    # Assert
    ids = {
        entries[0]["value"]: record_id for record_id, entries in writer.metadata.items() if entries
    }
    for person in plan.people:
        assert writer.employers[ids[person.external_id]] == ids[person.employer]


def test_a_record_refused_once_is_created_on_the_retry(writer, target, plan):
    # Arrange
    org = plan.organizations[0]
    writer.refuse(org.record["name"], times=1)

    # Act
    report = run(writer, target, plan)

    # Assert
    assert report.counts[ORGANIZATIONS].created == len(plan.organizations)
    assert not report.rejections


def test_a_record_refused_twice_is_reported_with_its_reason(writer, target, plan):
    # Arrange
    person = plan.people[0]
    writer.refuse(person_name(person), times=2, message="email is invalid", status=422)

    # Act
    report = run(writer, target, plan)

    # Assert
    assert report.counts[PEOPLE].rejected == 1
    assert report.counts[PEOPLE].created == len(plan.people) - 1
    assert (report.rejections[0].external_id, str(report.rejections[0].reason)) == (
        person.external_id,
        "422 email is invalid",
    )
    assert report.rejections[1].kind == "links"
    assert report.rejections[1].reason == "the person was not created"


def test_people_of_a_refused_organization_are_not_linked(writer, target, plan):
    # Arrange
    employer = plan.organizations[
        next(
            i for i, o in enumerate(plan.organizations) if o.external_id == plan.people[0].employer
        )
    ]
    writer.refuse(employer.record["name"], times=2)
    staff = [p for p in plan.people if p.employer == employer.external_id]

    # Act
    report = run(writer, target, plan)

    # Assert
    link_rejections = [r for r in report.rejections if r.kind == "links"]
    assert {r.external_id for r in link_rejections} == {p.external_id for p in staff}
    assert report.counts["links"].created == len(plan.people) - len(staff)


def test_refused_metadata_is_reported_with_the_id_of_the_created_record(
    writer, target, plan, monkeypatch
):
    # Arrange
    def refuse(kind, record_id, entries):
        raise WriteRejected("metadata is locked", 409)

    monkeypatch.setattr(writer, "set_metadata", refuse)

    # Act
    report = run(writer, target, replace(plan, people=[]))

    # Assert
    assert report.counts[ORGANIZATIONS].created == 0
    assert "but its metadata was refused: 409 metadata is locked" in report.rejections[0].reason


def test_a_second_run_creates_nothing(writer, target, plan):
    # Arrange
    run(writer, target, plan)
    writer.calls.clear()

    # Act
    report = run(writer, target, plan)

    # Assert
    assert writes(writer) == []
    assert report.counts[ORGANIZATIONS].skipped == len(plan.organizations)
    assert report.counts[PEOPLE].skipped == len(plan.people)
    assert report.counts["links"].skipped == len(plan.people)


def test_an_existing_person_with_a_wrong_employer_is_linked_again(writer, target, plan):
    # Arrange
    run(writer, target, plan)
    person_id = next(iter(writer.records[PEOPLE]))
    right = writer.employers[person_id]
    writer.employers[person_id] = "someone-else"

    # Act
    report = run(writer, target, plan)

    # Assert
    assert report.counts["links"].created == 1
    assert writer.employers[person_id] == right


def test_records_without_our_external_id_are_never_changed(writer, target, plan):
    # Arrange
    tag = [{"key": "x", "value": "y"}]
    foreign = writer.add(PEOPLE, WORKSPACE_ID, {"first_name": "Staff"}, tag)

    # Act
    report = run(writer, target, plan)

    # Assert
    assert report.untouched == 1
    assert all(name != foreign for _, name in writes(writer))
    assert writer.metadata[foreign] == [{"key": "x", "value": "y"}]


def test_a_dry_run_reads_but_writes_nothing(writer, target, plan):
    # Act
    report = run(writer, target, plan, dry_run=True)

    # Assert
    assert writes(writer) == []
    assert report.counts[ORGANIZATIONS].created == len(plan.organizations)
    assert report.counts["links"].created == len(plan.people)
    assert report.format().startswith("dry run: nothing was written")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"base_url": "https://ca.omerlo.app/api"}, "production host"),
        ({"base_url": "https://omerlo.example/api"}, "not a test host"),
        ({"workspace_id": "ws-elsewhere"}, "not visible to this key"),
        ({"locales": ("en",)}, "locale(s) fr"),
    ],
)
def test_the_guard_refuses_a_target_that_is_not_the_test_workspace(
    writer, target, plan, change, message
):
    # Arrange
    other = replace(target, **change)

    # Act / Assert
    with pytest.raises(TenantGuardError, match=re.escape(message)):
        run(writer, other, plan)
    assert writes(writer) == []


def test_the_guard_refuses_a_workspace_holding_another_tenants_records(writer, target, plan):
    # Arrange
    writer.add(ORGANIZATIONS, WORKSPACE_ID, {"name": "X", "profile_image_key": "other/a.webp"})
    writer.add(PEOPLE, WORKSPACE_ID, {"first_name": "Y", "profile_image_key": f"{TENANT_ID}/b"})

    # Act / Assert
    with pytest.raises(TenantGuardError, match="belongs to another tenant"):
        run(writer, target, plan)
    assert writes(writer) == []


def test_an_auth_error_stops_the_run_instead_of_retrying(writer, target, plan):
    # Arrange
    writer.auth_error = True

    # Act / Assert
    with pytest.raises(OmerloAuthError):
        run(writer, target, plan)
    assert [op for op, _ in writer.calls].count("create") == 1


def test_the_report_lists_counts_and_each_rejection(writer, target, plan):
    # Arrange
    writer.refuse(plan.organizations[0].record["name"], times=2, message="name taken", status=409)

    # Act
    text = run(writer, target, plan).format()

    # Assert
    assert text.splitlines()[0].startswith("organizations  created")
    assert "rejected    1" in text.splitlines()[0]
    assert f"  {plan.organizations[0].external_id}  409 name taken" in text


def test_a_payload_the_api_would_refuse_is_reported_without_being_sent(writer, target, plan):
    # Arrange
    org = plan.organizations[0]
    org.record["contact"][0]["facebook"] = "https://fb.example/page"

    # Act
    report = run(writer, target, plan)

    # Assert
    assert report.rejections[0].external_id == org.external_id
    assert "facebook must start with" in report.rejections[0].reason
    assert ("create", f"{ORGANIZATIONS}:{org.record['name']}") not in writer.calls


def test_a_link_refused_twice_is_reported_and_the_run_goes_on(writer, target, plan, monkeypatch):
    # Arrange
    calls = []

    def refuse(person_id, org_id, block_type_id):
        calls.append(person_id)
        if len(calls) <= 2:
            raise WriteRejected("relation must belong to the related profile type", 400)
        writer.employers[person_id] = org_id

    monkeypatch.setattr(writer, "set_employer", refuse)

    # Act
    report = run(writer, target, plan)

    # Assert
    assert report.counts["links"].rejected == 1
    assert report.counts["links"].created == len(plan.people) - 1
    assert report.rejections[0].reason == "400 relation must belong to the related profile type"
