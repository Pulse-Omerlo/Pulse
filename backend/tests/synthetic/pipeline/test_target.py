# AI contribution: 50% or more AI-generated
import re

import pytest

from synthetic.pipeline.target import DEFAULT_TARGET, load_target


def test_the_staging_target_file_has_every_id_a_run_needs():
    # Act
    target = load_target(DEFAULT_TARGET)

    # Assert
    assert target.base_url == "https://staging.omerlo.app/api"
    assert target.locales == ("fr",)
    assert all([target.workspace_id, target.organization_type, target.person_type])
    assert target.employer_block_type


def test_a_target_missing_a_section_names_it(tmp_path):
    # Arrange
    path = tmp_path / "broken.toml"
    path.write_text('base_url = "https://staging.omerlo.app/api"\n', encoding="utf-8")

    # Act / Assert
    with pytest.raises(ValueError, match=re.escape("broken.toml: missing 'tenant_id'")):
        load_target(path)
