# Cosmic Initial Conditions for Nyx

Working directory: `/pscratch/sd/x/xju/FoundationUniverse/nyx_sim/agent_area/v0`
The original cosmicic code is located at `/pscratch/sd/x/xju/FoundationUniverse/nyx_sim/cosmicic`.

## Instructions

1. If not already in your working directory, make a copy of the cosmicic code, and then compile it.
2. If the compilation is successful, copy the executable `init` to your working directory.
3. Finally, create a parameter file named `input.par` for a cosmological simulation with Nyx.
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

After that, feel free to run the command to create the cosmic initial conditions for Nyx simulation.

## Additional notes
* Note that if the platform is Perlmuttter, you need to load these module first:
- cray-fftw
- cray-hdf5-parallel

* You may have to read the `cosmicic/README` file located at your working directory for more details.
