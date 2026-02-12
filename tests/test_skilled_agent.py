import pytest

from agents import Runner
from hepagent.agents.common import AgentContext
from hepagent.agents.skilled import create as create_skilled_agent


@pytest.mark.asyncio
async def test_skilled_agent_skill_cycle(mock_agent_env):
    from hepagent.helpers import get_agent_dir

    print(f"\n[DEBUG] Mock Dir: {mock_agent_env}")
    print(f"[DEBUG] Helper returns: {get_agent_dir()}")

    # 1. Initialize Agent and Context
    agent = create_skilled_agent()
    context = AgentContext(agent_name="Scientific Researcher")

    # 2. Verify Skill Loading (State Management)
    # The agent should call load_skill_details because it sees 'nyx' in the catalog
    task = "I need to start a Nyx project."
    await Runner.run(agent, task, context=context)

    assert context.active_skill == "nyx"

    # 3. Verify Logbook Tool (Structured Input)
    # We simulate a failure and check if the agent logs it
    error_task = "The bash command failed with error 'Timeout'. Log this immediately."
    await Runner.run(agent, error_task, context=context)

    # Check the actual file in the mock directory
    logbook_file = mock_agent_env / "skills" / "nyx" / "LOGBOOK.md"
    log_content = logbook_file.read_text()

    assert "Timeout" in log_content
