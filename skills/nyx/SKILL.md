---
name: nyx-sim
description: Build and run Cosmology simulation with the Nyx code. Use when analyzing Ly-alpha forest data or simulating large-scale structure formation.
---

# Nyx Cosmological Simulation

You need to ask users to provide the following information one by one before proceeding with the simulation setup:
* working directory for the Nyx simulation. This is where you will set up the simulation, run it, and analyze the outputs. Make sure to confirm the path with the user before proceeding.
* the original cosmicic code location. This is where the code for generating cosmic initial conditions is located. You will need to copy and compile this code to create the initial conditions for the Nyx simulation. Again, confirm the path to the source code with the user before proceeding.
* the cosmological parameters and runtime parameters for the simulation. These parameters will be used to create the parameter file for the Nyx simulation. Confirm the values of these parameters with the user before proceeding.

Save the provided information in the markdown file `MEMORY.md` in the working directory for future reference and use in the simulation setup.


## Use this skill when
 - You need to run cosmological simulations for large-scale structure formation.


## Instructions

Follow these steps closely.
For each step, refer to corresponding markdown files in the `resources` directory for detailed information and guidance.
Write down unexpected errors and critical decisions to the `MEMORY.md` file in the working directory for future reference.

1. Create the transfer function, see `resources/TF.md`.
2. Generate initial conditions, see `resources/IC.md`.


## Additional notes
- Ensure you prompt for approval before running large simulations.
- Stop when confused about simulation parameters or steps and ask for clarification.
