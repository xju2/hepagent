import hepagent.agents.physicist as physicist
import hepagent.agents.skilled as skilled
from agents import Agent


def test_physicist_extends_scientist_prompt_with_playbook_and_clock(monkeypatch):
    monkeypatch.setattr(skilled, "get_model_provider", lambda **_kw: "model")
    monkeypatch.setattr(
        skilled.AgentManifestLoader, "get_instructions", lambda self, ctx, agent: "BASE"
    )

    agent = physicist.create()
    base = skilled.create()

    assert isinstance(agent, Agent)
    assert agent.name == "Physicist Agent"
    assert [t.name for t in agent.tools] == [t.name for t in base.tools]
    prompt = agent.instructions(None, agent)
    assert prompt.startswith("BASE\n\n# PHYSICIST WORKING RULES")
    assert "# CLOCK\nElapsed since you started: 0 s" in prompt


def test_create_app_agent_routes_physicist(monkeypatch):
    import hepagent.main as main_module

    monkeypatch.setattr(main_module, "create_physicist_agent", lambda **kw: ("physicist", kw))
    agent = main_module.create_app_agent("Physicist", model_provider="cborg", model_name=None)
    assert agent[0] == "physicist"
    assert "physicist" in main_module.get_available_agents()
