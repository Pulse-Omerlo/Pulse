# AI contribution: 50% or more AI-generated
import copy
import re

import pytest

from synthetic.pipeline.payloads import PayloadError, organization_payload, person_payload


def test_organization_payload_drops_server_fields_and_the_linkedin_scheme(plan, target):
    # Arrange
    record = copy.deepcopy(plan.organizations[0].record)
    record["contact"][0]["linkedin"] = "https://www.linkedin.com/company/demo"

    # Act
    payload = organization_payload(record, target)

    # Assert
    assert payload["name"] == record["name"]
    assert payload["profile"] == {"profile_type_id": "type-org", "status_id": "status-done"}
    assert payload["contact"][0]["linkedin"] == "www.linkedin.com/company/demo"
    for entry in payload["contact"] + payload["address"] + payload["locales"]:
        assert "id" not in entry
        assert "deleted_at" not in entry
    assert all(v is not None for v in payload["description"][0]["blocks"][0].values())


def test_person_payload_uses_the_target_person_type(plan, target):
    # Arrange
    record = plan.people[0].record

    # Act
    payload = person_payload(record, target)

    # Assert
    assert payload["first_name"] == record["first_name"]
    assert payload["profile"]["profile_type_id"] == "type-person"
    assert payload["locales"][0]["job_title"] == record["locales"][0]["job_title"]


def test_a_social_link_with_the_wrong_prefix_is_refused_before_sending(plan, target):
    # Arrange
    record = copy.deepcopy(plan.people[0].record)
    record["contact"][0]["facebook"] = "https://fb.example/someone"

    # Act / Assert
    with pytest.raises(PayloadError, match=re.escape("facebook must start with www.facebook.com/")):
        person_payload(record, target)
