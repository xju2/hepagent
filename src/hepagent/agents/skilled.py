from agents import Agent, RunContextWrapper
from hepagent.agent_helpers import AgentManifestLoader
from hepagent.agents.bash import execute_bash_command_with_confirmation
from hepagent.agents.common import AgentContext
from hepagent.model_providers import get_cborg_model_provider


def dynamic_instructions(
    context: RunContextWrapper[AgentContext], agent: Agent[AgentContext]
) -> str:
    return f"The user's name is {context.context.agent_name}. Help them with their questions."


def create(agent_name: str = "nyx") -> Agent:
    # 1. Initialize the loader for a specific soldier
    # It will look in .agents/skills/"agent_name"/ for SOUL.md and WORLD.md
    loader = AgentManifestLoader()

    agent = Agent[AgentContext](
        name=f"{agent_name.capitalize()} Agent",
        instructions=loader.get_instructions,
        model=get_cborg_model_provider(),
        tools=[execute_bash_command_with_confirmation],
    )
    return agent


async def main():
    from agents import Runner

    task_prompt = "List the files in the current directory and tell me how many there are."
    agent = create()
    result = await Runner.run(agent, task_prompt)
    print(result.final_output)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
