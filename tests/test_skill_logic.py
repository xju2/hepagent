import pytest

from agents import Runner
from hepagent.agents.common import AgentContext
from hepagent.agents.skilled import create as create_skilled_agent


@pytest.mark.asyncio
async def test_nyx_skill_activation_and_logging():
    # Setup
    agent = create_skilled_agent()
    context = AgentContext(agent_name="nyx")

    # 1. Test Discovery: Does it load the skill when asked?
    task = "I need to set up a Nyx simulation."
    result = await Runner.run(agent, task, context=context)

    # Check if the tool was actually triggered and context updated
    assert context.active_skill == "nyx"

    # 2. Test Critical Rule: Does it log an error?
    error_task = "The bash command to create the directory failed with 'Disk Full'. Record this."
    await Runner.run(agent, error_task, context=context)

    # Verify the logbook file was actually written to
    # (Assuming your get_agent_path logic points to a testable temp dir or the local .agents)
    from hepagent.agent_helpers import get_agent_path

    log_path = get_agent_path(context) / "LOGBOOK.md"

    with open(log_path) as f:
        content = f.read()
        assert "Disk Full" in content
