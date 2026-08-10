"""Fixtures for the plan tests. The builders themselves live in `plan_factory`."""

from __future__ import annotations

import pytest
from plan_factory import make_plan

from hepagent.plan.schema import AnalysisPlan


@pytest.fixture
def plan() -> AnalysisPlan:
    """A two-node linear plan: a -> b."""
    return make_plan()


@pytest.fixture
def fan_plan() -> AnalysisPlan:
    """A fan-out/fan-in plan: root -> (left, right) -> merge."""
    return make_plan(
        node_ids=("root", "left", "right", "merge"),
        edges=(("root", "left"), ("root", "right"), ("left", "merge"), ("right", "merge")),
    )
