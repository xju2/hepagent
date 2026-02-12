from agents import Agent
from hepagent.agent_helpers import AgentManifestLoader, update_logbook
from hepagent.agents.bash import execute_bash_command_with_confirmation
from hepagent.agents.common import AgentContext
from hepagent.model_providers import get_cborg_model_provider


def create() -> Agent[AgentContext]:
    # 1. Initialize the loader for a specific soldier
    # It will look in .agents/skills/"agent_name"/ for SOUL.md and WORLD.md
    loader = AgentManifestLoader()

    agent = Agent[AgentContext](
        name="Skilled Agent",
        instructions=loader.get_instructions,
        model=get_cborg_model_provider(),
        tools=[execute_bash_command_with_confirmation, update_logbook],
    )
    return agent


async def main(agent_name: str = "nyx"):
    from agents import Runner

    task_prompt = "List the files in the current directory and tell me how many there are."
    agent = create()
    context = AgentContext(agent_name=agent_name)
    result = await Runner.run(agent, task_prompt, context=context)
    print(result.final_output)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
