# Cosmic Initial Conditions for Nyx

Run the cosmicic code to generate initial conditions for Nyx cosmological simulation in your working directory for the given cosmological and runtime parameters.

## Instructions

1. If not already in your working directory, make a copy of the cosmicic code, and then compile it.
2. If the compilation is successful, copy the executable `init` to your working directory.
3. Create a parameter file named `input.par` based on the default parameters from `cosmicic/input.par`. Modify the cosmological parameters (e.g., Omega_m, Omega_b, h, sigma_8, n_s) and runtime parameters (e.g., box size, number of particles, output redshifts) as needed for your simulation.
4. After that, write a bash script that users can run the `init` in SLURM.

## Additional notes
* Note that if the platform is Perlmutter, you need to load these modules first:
- cray-fftw
- cray-hdf5-parallel

* You may have to read the `cosmicic/README` file located at your working directory for more details.
