# AI contribution: 50% or more AI-generated
"""Match organizations with a Splink probabilistic model (dev only, compared with Tier 1 and 2)."""

# Fields Splink compares. Each one is a column of the rows built below.
MATCH_FIELDS = ("name_normalized", "city", "domain", "linkedin")


def splink_rows(records):
    """One row per source record in the shape Splink reads. Blank fields become None."""
    rows = []
    for record in records:
        row = {"unique_id": record.external_id, "tenant_id": str(record.tenant_id)}
        for field in MATCH_FIELDS:
            # Splink treats None as missing, but an empty string as a real value.
            row[field] = getattr(record, field) or None
        rows.append(row)
    return rows
