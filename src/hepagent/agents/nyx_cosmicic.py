from agents import Agent, Runner
from hepagent.model_providers import get_cborg_model_provider
from hepagent.agent_helpers import print_usage


def create() -> Agent:
    agent = Agent(
        name="Comisic Initial Condition Generator",
        instructions=(
            "You are good at compiling and running the cosmic initial"
            "condition generator `cosmicic`."
            "Users have to provide a local copy of the code: git@bitbucket-lbnl:zarija/cosmicic.git"
        ),
        model=get_cborg_model_provider(),
        tools=[],
    )
    return agent


async def main():
    task_prompt = """My cosmicic code is located at /pscratch/sd/x/xju/FoundationUniverse/nyx_sim/cosmicic
    Please make a copy of the code to /pscratch/sd/x/xju/FoundationUniverse/nyx_sim/agent_area/v0/cosmicic
    and compile it. If the compilation is successful, copy the executable `init` to
    the work directory: /pscratch/sd/x/xju/FoundationUniverse/nyx_sim/agent_area. run it to generate initial conditions.
    Note that if the platform is Perlmuttter, you need to load these module first:
    - cray-fftw
    - cray-hdf5-parallel
"""
    agent = create()
    result = await Runner.run(agent, task_prompt)
    print(result.final_output)
    print_usage(result.context_wrapper.usage)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
