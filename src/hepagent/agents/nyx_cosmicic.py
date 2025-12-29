from agents import Runner
from hepagent.agent_helpers import print_usage
from hepagent.agents.bash import create as create_bash_agent


async def main():
    task_prompt = """Your working directory is /pscratch/sd/x/xju/FoundationUniverse/nyx_sim/agent_area/v0.
    The original cosmicic code is located at /pscratch/sd/x/xju/FoundationUniverse/nyx_sim/cosmicic.
    If not already in your working directory, make a copy, and then compile it.
    If the compilation is successful, copy the executable `init` to your working directory.
    Note that if the platform is Perlmuttter, you need to load these module first:
    - cray-fftw
    - cray-hdf5-parallel
    Finally, create a parameter file named `input.par` for a cosmological simulation with Nyx.
    The cosmological parameters are:
    - hubble = 0.675
    - Omega_m = 0.31
    - Omega_bar = 0.0487
    - n_s = 0.96
    And the runtime parameters are:
    - np = 265
    - box_size = 80.0
    - seed = 343240149
    - z_in = 200.0
    You may have to read the `cosmicic/README` file located at your working directory for more details.
    After that, feel free to run the command to create the cosmic initial conditions for Nyx simulation.
"""
    agent = create_bash_agent()
    result = await Runner.run(agent, task_prompt)
    print(result.final_output)
    print_usage(result.context_wrapper.usage, agent.model.model)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
