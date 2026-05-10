from types import SimpleNamespace

import pytest

import hepagent.agents.explorer as explorer_module
from hepagent.agents.explorer import _select_specialists, create, run_specialist_panel


def test_select_specialists_defaults_to_broad_panel():
    selected = _select_specialists()

    assert [specialist.key for specialist in selected] == ["collider", "cosmology", "neutrino"]


def test_select_specialists_accepts_requested_keys():
    selected = _select_specialists("theory, instrumentation")

    assert [specialist.key for specialist in selected] == ["theory", "instrumentation"]


def test_select_specialists_rejects_unknown_key():
    with pytest.raises(ValueError, match="Unknown specialist"):
        _select_specialists("cosmology,unknown")


def test_create_explorer_agent(monkeypatch):
    captured = {}
    sentinel_model = object()

    class StubAgent:
        def __init__(self, name, instructions, model, tools, **kwargs):
            captured["name"] = name
            captured["instructions"] = instructions
            captured["model"] = model
            captured["tools"] = tools

    monkeypatch.setattr(explorer_module, "Agent", StubAgent)
    monkeypatch.setattr(explorer_module, "get_model_provider", lambda **_: sentinel_model)

    agent = create(model_provider="openai", model_name="gpt-test")

    assert isinstance(agent, StubAgent)
    assert captured["name"] == "Explorer Agent"
    assert captured["model"] is sentinel_model
    assert len(captured["tools"]) == 1
    assert "explore_research_directions" in captured["instructions"]


@pytest.mark.asyncio
async def test_run_specialist_panel_runs_selected_agents(monkeypatch):
    calls = []

    class StubAgent:
        def __init__(self, name, instructions, model, tools, **kwargs):
            self.name = name
            self.instructions = instructions
            self.model = model
            self.tools = tools

    async def fake_run(agent, prompt):
        calls.append((agent.name, prompt))
        return SimpleNamespace(final_output=f"memo from {agent.name}")

    monkeypatch.setattr(explorer_module, "Agent", StubAgent)
    monkeypatch.setattr(explorer_module, "get_model_provider", lambda **_: "model")
    monkeypatch.setattr(explorer_module.Runner, "run", fake_run)

    output = await run_specialist_panel(
        "How can weak lensing inform neutrino mass?",
        interests="survey systematics",
        specialist_keys="cosmology,neutrino",
        model_provider="cborg",
        model_name="test-model",
    )

    assert [name for name, _ in calls] == [
        "Cosmology Specialist",
        "Neutrino Physics Specialist",
    ]
    assert "survey systematics" in calls[0][1]
    assert "Parallel specialist findings" in output
    assert "memo from Cosmology Specialist" in output
    assert "memo from Neutrino Physics Specialist" in output
