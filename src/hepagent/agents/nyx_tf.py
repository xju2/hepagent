from agents import Agent, Runner
from hepagent.agent_helpers import print_usage
from hepagent.agents.nyx_context import NyxContext
from hepagent.model_providers import get_cborg_model_provider
from hepagent.tools.nyx.transfer_function import create_transfer_function


def create() -> Agent[NyxContext]:
    agent = Agent[NyxContext](
        name="Cosmic Transfer Function Creator",
        instructions=(
            "Create a cosmic transfer function for a given set of cosmological parameters."
            "And save the output to the agent working directory, if not specified."
        ),
        model=get_cborg_model_provider(),
        tools=[create_transfer_function],
    )
    return agent


async def main():
    task_prompt = """Create a cosmic transfer function for a given set of cosmological parameters:
    - h = 0.675
    - Omega_m = 0.31
    - Omega_b = 0.0487
    - n_s = 0.96
    - z_ic = 200
    And save the output to /pscratch/sd/x/xju/FoundationUniverse/nyx_sim/agent_area/v0/cmb.tf"
"""
    agent = create()
    result = await Runner.run(agent, task_prompt)
    print(result.final_output)
    print_usage(result.context_wrapper.usage, agent.model.model)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
