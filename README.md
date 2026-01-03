# Project
Building a HEP Agent framwork for cosmology simulation and particle physics analysis.
The framework is based on the `openai-agent-framework` and `cborg` model provider.

## Introduction


## Installation

```bash
uv sync
```

## Usage

### Interactive Bash Agent with TextualAgent

The project includes `TextualAgent`, an interactive TUI (Terminal User Interface) for running AI agents with real-time display of thinking processes and bash command execution.

#### Quick Start

Run with DummyAgent (demo mode, no API key required):
```bash
python3 scripts/bash_textual.py
```

Run with real bash agent (requires `CBORG_API_KEY` environment variable):
```bash
export CBORG_API_KEY="your-api-key"
python3 scripts/bash_textual.py --real
```

Or use the example script:
```bash
python3 scripts/example_bash_textual.py
```

#### Features

- **Interactive Display**: Real-time visualization of agent thinking and command execution
- **Three Execution Modes**:
  - **YOLO mode** (Press `y` or `Ctrl+Y`): Auto-approve all commands
  - **CONFIRM mode** (Press `c`): Ask for confirmation before each command (default)
  - **HUMAN mode** (Press `u` or `Ctrl+U`): Disable automatic command execution
- **Step Navigation**: Navigate through agent execution steps with `left`/`h` and `right`/`l`
- **Cost Tracking**: Real-time display of API usage costs
- **Keyboard Controls**:
  - `q` or `Ctrl+Q`: Quit
  - `j`/`k` or `↑`/`↓`: Scroll content
  - `0`: Jump to first step
  - `$`: Jump to last step

#### Session Recording

All agent executions are automatically recorded to JSON files in the `sessions/` directory. Each session file contains:
- Task description
- Execution status (success/error)
- Final result
- Model information and cost
- Execution mode (YOLO/CONFIRM/HUMAN)
- Complete message history with timestamps

Session files are named `session_YYYYMMDD_HHMMSS.json` and can be inspected after the agent completes:

```bash
# View the latest session
cat sessions/session_*.json | jq .

# Count messages in a session
cat sessions/session_20260103_120000.json | jq '.messages | length'

# Extract only the task and result
cat sessions/session_20260103_120000.json | jq '{task, status, result, cost}'
```

#### Programmatic Usage

```python
from hepagent.agents.bash import create as create_bash_agent
from scripts.bash_textual import TextualAgent, AgentAdapter

# Create bash agent
bash_agent = create_bash_agent()

# Create TextualAgent app
app = TextualAgent(model="gpt-4", env={})

# Wrap bash agent with adapter
app.agent = AgentAdapter(bash_agent, app)

# Run with a task
exit_status, result = app.run(task="List files in current directory")

# Session is automatically saved to sessions/session_YYYYMMDD_HHMMSS.json
```

### Notes
* Tranfer function. Create a plotting function that compares the generated transfer function to a reference transfer function (e.g., from CAMB or CLASS) to ensure accuracy.


### Prompts.

```text
Your working directory is /pscratch/sd/x/xju/FoundationUniverse/nyx_sim/agent_area/v0. You created an executable `init` that can create cosmic initial conditions that will be used by Nyx code to simulate cosmology.
To run `init`, you need to provide a parameter file named `input.par` in the same directory. Now, ask me anything you need to know in order to generate the `input.par` file for a cosmological simulation with Nyx.

and run `./init input.par cmb.tf output/ics_80mpc_1024`.
```


```text
Your working directory is /pscratch/sd/x/xju/FoundationUniverse/nyx_sim/agent_area/v0. The original cosmicic code is located at /pscratch/sd/x/xju/FoundationUniverse/nyx_sim/cosmicic. Make a copy of cosmicic code to your working directory, compile it. If the compilation is successful, copy the executable `init` to your working directory. Note that if the platform is Perlmuttter, you need to load these module first: - cray-fftw - cray-hdf5-parallel. Finally, create a parameter file named `input.par` for a cosmological simulation with Nyx. The cosmological parameters are: - hubble = 0.675; - Omega_m = 0.31; - Omega_bar = 0.0487; - n_s = 0.96. And the runtime parameters are: - np = 265; - box_size = 80.0; - seed = 343240149; - z_in = 200.0; - output_file = output/ics_80mpc_256. And the transfer_function is located at `cmb.tf`. After that, feel free to run the command to create the cosmic initial conditions for Nyx simulation. You may have to read the `cosmicic/README` file located at your working directory for more details.
```
