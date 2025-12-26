# Project
Building a HEP Agent framwork for cosmology simulation and particle physics analysis.
The framework is based on the `openai-agent-framework` and `cborg` model provider.

## Introduction



## Installation

```bash
uv sync
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
