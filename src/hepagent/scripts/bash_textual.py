from hepagent.agents.textual import TextualAgent
from hepagent.model_providers import DEFAULT_CBORG_MODEL

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run the Textual Bash Agent")
    parser.add_argument(
        "-t",
        "--task",
        type=str,
        choices=["dummy", "real", "cosmicic"],
        default="dummy",
        help="Type of agent to run: 'dummy', 'real', and 'cosmicic'",
    )
    # model name
    parser.add_argument(
        "-m",
        "--model",
        type=str,
        default=DEFAULT_CBORG_MODEL,
        help="Model name to use for the real bash agent (if applicable)",
    )
    args = parser.parse_args()

    task = args.task
    model_name = args.model
    use_real_agent = task in ("real", "cosmicic")

    if use_real_agent:
        from hepagent.agents.bash import create as create_bash_agent
        from hepagent.agents.textual import AgentAdapter

        cosmicic_prompt = (
            "Your working directory is /pscratch/sd/x/xju/FoundationUniverse/nyx_sim/agent_area/v0."
            "The original cosmicic code is located at "
            "/pscratch/sd/x/xju/FoundationUniverse/nyx_sim/cosmicic. "
            "1. Make a copy of cosmicic code to your working directory, compile it. "
            "If the compilation is successful, copy the `init` to your working directory."
            "Note that if the platform is Perlmuttter, you need to load these module first: "
            "- cray-fftw - cray-hdf5-parallel. "
            "2. Create a parameter file named `input.par` for a cosmological simulation with Nyx. "
            "The cosmological parameters are: "
            "- hubble = 0.675; - Omega_m = 0.31; - Omega_bar = 0.0487; - n_s = 0.96. "
            "And the runtime parameters are: - np = 265; - box_size = 80.0; - seed = 343240149; "
            "- z_in = 200.0; - output_file = output/ics_80mpc_256. "
            "And the transfer_function is located at `cmb.tf`. "
            "3. Create the cosmic initial conditions for Nyx simulation. "
            "You may have to read the `cosmicic/README` file located for more details."
        )
        simple_bash_prompt = (
            "List the files in the current directory and tell me how many there are."
        )
        task_prompt = cosmicic_prompt if task == "cosmicic" else simple_bash_prompt

        # Create the bash agent
        try:
            bash_agent = create_bash_agent()
            app = TextualAgent(model="gpt-4", env={})
            # Wrap the bash agent with our adapter
            app.agent = AgentAdapter(bash_agent, app)
            exit_status, result = app.run_task(task=task_prompt)
            print(f"Agent exited with status: {exit_status}, result: {result}")
        except Exception as e:
            print(f"Error: {e}")
            print("Make sure you have set the CBORG_API_KEY environment variable")
            print("Falling back to DummyAgent...")
            app = TextualAgent(model="gpt-4", env={})
            exit_status, result = app.run_task(task="Demonstrate the Textual Bash Agent UI")
            print(f"Agent exited with status: {exit_status}, result: {result}")
    else:
        print("Using DummyAgent. Use --real or -r to use the actual bash agent.")
        app = TextualAgent(model="gpt-4", env={})
        exit_status, result = app.run_task(task="Demonstrate the Textual Bash Agent UI")
        print(f"Agent exited with status: {exit_status}, result: {result}")
