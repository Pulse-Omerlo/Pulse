# AI contribution: 50% or more AI-generated
"""Generate and clean 2,500 synthetic records through the production adapter."""

import json
import sys
from dataclasses import asdict
from pathlib import Path


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
    from app.ingestion.mapped import MappedRecord, clean_mapped

    records = [
        MappedRecord(
            kind="organization",
            external_id=str(index),
            workspace_id=" demo ",
            name_normalized=f"  Socie\u0301te\u0301   {index}  ",
            city=" N/A " if index % 5 == 0 else "  Montre\u0301al  ",
            attributes={"synthetic": True},
        )
        for index in range(2500)
    ]
    result = clean_mapped(records)
    if len(result.records) != 2500 or result.failures or result.cleaning_flag_counts:
        raise RuntimeError("Unexpected cleaning result")
    for index, record in enumerate(result.records):
        expected_city = None if index % 5 == 0 else "Montréal"
        if (
            record.name_normalized != f"Société {index}"
            or record.city != expected_city
            or record.workspace_id != "demo"
        ):
            raise RuntimeError(f"Unexpected output for record {index}")
    print(
        json.dumps(
            {
                "received": len(records),
                "cleaned": len(result.records),
                "failures": len(result.failures),
                "flags": result.cleaning_flag_counts,
                "sample": [asdict(record) for record in result.records[:3]],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
