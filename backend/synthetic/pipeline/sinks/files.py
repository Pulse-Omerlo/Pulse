# AI contribution: 50% or more AI-generated
"""Write a plan as fixture files, in the layout the mock Omerlo server serves."""

from pathlib import Path

from synthetic.omerlo_fixtures import TenantData, write_fixtures
from synthetic.pipeline.plan import EXTERNAL_ID_KEY, Plan


def write_files(plan: Plan, out_dir: Path) -> Path:
    """Fixture files for one tenant named after the mix; returns the tenant's folder."""
    metadata = {}
    for org in plan.organizations:
        metadata[org.record["id"]] = [
            {"key": EXTERNAL_ID_KEY, "value": org.external_id},
            *org.metadata,
        ]
    for person in plan.people:
        metadata[person.record["id"]] = [{"key": EXTERNAL_ID_KEY, "value": person.external_id}]
    data = TenantData(
        plan.tenant,
        resources={
            "workspaces": plan.workspaces,
            "categories": plan.categories,
            "organizations": [o.record for o in plan.organizations],
            "people": [p.record for p in plan.people],
            "projects": [],
            "events": [],
            "event_sessions": [],
        },
        metadata=metadata,
        ground_truth=plan.ground_truth,
    )
    write_fixtures([data], out_dir, plan.mix.seed)
    return out_dir / plan.tenant.slug
