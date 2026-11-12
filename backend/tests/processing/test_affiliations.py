# AI contribution: 50% or more AI-generated
"""Linking people to organizations from relations, work emails and job titles."""

import uuid

from app.processing.affiliations import (
    CONFLICT,
    EMAIL_DOMAIN,
    JOB_TITLE,
    LINKED,
    RELATION,
    OrganizationFacts,
    PersonFacts,
    affiliate,
    work_domain,
)

FILE = uuid.UUID(int=1)
OMERLO = uuid.UUID(int=2)
GENERIC = frozenset({"gmail.com", "wixsite.com"})


def org(n: int, name: str, *domains: str, key: int | None = None, source=FILE) -> OrganizationFacts:
    record_id = uuid.UUID(int=100 + n)
    return OrganizationFacts(
        record_id=record_id,
        key=uuid.UUID(int=key) if key else record_id,
        source_id=source,
        external_id=f"org-{n}",
        name_normalized=name,
        domains=frozenset(domains),
    )


def person(n: int = 1, *, emails=(), titles=(), refs=(), source=FILE) -> PersonFacts:
    return PersonFacts(
        record_id=uuid.UUID(int=n),
        source_id=source,
        emails=tuple(emails),
        job_titles=tuple(titles),
        organization_refs=tuple(refs),
    )


ACME = org(1, "acme logistique", "acme.ca")
BOREAL = org(2, "boreal design", "boreal.qc.ca")


def summary(decisions):
    return [(d.organization_record_id, d.status, d.method) for d in decisions]


def test_a_relation_links_the_person_to_that_organization():
    # Act
    decisions = affiliate([person(refs=["org-1"])], [ACME, BOREAL], GENERIC)

    # Assert
    assert summary(decisions) == [(ACME.record_id, LINKED, RELATION)]
    assert decisions[0].confidence == 1.0
    assert decisions[0].evidence == (
        {"signal": RELATION, "value": "org-1", "field": "organization_id", "source": "org-1"},
    )


def test_a_person_can_have_several_relations():
    # Act
    decisions = affiliate([person(refs=["org-1", "org-2"])], [ACME, BOREAL], GENERIC)

    # Assert
    assert {(d.organization_record_id, d.status) for d in decisions} == {
        (ACME.record_id, LINKED),
        (BOREAL.record_id, LINKED),
    }


def test_a_relation_only_names_organizations_of_the_same_source():
    # Arrange
    elsewhere = org(1, "acme logistique", source=OMERLO)

    # Act / Assert
    assert affiliate([person(refs=["org-1"])], [elsewhere], GENERIC) == []


def test_a_relation_wins_over_inferred_signals_pointing_elsewhere():
    # Act
    decisions = affiliate(
        [person(refs=["org-1"], emails=["ana@boreal.qc.ca"])], [ACME, BOREAL], GENERIC
    )

    # Assert
    assert summary(decisions) == [(ACME.record_id, LINKED, RELATION)]


def test_a_work_email_links_the_person_to_the_organization_with_that_domain():
    # Act
    decisions = affiliate([person(emails=["ana@acme.ca"])], [ACME, BOREAL], GENERIC)

    # Assert
    assert summary(decisions) == [(ACME.record_id, LINKED, EMAIL_DOMAIN)]
    assert decisions[0].confidence == 0.9
    assert decisions[0].evidence[0] == {
        "signal": EMAIL_DOMAIN,
        "value": "acme.ca",
        "field": "email",
        "source": "ana@acme.ca",
    }


def test_an_email_on_a_subdomain_counts_as_the_organization_domain():
    # Act
    decisions = affiliate([person(emails=["ana@mail.boreal.qc.ca"])], [ACME, BOREAL], GENERIC)

    # Assert
    assert summary(decisions) == [(BOREAL.record_id, LINKED, EMAIL_DOMAIN)]


def test_generic_and_personal_email_domains_never_create_a_link():
    # Arrange
    webmail = org(3, "gmail", "gmail.com")
    isp = org(4, "videotron", "videotron.ca")
    people = [person(1, emails=["ana@gmail.com"]), person(2, emails=["bob@videotron.ca"])]

    # Act / Assert
    assert affiliate(people, [webmail, isp], GENERIC) == []
    assert work_domain("ana@gmail.com", GENERIC) is None


def test_an_organization_named_in_the_job_title_is_linked():
    # Act
    decisions = affiliate(
        [person(titles=["Directrice des ventes, Acme Logistique inc."])], [ACME, BOREAL], GENERIC
    )

    # Assert
    assert summary(decisions) == [(ACME.record_id, LINKED, JOB_TITLE)]
    assert decisions[0].confidence == 0.6


def test_a_job_title_must_hold_the_whole_name_as_words():
    # Arrange
    short = org(3, "vert")
    single = org(4, "design")

    # Act
    decisions = affiliate(
        [person(titles=["Designer vert", "Boreal designers"])], [short, single, BOREAL], GENERIC
    )

    # Assert
    assert decisions == []


def test_signals_that_agree_give_one_link_with_all_their_evidence():
    # Act
    decisions = affiliate(
        [person(emails=["ana@acme.ca"], titles=["Analyste chez Acme Logistique"])],
        [ACME, BOREAL],
        GENERIC,
    )

    # Assert
    assert summary(decisions) == [(ACME.record_id, LINKED, EMAIL_DOMAIN)]
    assert [item["signal"] for item in decisions[0].evidence] == [EMAIL_DOMAIN, JOB_TITLE]


def test_signals_that_disagree_link_nothing_and_keep_each_candidate_as_a_conflict():
    # Act
    decisions = affiliate(
        [person(emails=["ana@acme.ca"], titles=["Conseillère, Boreal Design"])],
        [ACME, BOREAL],
        GENERIC,
    )

    # Assert
    assert {(d.organization_record_id, d.status, d.method) for d in decisions} == {
        (ACME.record_id, CONFLICT, EMAIL_DOMAIN),
        (BOREAL.record_id, CONFLICT, JOB_TITLE),
    }
    assert all(d.confidence == 0 for d in decisions)


def test_a_domain_shared_by_two_organizations_is_a_conflict():
    # Arrange
    twin = org(3, "acme transport", "acme.ca")

    # Act
    decisions = affiliate([person(emails=["ana@acme.ca"])], [ACME, twin], GENERIC)

    # Assert
    assert {d.status for d in decisions} == {CONFLICT}
    assert len(decisions) == 2


def test_records_of_one_matched_organization_give_one_link_to_the_lowest_record():
    # Arrange
    first = org(1, "acme logistique", "acme.ca", key=50)
    second = org(2, "acme logistique", "acme.ca", key=50)

    # Act
    decisions = affiliate([person(emails=["ana@acme.ca"])], [second, first], GENERIC)

    # Assert
    assert summary(decisions) == [(first.record_id, LINKED, EMAIL_DOMAIN)]


def test_people_without_signals_get_nothing_and_results_are_in_a_stable_order():
    # Arrange
    people = [person(3, emails=["c@acme.ca"]), person(1), person(2, emails=["b@boreal.qc.ca"])]

    # Act
    decisions = affiliate(people, [ACME, BOREAL], GENERIC)

    # Assert
    assert [d.person_record_id.int for d in decisions] == [2, 3]
