---
name: nyx-sim
description: Build and run Cosmology simulation with the Nyx code. Use when analyzing Ly-alpha forest data or simulating large-scale structure formation.
---

# Nyx Cosmological Simulation

You need to ask user's input on the following:
* working directory for the Nyx simulation. This is where you will set up the simulation, run it, and analyze the outputs. Make sure to confirm the path with the user before proceeding.
* the original cosmicic code location. This is where the code for generating cosmic initial conditions is located. You will need to copy and compile this code to create the initial conditions for the Nyx simulation. Again, confirm the path to the source code with the user before proceeding.
* the cosmological parameters and runtime parameters for the simulation. These parameters will be used to create the parameter file for the Nyx simulation. Confirm the values of these parameters with the user before proceeding.


## Use this skill when
 - You need to run cosmological simulations for large-scale structure formation.


## Instructions

Follow these steps closely. For each step, refer to corresponding markdown files in the `resources` directory for detailed information and guidance.

1. Create the transfer function, see `resources/TF.md`.
2. Generate initial conditions, see `resources/ICs.md`.
3. Set up the Nyx simulation parameters, see `resources/NYX-SETUP.md`.
4. Run the Nyx simulation, see `resources/NYX-RUN.md`.
5. Analyze the simulation outputs, see `resources/NYX-ANALYSIS.md`


## Additional notes
- Ensure you prompt for approval of before running large simulations.
- Stop when confused about simulation parameters or steps and ask for clarification.
