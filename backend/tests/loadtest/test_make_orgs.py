# AI contribution: 50% or more AI-generated
import csv

import pytest

from app.ingestion.files.columns import LAYOUTS
from loadtest.make_orgs import COLUMNS, DUPLICATE_EVERY, main, make_rows


def test_one_organization_in_ten_gets_a_duplicate_row():
    # Act
    rows = make_rows(100)

    # Assert
    duplicates = [row for row in rows if row["id"].endswith("-dup")]
    assert len(duplicates) == 100 // DUPLICATE_EVERY
    assert len(rows) == 100 + len(duplicates)


def test_a_duplicate_keeps_the_website_of_its_organization():
    # Act
    first, duplicate = make_rows(1)

    # Assert
    assert duplicate["id"] == "load-0-dup"
    assert duplicate["website"] == first["website"]
    assert duplicate["name"] == first["name"].upper()


def test_ids_are_unique():
    # Act
    rows = make_rows(500)

    # Assert
    ids = [row["id"] for row in rows]
    assert len(ids) == len(set(ids))


def test_the_same_seed_gives_the_same_rows():
    # Act / Assert
    assert make_rows(50, seed=7) == make_rows(50, seed=7)


def test_the_columns_are_the_file_import_layout():
    # Act / Assert
    assert tuple(COLUMNS) == LAYOUTS["organization"].columns


def test_main_writes_a_csv_with_a_header(tmp_path):
    # Arrange
    out = tmp_path / "out" / "organizations.csv"

    # Act
    main(["--count", "20", "--out", str(out)])

    # Assert
    with out.open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    assert len(rows) == 22
    assert list(rows[0]) == COLUMNS


def test_main_rejects_a_count_below_one(tmp_path):
    # Act / Assert
    with pytest.raises(SystemExit):
        main(["--count", "0", "--out", str(tmp_path / "x.csv")])
