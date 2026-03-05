from agents import Agent
from hepagent.agents.common import AgentContext
from hepagent.model_providers import get_model_provider
from hepagent.tools.common import ask_user_for_info
from hepagent.tools.iri.compute import get_job_status, submit_job


def create(
    model_provider: str = "cborg",
    model_name: str | None = None,
) -> Agent[AgentContext]:
    instructions = """You are an expert agent
    facilitate other agents in running their compute tasks
    by submitting jobs to IRI and checking their status.
    Ask users if any information is missing.
    """
    agent = Agent[AgentContext](
        name="IRI Agent",
        instructions=instructions,
        model=get_model_provider(model_provider=model_provider, model_name=model_name),
        tools=[
            submit_job,
            get_job_status,
            ask_user_for_info,
        ],
    )
    return agent


async def main(agent_name: str = "nyx"):
    from agents import Runner

    task_prompt = "Please run the command `dummy.sh triton` at AmSC platform with account of m3443."
    agent = create()
    context = AgentContext(agent_name=agent_name)
    result = await Runner.run(agent, task_prompt, context=context)
    print(result.final_output)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
