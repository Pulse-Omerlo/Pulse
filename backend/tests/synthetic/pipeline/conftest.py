# AI contribution: 50% or more AI-generated
import pytest

from synthetic.pipeline.mixer import Mix
from synthetic.pipeline.plan import build_plan


@pytest.fixture
def small_mix():
    """20 organizations split between two sectors; sector 54 holds only 10% of the people."""
    return Mix(
        name="small",
        seed=7,
        organizations=20,
        people=30,
        sectors={"54": 0.5, "72": 0.5},
        gaps={"54": 0.1},
        duplicate_org_share=0.1,
        duplicate_person_share=0.1,
        locales=("fr",),
    )


@pytest.fixture
def plan(small_mix):
    return build_plan(small_mix)
