"""Tests for the architect agent.

The model is stubbed throughout. What is under test is the part that has to hold
whatever the model says: apply, validate, repair once, and fall back to the plain
template rather than run something that does not validate.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from hepagent.agents.jfc.architect import (
    ArchitectProposal,
    ProposedEdit,
    describe_plan,
    propose_plan,
)
from hepagent.plan.templates import instantiate
from hepagent.plan.validate import validate_plan

PROMPT = "Measure the Z->bb cross section in the ee and mumu channels."


@pytest.fixture
def template():
    return instantiate(
        "jfc-measurement",
        analysis_name="zbb",
        analysis_type="measurement",
        physics_prompt=PROMPT,
    )


class _Run:
    """Stands in for `Runner.run`, returning queued outputs in order."""

    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls = 0

    async def __call__(self, agent, task, **kwargs):
        self.calls += 1
        output = self.outputs.pop(0) if self.outputs else None
        if isinstance(output, Exception):
            raise output
        return type("Result", (), {"final_output": output})()


def run_with(*outputs):
    return patch("hepagent.agents.jfc.architect.Runner.run", new=_Run(*outputs))


async def propose(**kwargs):
    return await propose_plan(
        physics_prompt=PROMPT, analysis_name="zbb", analysis_type="measurement", **kwargs
    )


def split(node_id, *into):
    return ProposedEdit(op="split_node", node_id=node_id, into=list(into))


# ------------------------------------------------------------- happy paths


@pytest.mark.asyncio
async def test_no_edits_means_the_template_fits(template):
    proposal = ArchitectProposal(rationale="Single channel; the template fits.", edits=[])
    with run_with(proposal):
        result = await propose()

    assert result.accepted
    assert result.plan.to_dict() == template.to_dict()
    assert result.rationale.startswith("Single channel")
    assert any("no edits" in note for note in result.notes)


@pytest.mark.asyncio
async def test_a_valid_multichannel_proposal_is_accepted():
    proposal = ArchitectProposal(
        rationale="Two channels are selected independently.",
        edits=[
            split("exploration", "exploration_ee", "exploration_mumu"),
            split("selection", "selection_ee", "selection_mumu"),
        ],
    )
    with run_with(proposal):
        result = await propose()

    assert result.accepted
    assert {"selection_ee", "selection_mumu"} <= set(result.plan.node_ids())
    assert validate_plan(result.plan).blocking == []
    assert any("accepted" in note for note in result.notes)


@pytest.mark.asyncio
async def test_the_architect_is_asked_only_once_when_the_proposal_validates():
    runner = _Run(ArchitectProposal(rationale="ok", edits=[split("selection", "sel_ee", "sel_mm")]))
    with patch("hepagent.agents.jfc.architect.Runner.run", new=runner):
        await propose()
    assert runner.calls == 1


# ------------------------------------------------------------ partial failure


@pytest.mark.asyncio
async def test_an_unapplicable_edit_is_skipped_and_the_rest_still_land():
    proposal = ArchitectProposal(
        rationale="Split selection; also drop a node that is not there.",
        edits=[
            split("selection", "selection_ee", "selection_mumu"),
            ProposedEdit(op="remove_node", node_id="phase_nine"),
        ],
    )
    with run_with(proposal):
        result = await propose()

    assert result.accepted
    assert "selection_ee" in result.plan.node_ids()
    assert any("skipped" in note for note in result.notes)


# ------------------------------------------------------------------- repair


@pytest.mark.asyncio
async def test_an_invalid_proposal_gets_one_repair_round():
    broken = ArchitectProposal(
        rationale="Wire documentation back into strategy.",
        edits=[ProposedEdit(op="add_edge", upstream="documentation", downstream="strategy")],
    )
    fixed = ArchitectProposal(
        rationale="Corrected: no cycle.",
        edits=[split("selection", "selection_ee", "selection_mumu")],
    )
    runner = _Run(broken, fixed)
    with patch("hepagent.agents.jfc.architect.Runner.run", new=runner):
        result = await propose()

    assert runner.calls == 2
    assert result.accepted
    assert "selection_ee" in result.plan.node_ids()
    assert any("asking for a repair" in note for note in result.notes)


@pytest.mark.asyncio
async def test_the_repair_prompt_carries_the_blocking_findings():
    """The architect has to be told what was wrong, not just that it was wrong."""
    broken = ArchitectProposal(
        rationale="cycle",
        edits=[ProposedEdit(op="add_edge", upstream="documentation", downstream="strategy")],
    )
    seen: list[str] = []

    async def capture(agent, task, **kwargs):
        seen.append(agent.instructions)
        return type("Result", (), {"final_output": broken})()

    with patch("hepagent.agents.jfc.architect.Runner.run", new=capture):
        await propose()

    assert len(seen) == 2
    assert "DID NOT VALIDATE" in seen[1]
    assert "P4-acyclic" in seen[1]


@pytest.mark.asyncio
async def test_a_proposal_that_stays_invalid_falls_back_to_the_template(template):
    broken = ArchitectProposal(
        rationale="cycle",
        edits=[ProposedEdit(op="add_edge", upstream="documentation", downstream="strategy")],
    )
    with run_with(broken, broken):
        result = await propose()

    assert not result.accepted
    assert result.plan.to_dict() == template.to_dict()
    assert any("still does not validate" in note for note in result.notes)


# ----------------------------------------------------------------- fallbacks


@pytest.mark.asyncio
async def test_a_model_error_falls_back_to_the_template(template):
    with run_with(RuntimeError("provider is down")):
        result = await propose()

    assert not result.accepted
    assert result.plan.to_dict() == template.to_dict()
    assert any("provider is down" in note for note in result.notes)


@pytest.mark.asyncio
async def test_an_unstructured_answer_falls_back_to_the_template(template):
    with run_with("Sure! Here is a plan in prose."):
        result = await propose()

    assert not result.accepted
    assert result.plan.to_dict() == template.to_dict()
    assert any("no structured proposal" in note for note in result.notes)


@pytest.mark.asyncio
async def test_a_failed_repair_falls_back_to_the_template(template):
    broken = ArchitectProposal(
        rationale="cycle",
        edits=[ProposedEdit(op="add_edge", upstream="documentation", downstream="strategy")],
    )
    with run_with(broken, RuntimeError("timed out")):
        result = await propose()

    assert not result.accepted
    assert result.plan.to_dict() == template.to_dict()
    assert any("repair failed" in note for note in result.notes)


@pytest.mark.asyncio
async def test_the_fallback_plan_is_always_runnable(template):
    """Whatever goes wrong, what comes back is a plan that validates."""
    for output in (RuntimeError("boom"), "prose", None):
        with run_with(output):
            result = await propose()
        assert validate_plan(result.plan).blocking == []
        assert result.plan.nodes


@pytest.mark.asyncio
async def test_the_search_template_can_be_proposed_against():
    proposal = ArchitectProposal(rationale="fits", edits=[])
    with run_with(proposal):
        result = await propose(template="jfc-search")
    assert result.accepted
    assert result.plan.template == "jfc-search"


# ------------------------------------------------------------ prompt content


def test_describe_plan_shows_structure_in_full_and_prompts_in_excerpt(template):
    described = describe_plan(template)

    for node in template.nodes:
        assert node.id in described
        assert node.artifact_path in described
    # The full prompts are far longer than what is shown.
    assert len(described) < sum(len(n.prompt) for n in template.nodes)
    assert "…" in described


def test_describe_plan_names_the_gates(template):
    described = describe_plan(template)
    assert "commitments/before" in described
    assert "human/after" in described


@pytest.mark.asyncio
async def test_the_architect_prompt_carries_the_physics_prompt_and_the_template():
    seen: list[str] = []

    async def capture(agent, task, **kwargs):
        seen.append(agent.instructions)
        return type("Result", (), {"final_output": ArchitectProposal(rationale="ok", edits=[])})()

    with patch("hepagent.agents.jfc.architect.Runner.run", new=capture):
        await propose()

    instructions = seen[0]
    assert PROMPT in instructions
    assert "# TEMPLATE PLAN" in instructions
    assert "split_node" in instructions  # the role definition reached the prompt


@pytest.mark.asyncio
async def test_the_architect_returns_structured_output():
    captured: list = []

    async def capture(agent, task, **kwargs):
        captured.append(agent.output_type)
        return type("Result", (), {"final_output": ArchitectProposal(rationale="ok", edits=[])})()

    with patch("hepagent.agents.jfc.architect.Runner.run", new=capture):
        await propose()

    assert captured[0] is ArchitectProposal


# ------------------------------------------------------------- output schema
#
# The tests above stub `Runner.run`, so they never exercise the SDK's schema
# layer. These do: a proposal type that cannot be expressed as a strict JSON
# schema fails at the provider, not here, and only under a real model call.


def test_the_proposal_type_produces_a_strict_json_schema():
    from agents.agent_output import AgentOutputSchema

    schema = AgentOutputSchema(ArchitectProposal)
    assert schema.is_strict_json_schema()
    assert schema.json_schema()  # raises if the type cannot be expressed


def test_a_realistic_proposal_round_trips_through_the_schema():
    """Strict mode requires every field present, so nulls must be accepted."""
    import json

    from agents.agent_output import AgentOutputSchema

    nulls = dict.fromkeys(
        (
            "label",
            "directory",
            "artifact",
            "prompt",
            "like",
            "upstream",
            "downstream",
            "kind",
            "inject",
        )
    )
    payload = json.dumps(
        {
            "rationale": "Two channels are selected independently.",
            "edits": [
                {
                    "op": "split_node",
                    "node_id": "selection",
                    "into": ["selection_ee", "selection_mumu"],
                    **nulls,
                }
            ],
        }
    )
    parsed = AgentOutputSchema(ArchitectProposal).validate_json(payload)
    assert parsed.edits[0].op == "split_node"
    assert parsed.edits[0].into == ["selection_ee", "selection_mumu"]
    assert parsed.edits[0].prompt is None
