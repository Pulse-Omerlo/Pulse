# AI contribution: 50% or more AI-generated
"""Tier 1 edge cases. Database calls are mocked; these are not integration tests."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app import match as command
from app.core.tenancy import TenantContextError
from app.models import SourceRecord, Tenant
from app.processing import entity_resolution as er
from app.processing.normalization import normalize_city, normalize_organization_name
from app.services.organizations import current_records
from app.sync import SyncCommandError

TENANT = UUID(int=100)
EXCLUDED = frozenset({"gmail.com", "facebook.com"})


def record(number=1, **fields):
    return SourceRecord(
        **(
            {
                "id": UUID(int=number),
                "tenant_id": TENANT,
                "kind": "organization",
                "external_id": str(number),
                "external_ids": {},
                "attributes": {},
            }
            | fields
        )
    )


@pytest.fixture
def session(mocker):
    return mocker.MagicMock(info={})


@pytest.fixture
def domain_file(tmp_path, monkeypatch):
    path = tmp_path / "domains.toml"
    monkeypatch.setattr(er, "GENERIC_DOMAINS_PATH", path)
    er.load_generic_domains.cache_clear()
    yield path
    er.load_generic_domains.cache_clear()


def test_domain_config_cleans_duplicates_and_caches_result(domain_file):
    # Arrange
    domain_file.write_text('domains = [" Gmail.COM ", "gmail.com"]')
    # Act
    domains = er.load_generic_domains()
    domain_file.write_text('domains = ["facebook.com"]')
    # Assert
    assert domains == frozenset({"gmail.com"})
    assert er.load_generic_domains() is domains


@pytest.mark.parametrize(
    "config",
    ["other = []", "domains = []", 'domains = "gmail.com"', "domains = [42]", 'domains = ["   "]'],
)
def test_bad_domain_config_is_rejected(domain_file, config):
    # Arrange
    domain_file.write_text(config)
    # Act
    with pytest.raises(ValueError) as error:
        er.load_generic_domains()
    # Assert
    assert "non-empty domains list" in str(error.value)


def test_external_ids_ignore_bad_entries_but_preserve_case():
    # Arrange
    item = record(
        external_ids={
            " crm ": [" ABC ", "ABC", "abc", "", None, 12],
            "neq": ["ABC"],
            " ": ["bad"],
            4: ["bad"],
            "broken": "123",
        }
    )
    # Act
    keys = er.external_keys(item)
    # Assert
    assert keys == {("crm", "ABC"), ("crm", "abc"), ("neq", "ABC")}
    assert er.external_keys(record(external_ids=None)) == set()


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"domain": "exemple.ca", "linkedin": "company/exemple"}, "domain"),
        ({"domain": "gmail.com", "linkedin": "company/exemple"}, "linkedin"),
        ({"external_ids": {"neq": ["123"]}}, "external_id"),
        ({"name_normalized": "exemple", "city": "quebec"}, "name_city"),
        ({"name_normalized": "exemple"}, "name_only"),
    ],
)
def test_rules_and_domain_precedence(fields, expected):
    # Arrange
    left, right = record(2, **fields), record(1, **fields)
    # Act
    match = er.classify_pair(left, right, EXCLUDED)
    # Assert
    assert match.rule == expected
    assert match.providePair == (right.id, left.id)
    assert match.strong == (expected != "name_only")


@pytest.mark.parametrize(
    "fields",
    [
        {"id": UUID(int=1)},
        {"tenant_id": UUID(int=999)},
        {"kind": "person"},
        {"valid_to": datetime(2026, 11, 1, tzinfo=UTC)},
    ],
)
def test_invalid_pairs_cannot_match_in_either_direction(fields):
    # Arrange
    left = record(1, domain="exemple.ca")
    right = record(2, domain="exemple.ca", **fields)
    # Act
    results = [er.classify_pair(a, b, EXCLUDED) for a, b in [(left, right), (right, left)]]
    # Assert
    assert results == [None, None]


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ({"domain": "facebook.com"}, {"domain": "facebook.com"}),
        ({"linkedin": "in/person"}, {"linkedin": "in/person"}),
        ({"external_ids": {"crm": ["123"]}}, {"external_ids": {"neq": ["123"]}}),
        ({"name_normalized": "parent"}, {"name_normalized": "subsidiary"}),
    ],
)
def test_weak_identifiers_do_not_create_strong_matches(left, right):
    # Arrange
    first, second = record(1, **left), record(2, **right)
    # Act
    match = er.classify_pair(first, second, EXCLUDED)
    # Assert
    assert match is None


def test_accents_match_but_different_cities_need_review():
    # Arrange
    left = record(
        1,
        name_normalized=normalize_organization_name("Énergie Québec Inc."),
        city=normalize_city("Québec"),
    )
    right = record(
        2,
        name_normalized=normalize_organization_name("Energie Quebec"),
        city=normalize_city("Quebec City"),
    )
    # Act
    strong = er.classify_pair(left, right, EXCLUDED)
    right.city = "montreal"
    weak = er.classify_pair(left, right, EXCLUDED)
    # Assert
    assert strong.rule == "name_city"
    assert weak.rule == "name_only"


def test_bilingual_city_names_are_not_translated_into_org_matches():
    # Arrange
    left = record(1, name_normalized=normalize_organization_name("Ville de Québec"))
    right = record(2, name_normalized=normalize_organization_name("City of Quebec"))
    # Act
    match = er.classify_pair(left, right, EXCLUDED)
    # Assert
    assert match is None


def test_blocking_deduplicates_pairs_and_keeps_tenants_separate():
    # Arrange
    fields = {
        "domain": "exemple.ca",
        "linkedin": "company/exemple",
        "name_normalized": "exemple",
        "external_ids": {"crm": ["123"]},
    }
    items = [
        record(1, **fields),
        record(2, **fields),
        record(3, **fields, tenant_id=UUID(int=999)),
        record(4, **fields, kind="person"),
        record(5, **fields, valid_to=datetime.now(UTC)),
    ]
    # Act
    matches = er.blocked_matches(iter(items), EXCLUDED)
    # Assert
    assert len(matches) == 1
    assert matches[0].providePair == (items[0].id, items[1].id)


def test_evidence_keeps_both_fields_and_supporting_version():
    # Arrange
    match = er.MatchCandidateData(
        UUID(int=1), UUID(int=2), "name_city", (("name_normalized", "exemple"), ("city", "quebec"))
    )
    # Act
    proof = er.evidence(match, match.right)
    # Assert
    assert [(entry["field"], entry["value"]) for entry in proof] == list(match.fields)
    assert all(entry["matched_value"] == entry["value"] for entry in proof)
    assert all(entry["rule_set_version"] == er.RULE_VERSION for entry in proof)
    assert all(entry["matched_record_id"] == str(match.right) for entry in proof)


@pytest.mark.parametrize(
    ("first", "second", "conflict"),
    [
        ({"linkedin": "company/parent"}, {"linkedin": "company/subsidiary"}, True),
        ({"external_ids": {"neq": ["123"]}}, {"external_ids": {"neq": ["456"]}}, True),
        ({"external_ids": {"crm": ["old", "123"]}}, {"external_ids": {"crm": ["123"]}}, False),
        ({"external_ids": {"crm": ["123"]}}, {}, False),
    ],
)
def test_conflicting_ids_and_missing_ids(first, second, conflict):
    # Arrange
    records = [record(1, **first), record(2, **second)]
    # Act
    result = er.conflicting_identifiers(records)
    # Assert
    assert result is conflict


def test_components_follow_strong_paths_and_ignore_weak_bridges():
    # Arrange
    a, b, c, d, e = [UUID(int=n) for n in range(1, 6)]
    edges = [
        er.MatchCandidateData(x, y, rule, ())
        for x, y, rule in [
            (a, b, "domain"),
            (b, c, "linkedin"),
            (a, c, "external_id"),
            (c, d, "name_only"),
            (d, e, "domain"),
        ]
    ]
    # Act
    groups = er.components(edges)
    # Assert
    assert groups == [{a, b, c}, {d, e}]


def test_empty_tenant_creates_nothing_and_does_not_commit(session):
    # Arrange
    session.scalar.return_value = Tenant(id=TENANT)
    session.scalars.side_effect = [[], [], []]
    # Act
    result = er.match_organizations(session, TENANT)
    # Assert
    assert result == er.MatchResult()
    session.add.assert_not_called()
    session.commit.assert_not_called()
    assert session.info == {}


def test_unknown_tenant_stops_before_loading_records(session):
    # Arrange
    session.scalar.return_value = None
    # Act
    with pytest.raises(ValueError) as error:
        er.match_organizations(session, TENANT)
    # Assert
    assert "Unknown tenant" in str(error.value)
    session.scalars.assert_not_called()


def test_query_requires_current_versions_and_both_tenant_filters(session):
    # Arrange
    org_id = UUID(int=50)
    items = [record()]
    session.scalars.return_value = iter(items)
    # Act
    result = current_records(session, TENANT, org_id)
    query = session.scalars.call_args.args[0]
    sql = str(query)
    # Assert
    assert result == items
    assert "source_record.valid_to IS NULL" in sql
    assert "source_record.tenant_id =" in sql and "org_link.tenant_id =" in sql
    assert org_id in query.compile().params.values()
    assert session.info == {}


def test_query_cannot_switch_an_already_bound_tenant(session):
    # Arrange
    session.info["tenant_id"] = UUID(int=999)
    # Act
    with pytest.raises(TenantContextError):
        current_records(session, TENANT, UUID(int=50))
    # Assert
    session.scalars.assert_not_called()


def test_command_commits_and_prints_summary(mocker, session, capsys):
    # Arrange
    session.__enter__.return_value = session
    mocker.patch.object(command, "get_session_factory", return_value=lambda: session)
    mocker.patch.object(command, "find_tenant", return_value=Tenant(id=TENANT))
    matcher = mocker.patch.object(
        command, "match_organizations", return_value=er.MatchResult(1, 2, 3)
    )
    # Act
    command.main(["--tenant", "demo-capitale"])
    # Assert
    matcher.assert_called_once_with(session, TENANT)
    session.commit.assert_called_once()
    assert "Created 1 organizations, 2 links, 3 candidates." in capsys.readouterr().out


def test_command_unknown_tenant_does_not_commit(mocker, session, capsys):
    # Arrange
    session.__enter__.return_value = session
    mocker.patch.object(command, "get_session_factory", return_value=lambda: session)
    mocker.patch.object(command, "find_tenant", side_effect=SyncCommandError("No tenant"))
    # Act
    with pytest.raises(SystemExit) as error:
        command.main(["--tenant", "missing"])
    # Assert
    assert error.value.code == 2
    assert "No tenant" in capsys.readouterr().err
    session.commit.assert_not_called()
