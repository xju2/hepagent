from agents import Runner
from hepagent.agent_helpers import print_usage
from hepagent.agents.bash import create as create_bash_agent


async def main():
    task_prompt = """Your working directory is /pscratch/sd/x/xju/FoundationUniverse/nyx_sim/agent_area/v0.
    The original cosmicic code is located at /pscratch/sd/x/xju/FoundationUniverse/nyx_sim/cosmicic.
    Make a copy of cosmicic code to your working directory, compile it.
    If the compilation is successful, copy the executable `init` to your working directory,
    where you will use it to generate initial conditions.
    Note that if the platform is Perlmuttter, you need to load these module first:
    - cray-fftw
    - cray-hdf5-parallel
    Finally, tell me if cosmic initial condition generation is ready.
"""
    agent = create_bash_agent()
    result = await Runner.run(agent, task_prompt)
    print(result.final_output)
    print_usage(result.context_wrapper.usage)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
