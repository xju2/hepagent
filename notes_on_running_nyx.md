# Setup and Execution of Nyx Initial Conditions on Perlmutter (NERSC)

---

## 1. Environment Overview

### System description
All setup and runs were carried out on **Perlmutter**, the CPU–GPU hybrid supercomputer hosted at NERSC.
For this task, only the **CPU nodes** were used, as *cosmicic* is a pure MPI code that performs FFT-based parallel domain decomposition to generate initial density and velocity fields.

Each CPU node on Perlmutter provides:
- 128 AMD Milan cores
- 512 GB of memory
- A high-speed interconnect optimized for MPI workloads

All work is split between the **home directory**, used for source repositories and lightweight files, and the **scratch directory**, used for large outputs and batch runs.

- Home: `/global/homes/d/diego-gh/`
- Scratch: `/pscratch/sd/d/diego-gh/`

---

## 2. Generating the Transfer Function with CLASS

Before running *cosmicic*, we must provide a transfer-function file that defines the relative amplitude of density perturbations as a function of wavenumber \( k \).
This was generated using the **CLASS** Boltzmann code (via its Python interface, `classy`).

### Steps

1. Loaded Python with the `classy` module available.
2. Created a Python script that sets up a Planck-like cosmology and requests the transfer function and power spectrum at redshift \( z = 200 \).

   The key parameters include:
   - \( h = 0.675 \)
   - \( \Omega_m = 0.31 \)
   - \( \Omega_b = 0.0487 \)
   - \( \sigma_8 = 0.83 \)
   - \( n_s = 0.96 \)

   The script below generates the transfer functions for both CDM and baryons and saves them in the format expected by *cosmicic*.

```python
from classy import Class
import numpy as np
import matplotlib.pyplot as plt

params = {
    'h': 0.675,
    'Omega_b': 0.0487,
    'Omega_cdm': 0.31 - 0.0487,
    'n_s': 0.96,
    'sigma8': 0.83,
    'output': 'mPk, dTk',
    'z_pk': '200',
    'z_max_pk': 200,
    'P_k_max_h/Mpc': 75.0,
}

cosmo = Class()
cosmo.set(params)
cosmo.compute()

tr = cosmo.get_transfer(z=200)
k_vals = tr['k (h/Mpc)']
T_cdm  = tr['d_cdm']
T_b    = tr['d_b']

zeros = np.zeros_like(k_vals)
cmb_tf = np.column_stack([k_vals, T_cdm, T_b, zeros, zeros, zeros, zeros])
np.savetxt("cmb.tf", cmb_tf, fmt="%.10e")
```
The above version is buggy because the cosmology units are not consistent with cosmicic's expectations. The new version is the following:
```python
# ==========================================
# CAMB → CosmicIC transfer function generator
# Includes CAMB δ_tot, BBKS, and CosmicIC-style T(k)
# ==========================================

import numpy as np
import matplotlib.pyplot as plt
import camb
from camb import model, initialpower

# --------------------------------------------------
# 1. Cosmological parameters
# --------------------------------------------------
h        = 0.675
Omega_b  = 0.0487
Omega_c  = 0.31 - Omega_b
Omega_m  = Omega_b + Omega_c
n_s      = 0.96
z_ic     = 200.0

# --------------------------------------------------
# 2. Set CAMB parameters
# --------------------------------------------------
pars = camb.CAMBparams()

pars.set_cosmology(
    H0     = 100*h,
    ombh2  = Omega_b*h*h,
    omch2  = Omega_c*h*h,
)

# Primordial spectrum (σ8 is derived from As)
pars.InitPower.set_params(ns=n_s)

pars.set_matter_power(
    redshifts=[z_ic],
    kmax=300.0      # physical Mpc^-1
)

pars.WantTransfer = True
pars.DoLensing = False

pars.Transfer.k_per_logint = 300  # default is ~10

# --------------------------------------------------
# 3. Run CAMB
# --------------------------------------------------
results   = camb.get_results(pars)
transfers = results.get_matter_transfer_data()

# --------------------------------------------------
# 4. Extract k and δ_i(k,z)
# --------------------------------------------------
k = transfers.q   # physical Mpc^-1 units

delta_cdm = transfers.transfer_z('delta_cdm',    z_index=0)
delta_b   = transfers.transfer_z('delta_baryon', z_index=0)
delta_m   = transfers.transfer_z('delta_tot',    z_index=0)

# CAMB already has δ(k→0)=1

# --------------------------------------------------
# 5. Generate CosmicIC 7-column format
# --------------------------------------------------
zeros = np.zeros_like(k)
cmb_tf = np.column_stack([
    k, delta_cdm, delta_b, zeros, zeros, zeros, zeros
])

np.savetxt("cmb_CAMB.tf", cmb_tf, fmt="%.10e")
print(f"Saved cmb_CAMB.tf with {len(k)} rows.")

# --------------------------------------------------
# 6. BBKS transfer function
# --------------------------------------------------
def T_BBKS(k, Omega_m, h):
    q = k / (Omega_m * h)
    return (
        np.log(1 + 2.34*q)/(2.34*q) *
        (1 + 3.89*q + (16.1*q)**2 + (5.46*q)**3 + (6.71*q)**4)**(-0.25)
    )

T_bbks = T_BBKS(k, Omega_m, h)

# --------------------------------------------------
# 7. CosmicIC-style matter transfer
# --------------------------------------------------
f_b = Omega_b / Omega_m
f_c = Omega_c / Omega_m

T_cic = f_b * delta_b + f_c * delta_cdm     # unnormalized
T_cic_norm = T_cic / T_cic[0]               # normalized like CosmicIC would do internally

# --------------------------------------------------
# 8. CAMB normalized
# --------------------------------------------------
T_camb_norm = delta_m / delta_m[0]

# --------------------------------------------------
# 9. Plot all comparisons
# --------------------------------------------------
plt.figure(figsize=(8,6))

plt.loglog(k, T_camb_norm,     label="CAMB (Total) normalized")
plt.loglog(k, T_cic_norm, '--',label="CAMB (Ω_b δ_b + Ω_c δ_cdm) normalized")
plt.loglog(k, T_bbks,   ':',   label="BBKS (analytic)")

plt.xlabel(r"$k\,[\mathrm{Mpc}^{-1}]$")
plt.ylabel("Transfer function")
plt.legend()
plt.tight_layout()
plt.savefig("CAMB_CIC_BBKS_comparison.png", dpi=300)
plt.show()
```
3. The file `cmb.tf` was inspected visually to confirm smooth, monotonic behavior in the transfer functions for both CDM and baryons.
4. The resulting `cmb.tf` file was then copied to the scratch directory where *cosmicic* will run:

```bash
cp cmb.tf /pscratch/sd/d/diego-gh/nyx_initial_conditions/
```

---

## 3. Cloning and Compiling *cosmicic*

The *cosmicic* repository (the custom initial-condition generator) was cloned into the personal repository directory on Perlmutter:

```bash
cd /global/homes/d/diego-gh/repos
git clone <repo_link> cosmicic
cd cosmicic
```

The code is a C/C++/MPI program that relies on FFTW3 and optionally HDF5 for I/O.
Perlmutter provides optimized builds of both through the Cray programming environment.

Before compilation, the following modules were loaded:

```bash
module load PrgEnv-gnu
module load cray-fftw
module load cray-hdf5-parallel
```

These automatically set the appropriate compiler wrappers (`CC` and `cc`) and define environment variables (`FFTW_INC`, `FFTW_LIB`) used by the Makefile.

The build process was straightforward:

```bash
make clean
make -j8
```

Compilation completed successfully, producing the executable `init`.

To verify correct linkage against the MPI-enabled FFTW libraries, I ran:

```bash
ldd init | grep fftw
```

The output confirmed linking against Cray’s optimized FFTW libraries for AMD Milan:

```
libfftw3_mpi.so.mpi31.3 => /opt/cray/pe/lib64/libfftw3_mpi.so.mpi31.3
libfftw3.so.mpi31.3     => /opt/cray/pe/lib64/libfftw3.so.mpi31.3
```

This verified that the build was fully compatible with Perlmutter’s environment.

Finally, the executable was copied to the scratch directory:

```bash
cp init /pscratch/sd/d/diego-gh/nyx_initial_conditions/
```

---

## 4. Preparing the Working Directory

All input and output files for this run were organized under:

```
/pscratch/sd/d/diego-gh/nyx_initial_conditions/
```

The directory structure is as follows:

```
cmb.tf                  ← transfer function from CLASS
input.par               ← cosmological + run parameters
init                    ← compiled cosmicic executable
logs/                   ← batch output logs (stdout, stderr)
output/                 ← generated Nyx-format IC files
run_cosmicic_1024.sbatch← Slurm batch job script
```

Creating the directory and subfolders:

```bash
mkdir -p /pscratch/sd/d/diego-gh/nyx_initial_conditions/{logs,output}
```

This ensures that log files and simulation outputs remain separated and easy to track.

---

## 5. Input Parameter File

The *cosmicic* code reads its configuration from `input.par`.
For this run, I prepared a version that generates a **1024³** test cube (a smaller version of the production 4096³ volume).

**`input.par`**
```
// Run:
np=1024
box_size=80.0
seed=343240149
z_in=200.0

// Cosmology:
hubble=0.675
Omega_m=0.31
Omega_bar=0.0487
Omega_nu=0.0
Omega_r=0.0
Sigma_8=0.83
n_s=0.96
w_de=-1.0
N_nu=3
nu_pairs=4
f_NL=0.0
TFFlag=0

// Code stuff:
PrintFormat=6
```

Here:
- `np=1024` sets the number of particles per side (the grid resolution).
- `TFFlag=0` specifies that an external transfer function (the `cmb.tf` file) should be used.
- `PrintFormat=6` instructs the code to output files in **Nyx-compatible parallel binary format**, which will later be used directly as Nyx initial conditions.

---

## 6. Creating the Batch Job Script

To run the program efficiently on Perlmutter, I created the Slurm submission script `run_cosmicic_1024.sbatch`.

This job uses one CPU node (128 cores), launching 32 MPI ranks (each handling one slab in the x-direction) — consistent with how *cosmicic* internally decomposes the domain.

**`run_cosmicic_1024.sbatch`**
```bash
#!/bin/bash -l
#SBATCH -A m3443
#SBATCH -C cpu
#SBATCH -q regular
#SBATCH -N 1
#SBATCH -t 00:30:00
#SBATCH -J cosmicic-80mpc-1024
#SBATCH -o logs/%x-%j.out
#SBATCH -e logs/%x-%j.err
#SBATCH --mail-type=BEGIN,END,FAIL
#SBATCH --mail-user=dgonzalezhernandez@ucsb.edu

module load PrgEnv-gnu
module load cray-fftw
module load cray-hdf5-parallel
export OMP_NUM_THREADS=1

echo "Starting on $(date) on $(hostname)"
srun -n 32 --ntasks-per-node=32 ./init input.par cmb.tf output/ics_80mpc_1024
echo "Done on $(date)"
```

This script ensures a consistent, reproducible run environment and collects all diagnostic information in the `logs/` directory.

---

## 7. Submitting the Job

The job was submitted with:

```bash
sbatch run_cosmicic_1024.sbatch
```

Slurm returned the job ID:

```
Submitted batch job 44310145
```

Monitoring progress:

```bash
squeue -u $USER
tail -f logs/cosmicic-80mpc-1024-44310145.out
```

Once running, the output is expected to display a summary similar to:

```
Code runs using 32 processors, on 1024 X 1024 X 1024 domain.
FFT DONE!
FFT DONE!
FFT DONE!
Writing output................Output domain: (32, 1024, 1024)
done
```

---

## 8. Expected Performance and Resource Usage

For a 1024³ grid, the computational cost is modest compared to the 4096³ production runs.

| Parameter | Estimate |
|------------|-----------|
| Nodes used | 1 CPU node |
| MPI ranks  | 32 |
| Expected runtime | ~1–3 minutes |
| CPU charge | ~4 core-hours (0.03 node-hours) |

Even a generous 30-minute walltime ensures the job will complete comfortably within the allocation limits.

---

# Setup and Execution of Nyx LyA Simulation (NERSC)

Once the initial conditions are generated, we can move on to running the Nyx simulation. Here is a summary of how to run the Nyx LyA example in Nyx's repository.

>[!warning] This notes are specifically on how to run a 64^3 simulation, as we have not been able to figure out how to debug the "illegal memory access" error when we try higher resolutions. This notes will be updated once that issue is resolved.

>[!warning] Since these instructions deal with a 64^3 simulation, you would have to generate new initial conditions matching this resolution, so the notes above are still valid, but the numbers referring to the 1024^3 resolution should be changed accordingly.


## 1. Cloning and Compiling Nyx

The *Nyx* repository (the custom initial-condition generator) was cloned into the personal scratch repository directory on Perlmutter:

```bash
cd /pscratch/sd/d/diego-gh
git clone https://github.com/AMReX-Astro/Nyx.git
```

Now, the example that we are trying to execute is the "LyA" simulation, which was specifically designed to model the Lyman-alpha forest. Nyx's repository comes with a template that we can modify to run this specific simulation.

To compile the code, we first need to navigate to the directory for this simulation and modify the GNUMakefile. Here is how the GNUMakefile looks like:

```bash
diego-gh@perlmutter:login06:/pscratch/sd/d/diego-gh> cd Nyx/Exec/LyA
diego-gh@perlmutter:login06:/pscratch/sd/d/diego-gh/Nyx/Exec/LyA> cat GNUmakefile

# AMREX_HOME defines the directory in which we will find all the AMReX code
AMREX_HOME ?= ../../subprojects/amrex

# TOP defines the directory in which we will find Source, Exec, etc
TOP = ../..

# compilation options
COMP    = gnu
USE_MPI = TRUE
# Use with Async IO
MPI_THREAD_MULTIPLE = FALSE
USE_OMP = FALSE
USE_CUDA = TRUE

USE_SUNDIALS      = TRUE
USE_FORT_ODE = FALSE
SUNDIALS_ROOT ?= /global/cfs/cdirs/nyx/sundials_shared/sundials/instdir

PROFILE       = FALSE
TRACE_PROFILE = FALSE
COMM_PROFILE  = FALSE
TINY_PROFILE  = TRUE

PRECISION = DOUBLE
USE_SINGLE_PRECISION_PARTICLES = TRUE
DEBUG     = FALSE

# physics

DIM      = 3
USE_HEATCOOL = TRUE
USE_SAVE_REACT = FALSE
USE_AGN = FALSE
ifeq ($(NO_HYDRO),TRUE)
USE_SDC = FALSE
USE_SUNDIALS = FALSE
USE_FUSED = FALSE
else
ifeq ($(USE_HEATCOOL),TRUE)
USE_SDC = TRUE
USE_SUNDIALS = TRUE
ifeq ($(USE_HIP),TRUE)
USE_FUSED ?= $(USE_HIP)
endif
USE_FUSED ?= $(USE_CUDA)
else
USE_SDC = FALSE
USE_SUNDIALS = FALSE
USE_FUSED = FALSE
endif
endif
USE_CONST_SPECIES = TRUE
NEUTRINO_PARTICLES = FALSE
NEUTRINO_DARK_PARTICLES = FALSE

USE_OWN_BCS = FALSE

# Halo finder
BOOST_INLUDE_DIR := ${OLCF_BOOST_ROOT}/include/boost
REEBER = FALSE

Bpack := ./Make.package
Blocs := .

include $(TOP)/Exec/Make.Nyx
```

Importantly, you must make sure that you are pointing to the correct SUNDIALS installation and also that you are using CUDA (to make sure that the code will be running on GPUs). Lastly, you can compile with:

```bash
nice make -f GNUMakefile -j16
```

---
## 2. Preparing the Working Directory

All input and output files for this run are organized inside of:

```bash
/pscratch/sd/d/diego-gh/Nyx/Exec/LyA
```

When executing Nyx, you need to modify the inputs file. Here is an example:

```bash
diego-gh@perlmutter:login06:/pscratch/sd/d/diego-gh/Nyx/Exec/LyA> cat inputs_64
# ------------------  INPUTS TO MAIN PROGRAM  -------------------
max_step = 10000000

nyx.ppm_type         = 1
nyx.use_colglaz      = 0
nyx.corner_coupling  = 1

nyx.strang_split     = 0
nyx.sdc_split        = 1
nyx.add_ext_src      = 0
nyx.heat_cool_type   = 11
#nyx.simd_width       = 8

# Note we now set USE_CONST_SPECIES = TRUE in the GNUmakefile
nyx.h_species=.76
nyx.he_species=.24

nyx.small_dens = 1.e-2
nyx.small_temp = 1.e-2

nyx.do_santa_barbara = 1
nyx.init_sb_vels     = 1
gravity.ml_tol = 1.e-10
gravity.sl_tol = 1.e-10
gravity.mlmg_agglomeration=1
gravity.mlmg_consolidation=1
nyx.reuse_mlpoisson = 1

nyx.initial_z = 200.0
nyx.final_z = 2.0

#File written during the run: nstep | time | dt | redshift | a
amr.data_log = runlog
#amr.grid_log = grdlog

#This is how we restart from a checkpoint and write an ascii particle file
#Leave this commented out in cvs version
#amr.restart = chk00100
#max_step = 4
#particles.particle_output_file = particle_output

gravity.no_sync      = 1
gravity.no_composite = 1

# PROBLEM SIZE & GEOMETRY
geometry.is_periodic =  1     1     1
geometry.coord_sys   =  0

geometry.prob_lo     =  0     0     0

#Domain size in Mpc
geometry.prob_hi     =  80.0  80.0  80.0

amr.n_cell           =  64  64  64
amr.max_grid_size    =  128
fabarray.mfiter_tile_size = 1024000 8 8
#fabarray.mfiter_tile_size = 1024000 128 128

# >>>>>>>>>>>>>  BC FLAGS <<<<<<<<<<<<<<<<
# 0 = Interior           3 = Symmetry
# 1 = Inflow             4 = SlipWall
# 2 = Outflow
# >>>>>>>>>>>>>  BC FLAGS <<<<<<<<<<<<<<<<
nyx.lo_bc       =  0   0   0
nyx.hi_bc       =  0   0   0

# WHICH PHYSICS
nyx.do_hydro = 1
nyx.do_grav  = 1

# COSMOLOGY
nyx.comoving_OmM = 0.31
nyx.comoving_OmB = 0.0487
nyx.comoving_h   = 0.675

# UVB and reionization
nyx.inhomo_reion     = 0
nyx.inhomo_zhi_file  = "zhi.bin"
nyx.inhomo_grid      = 512
nyx.uvb_rates_file   = "../TREECOOL_middle"
nyx.uvb_density_A    = 1.0
nyx.uvb_density_B    = 0.0
nyx.reionization_zHI_flash   = -1.0
nyx.reionization_zHeII_flash = -1.0
nyx.reionization_T_zHI       = 2.0e4
nyx.reionization_T_zHeII     = 1.5e4

# PARTICLES
nyx.do_dm_particles = 1

# >>>>>>>>>>>>>  PARTICLE INIT OPTIONS <<<<<<<<<<<<<<<<
#  "AsciiFile"        "Random"      "Cosmological"
# >>>>>>>>>>>>>  PARTICLE INIT OPTIONS <<<<<<<<<<<<<<<<
nyx.particle_init_type = BinaryMetaFile
nyx.binary_particle_file = /pscratch/sd/d/diego gh/nyx_initial_conditions_64/FileList_64.txt
amr.nreaders = 4
particles.nparts_per_read = 262144

# TIME STEP CONTROL
nyx.relative_max_change_a = 0.01    # max change in scale factor
particles.cfl             = 0.5     # 'cfl' for particles
nyx.cfl                   = 0.5     # cfl number for hyperbolic system
nyx.init_shrink           = 1.0     # scale back initial timestep
nyx.change_max            = 2.0     # factor by which timestep can change
nyx.dt_cutoff             = 5.e-20  # level 0 timestep below which we halt

# DIAGNOSTICS & VERBOSITY
nyx.sum_interval      = -1      # timesteps between computing mass
nyx.v                 = 1       # verbosity in Nyx.cpp
gravity.v             = 1       # verbosity in Gravity.cpp
amr.v                 = 1       # verbosity in Amr.cpp
mg.v                  = 1       # verbosity in Amr.cpp
particles.v           = 2       # verbosity in Particle class

# REFINEMENT / REGRIDDING
amr.max_level          = 0        # maximum level number allowed
#amr.ref_ratio          = 2 2 2 2
#amr.regrid_int         = 4 4 4 4
#amr.n_error_buf        = 0 0 0 8
#amr.refine_grid_layout = 1
amr.regrid_on_restart  = 1
#amr.blocking_factor    = 32
#amr.nosub              = 1

amr.refinement_indicators = density
amr.density.value_greater = 3.5e9
amr.density.field_name = denvol

# CHECKPOINT FILES
amr.checkpoint_files_output = 1
amr.check_file        = chk
amr.check_int         = 100
amr.checkpoint_nfiles = 64

# PLOTFILES
amr.plot_files_output = 1
amr.plot_file       = plt
amr.plot_int        = -1
nyx.plot_z_values   = 7.0 6.0 5.0 4.0 3.0 2.0

amr.plot_vars        = density xmom ymom zmom rho_e Temp phi_grav
amr.derive_plot_vars = particle_mass_density particle_count

# Halo Finder
#nyx.analysis_z_values = 150 10 5 4 3 2
reeber.halo_int = 1
reeber.negate = 1
reeber.halo_density_vars = density particle_mass_density
reeber.halo_extrema_threshold = 20
reeber.halo_component_threshold = 10
#nyx.mass_halo_min = 1.e11
#nyx.mass_seed = 1.e6

# ANALYSIS in situ
nyx.analysis_z_values   = 7.0 6.0 5.0 4.0 3.0 2.0
insitu.int              = 100
insitu.start            = 0
insitu.reeber_int       = 100

# SENSEI in situ
sensei.enabled = 0
#sensei.config = write_vtk.xml
sensei.config = render_iso_catalyst_3d.xml
sensei.frequency = 2

#PROBIN FILENAME
amr.probin_file = ""

# GPU SPECIFIC ARGUMENTS
#amrex.the_arena_is_managed=0
nyx.minimize_memory=0
amrex.abort_on_out_of_gpu_memory=1
nyx.sundials_alloc_type=5
amrex.max_gpu_streams=8

nyx.hydro_tile_size=64 64 64
nyx.sundials_tile_size=64 64 64
nyx.sundials_use_tiling=1
nyx.sundials_alloc_type=5

amrex.max_gpu_streams=8
nyx.sundials_reltol=1e-6
nyx.sundials_abstol=1e-6
DistributionMapping.strategy=ROUNDROBIN

#nyx.load_balance_start_z=10.0
nyx.load_balance_start_z=0
nyx.load_balance_int=100
nyx.load_balance_wgt_strategy=1
nyx.load_balance_strategy=KNAPSACK
```

Here are some important details:

- Match the initial redshift to the one at which the initial conditions where generated
- Match the simulation box size and resolution to those used for the initial conditions
- Match the cosmological parameters to those used for the initial conditions
- Make sure that the initial conditions are correctly being set to 'BinaryMetaFile' type and is pointing to the FileList.txt generated by the cosmicic code

  >[!warning] The FileList.txt contains the names of all the files created by cosmicic that are loaded as the initial conditions in Nyx. However, cosmicic adds an arbitrary relative path for this files, which might not be correct with however you decide to organize each run. Instead, I strongly suggest to modify FileList.txt to have the absolute path for all the files.

---
## 3. Creating the Batch Job Script and Submitting the Job

Now we need a job script for the simulation run. For this example, this is the job script that was used:

```bash
diego-gh@perlmutter:login06:/pscratch/sd/d/diego-gh/Nyx/Exec/LyA> cat run_nyx_gpu_64_perlmutter_new.sbatch
#!/bin/bash -l
#SBATCH -N 2                       # 2 node
#SBATCH -C gpu                     # A100 GPU nodes
#SBATCH -G 8                       # 4 GPUs per node × 2 node
#SBATCH -q debug                   # short test queue (30 min limit)
#SBATCH -J NyxGPU64                # job name
#SBATCH --mail-user=dgonzalezhernandez@ucsb.edu
#SBATCH --mail-type=ALL
#SBATCH -A m3443                   # your project allocation
#SBATCH -t 0:05:00                 # 5 minutes wall time, enough for this sim
#SBATCH -o logs/%x-%j.out          # standard output log
#SBATCH -e logs/%x-%j.err          # standard error log

# ---------------- OpenMP settings ----------------
export OMP_NUM_THREADS=1
export OMP_PLACES=threads
export OMP_PROC_BIND=spread

# ---------------- Environment ----------------
module load PrgEnv-gnu/8.5.0
module load craype-accel-nvidia80
module load cudatoolkit/12.4
module load cray-fftw
module load cray-hdf5-parallel
module load cray-mpich/8.1.30

# Recommended Perlmutter fix for CUDA IPC errors:
export MPICH_GPU_SUPPORT_ENABLED=1
export MPICH_GPU_IPC_ENABLED=0     # prevent cuIpcOpenMemHandle issues

# ---------------- Paths ----------------
EXE=/pscratch/sd/d/diego-gh/Nyx/Exec/LyA/Nyx3d.gnu.TPROF.MPI.CUDA.ex
INPUTS=/pscratch/sd/d/diego-gh/Nyx/Exec/LyA/inputs_64
SDIR=/pscratch/sd/d/diego-gh/Nyx/Exec/LyA/nyx_64_test_${SLURM_JOB_ID}

# Create run and log directories
mkdir -p "$SDIR"
mkdir -p logs
cp -f "$INPUTS" "$SDIR/"

cd "$SDIR"
echo "Starting run on $(date) with $SLURM_JOB_NUM_NODES nodes."
echo "Executable: $EXE"
echo "Inputs: $SDIR/inputs"
echo "--------------------------------------------------"

# --- GPU sanity check (verify GPU visibility and binding) ---
echo "Performing GPU visibility sanity check..."
srun -n 8 -c 8 --cpu_bind=cores -G 8 --gpu-bind=single:1 \
     bash -c 'echo "Node: $(hostname) | GPU(s): $(nvidia-smi --query-gpu=name --format=csv,noheader | head -n 1)"' \
     | sort
echo "GPU assignment verified — starting simulation."
echo "--------------------------------------------------"

# ---------------- Run ----------------
srun -n 8 -c 8 --cpu_bind=cores -G 8 --gpu-bind=single:1 \
     "$EXE" "$SDIR/inputs_64"

echo "Finished on $(date)"
```

Lastly, you can simply submit the job with:

```bash
sbatch run_nyx_gpu_64_perlmutter_new.sbatch
```

---
## 4. Checking outputs

Once the code finishes execution, it will have produced a series of checkpoints and output files. As further verification, you can also see the start and the end of the log file that was created. Here is what the start looks like for this example:

```bash
tarting run on Wed 05 Nov 2025 04:01:05 PM PST with 2 nodes.
Executable: /pscratch/sd/d/diego-gh/Nyx/Exec/LyA/Nyx3d.gnu.TPROF.MPI.CUDA.ex
Inputs: /pscratch/sd/d/diego-gh/Nyx/Exec/LyA/nyx_64_test_44910962/inputs
--------------------------------------------------
Performing GPU visibility sanity check...
Node: nid001621 | GPU(s): NVIDIA A100-SXM4-40GB
Node: nid001621 | GPU(s): NVIDIA A100-SXM4-40GB
Node: nid001621 | GPU(s): NVIDIA A100-SXM4-40GB
Node: nid001621 | GPU(s): NVIDIA A100-SXM4-40GB
Node: nid001624 | GPU(s): NVIDIA A100-SXM4-40GB
Node: nid001624 | GPU(s): NVIDIA A100-SXM4-40GB
Node: nid001624 | GPU(s): NVIDIA A100-SXM4-40GB
Node: nid001624 | GPU(s): NVIDIA A100-SXM4-40GB
GPU assignment verified — starting simulation.
--------------------------------------------------
Initializing AMReX (25.10-31-g9a2bff00537e)...
MPI initialized with 8 MPI processes
MPI initialized with thread support level 0
Initializing CUDA...
CUDA initialized with 8 devices.
Initializing SUNDIALS with 1 threads...
SUNDIALS initialized.
AMReX (25.10-31-g9a2bff00537e) initialized
Successfully read inputs file ...

AMReX git describe: 25.10-31-g9a2bff005

Nyx git describe:   21.02.1-246-g51a5f8e5-dirty
Integrating heating/cooling method with the following method: Vectorized CVODE
Nyx::setting species concentrations to 0.76 and 0.24 in the hydro and in the EOS
0 Species:
Successfully read inputs file ...
Getting Gconst from nyx_constants: 4.300927161e-09
Using -5.404704468e-08 for 4 pi G in Gravity.cpp
Initializing the data at level 0
... setting particle_initrandom_mass to 3317482451 by dividing 8.696581196e+14 by 262144

Initializing DM particles from meta file"/pscratch/sd/d/diego-gh/nyx_initial_conditions_64/FileList_64.txt" ...

InitFromBinaryMetaFile: processing file: /pscratch/sd/d/diego-gh/nyx_initial_conditions_64/output/ics_80mpc_64.nyx.0
Reading with 8 readers
Redistributing after every 262144 particles for each reader

Total number of particles: 65536
ParticleContainer spread across MPI nodes - bytes (num particles): [Min: 0 (0), Max: 1128960 (28224), Total: 2621440 (65536)]
InitFromBinaryFile() time: 0.351069316
InitFromBinaryMetaFile: processing file: /pscratch/sd/d/diego-gh/nyx_initial_conditions_64/output/ics_80mpc_64.nyx.1
Reading with 8 readers
Redistributing after every 262144 particles for each reader
```

And here are some of the last lines:

```bash
END REGION R::Nyx::coarseTimeStep
Unused ParmParse Variables:
  [TOP]::amr.nreaders(nvals = 1)  :: [4]
  [TOP]::amr.probin_file(nvals = 1)  :: []
  [TOP]::nyx.corner_coupling(nvals = 1)  :: [1]
  [TOP]::nyx.inhomo_grid(nvals = 1)  :: [512]
  [TOP]::nyx.inhomo_zhi_file(nvals = 1)  :: [zhi.bin]
  [TOP]::nyx.reionization_T_zHI(nvals = 1)  :: [2.0e4]
  [TOP]::nyx.reionization_T_zHeII(nvals = 1)  :: [1.5e4]
  [TOP]::nyx.reionization_zHI_flash(nvals = 1)  :: [-1.0]
  [TOP]::nyx.reionization_zHeII_flash(nvals = 1)  :: [-1.0]
  [TOP]::nyx.use_colglaz(nvals = 1)  :: [0]
  [TOP]::nyx.uvb_density_A(nvals = 1)  :: [1.0]
  [TOP]::nyx.uvb_density_B(nvals = 1)  :: [0.0]
  [TOP]::reeber.halo_component_threshold(nvals = 1)  :: [10]
  [TOP]::reeber.halo_density_vars(nvals = 2)  :: [density, particle_mass_density]
  [TOP]::reeber.halo_extrema_threshold(nvals = 1)  :: [20]
  [TOP]::reeber.halo_int(nvals = 1)  :: [1]
  [TOP]::reeber.negate(nvals = 1)  :: [1]
  [TOP]::sensei.config(nvals = 1)  :: [render_iso_catalyst_3d.xml]
  [TOP]::sensei.enabled(nvals = 1)  :: [0]
  [TOP]::sensei.frequency(nvals = 1)  :: [2]

Device Memory Usage:
-----------------------------------------------------------------------------------------------------------------------------------------------------
Name                                                          Nalloc    Nfree  AvgMem min  AvgMem avg  AvgMem max  MaxMem min  MaxMem avg  MaxMem max
-----------------------------------------------------------------------------------------------------------------------------------------------------
The_Arena::Initialize()                                            8        8     397 KiB     417 KiB     437 KiB      29 GiB      29 GiB      29 GiB
Nyx::sdc_hydro()                                               14714    14714       0   B    6913 KiB      54 MiB       0   B    8461 KiB      66 MiB
Nyx::advance_hydro_plus_particles()                            10515    10515       0   B    3651 KiB      28 MiB       0   B    5185 KiB      40 MiB
ResizeRandomSeed                                                   8        8      40 MiB      40 MiB      40 MiB      40 MiB      40 MiB      40 MiB
Nyx::reactions_cvsetup                                         39938    39938       0   B    3621 KiB      28 MiB       0   B    4608 KiB      36 MiB
StateData::define()                                               45       45    8136   B    3650 KiB      28 MiB    3991 KiB    7641 KiB      32 MiB
Nyx::reactions_alloc                                           27326    27326       0   B    2418 KiB      18 MiB       0   B    3072 KiB      24 MiB
AmrLevel::writePlotFile()                                          6        6       0   B    1646   B      12 KiB       0   B    2304 KiB      18 MiB
FillPatchIterator::Initialize                                  16860    16860       0   B    4446   B      34 KiB     768 KiB    2859 KiB      17 MiB
solve_for_old_phi                                               6306     6306       0   B     853 KiB    6828 KiB       0   B    1710 KiB      13 MiB
Nyx::Nyx(Amr)                                                     54       54    3538   B    3577   B    3853   B    1896 KiB    3369 KiB      13 MiB
MLMG::prepareForSolve()                                       100983   100983     276   B     202 KiB    1592 KiB    1537 KiB    2838 KiB      11 MiB
ParticleContainer::WriteParticles()                              216      216       0   B     138   B    1101   B     415 KiB    2370 KiB      10 MiB
amrex::unpackRemotes                                              25       25       0   B    1277 KiB       9 MiB     415 KiB    2370 KiB      10 MiB
amrex::communicateParticlesStart                               16889    16889       0   B     438   B    1484   B     226 KiB    1489 KiB    8724 KiB
Gravity::multilevel_solve_for_old_phi()                         6306     6306       0   B     852 KiB    6823 KiB       0   B     855 KiB    6840 KiB
Nyx::derive(mf)                                                   18       18       0   B       9   B      74   B       0   B     512 KiB    4096 KiB
Gravity::actual_multilevel_solve()                              4220     4220      93   B    5611   B      43 KiB     512 KiB     960 KiB    4096 KiB
ParticleContainer<NSR, NSI, NAR, NAI>::InitFromBinaryFile()      156      156     179   B     605   B    1446   B     811 KiB    1873 KiB    3031 KiB
Nyx::init(old)                                                     1        1       0   B       0   B       1   B       0   B     364 KiB    2916 KiB
Nyx::reset_internal_energy_nostore()                            4204     4204       0   B     246   B    1968   B       0   B     364 KiB    2916 KiB
amrex::packBuffer                                                 40       40     287   B     414   B     501   B     415 KiB    1280 KiB    2894 KiB
Gravity::AddParticlesToRhs()                                    2102     2102       0   B    1376   B      10 KiB       0   B     334 KiB    2679 KiB
ParticleContainer::AssignDensity()                              2140     2140      16   B    1195   B    9442   B    1458 KiB    1582 KiB    2456 KiB
amrex::ParticleToMesh                                              6        6       0   B       2   B      18   B       0   B     256 KiB    2048 KiB
Gravity::solve_for_new_phi()                                    2102     2102       0   B      34 KiB     278 KiB       0   B     256 KiB    2048 KiB
Redistribute_partition                                          4988     4988     190   B     270   B     429   B     310 KiB     775 KiB    1736 KiB
Nyx::compute_gas_fractions()::ReduceOpsOnDevice                16824    16824     199   B     237   B     487   B    1728 KiB    1728 KiB    1728 KiB
amrex::unpackBuffer                                                1        1       0   B       0   B       6   B       0   B     189 KiB    1515 KiB
Nyx::compute_rho_temp()::ReduceOpsOnDevice                     16824    16824     174   B     206   B     409   B    1512 KiB    1512 KiB    1512 KiB
WriteBinaryParticleData()                                        500      500       3   B     255   B    1993   B     257 KiB     453 KiB    1240 KiB
ParticleContainer::RedistributeGPU()                              32       32     385   B     385   B     386   B     972 KiB     972 KiB     972 KiB
Gravity::multilevel_solve_for_new_phi()                           24       24     320   B     320   B     320   B     948 KiB     948 KiB     948 KiB
Nyx::post_init()                                                  16       16       2   B       2   B       2   B     768 KiB     768 KiB     768 KiB
FillBoundary_nowait()                                         177262   177262       2   B    3242   B      23 KiB      11 KiB      93 KiB     650 KiB
BndryData::define()                                              108      108      49   B      57 KiB     459 KiB     136 KiB     194 KiB     597 KiB
MLCellLinOp::defineAuxData()                                     312      312      32   B      47 KiB     359 KiB      90 KiB     140 KiB     450 KiB
VisMF::Write(FabArray)                                           354      354       0   B       1   B      10   B     432 KiB     432 KiB     432 KiB
ParticleCopyPlan::build                                       101303   101303      32   B      42   B      51   B      42 KiB     128 KiB     290 KiB
AmrLevel::derive()                                                32       32       0   B       0   B       1   B     256 KiB     256 KiB     256 KiB
Nyx::read_params()                                                 8        0     250 KiB     250 KiB     250 KiB     250 KiB     250 KiB     250 KiB
amrex::Dot()                                                  115808   115808       0   B     179   B    1432   B       0   B      27 KiB     216 KiB
FabArray::norminf()                                           240446   240446     203   B     385   B    1117   B     216 KiB     216 KiB     216 KiB
MultiFab::min()                                                16818    16818      21   B      57   B     302   B     216 KiB     216 KiB     216 KiB
FabArray::sum()                                               148682   148682     144   B     193   B     347   B     216 KiB     216 KiB     216 KiB
Nyx::est_time_step()                                           16832    16832      21   B      27   B      59   B     216 KiB     216 KiB     216 KiB
NyxParticleContainer<NSR,NSI,NAR,NAI>::estTimestep(lev)        16824    16824      20   B      26   B      73   B     216 KiB     216 KiB     216 KiB
Gravity::set_mass_offset()                                         8        8       0   B       0   B       0   B     216 KiB     216 KiB     216 KiB
vol_weight_sum                                                 33688    33688     124   B     129 KiB     172 KiB     216 KiB     216 KiB     216 KiB
MLMG::addInterpCorrection()                                    11183    11183       0   B     314   B    2512   B    4096   B    7680   B      32 KiB
amrex::average_down                                            11183    11183       0   B       7   B      61   B    4096   B    7680   B      32 KiB
FillBoundary_finish()                                             80       80       2   B       2   B       2   B      11 KiB      11 KiB      11 KiB
FabArray::ParallelCopy_nowait()                                30504    30504       0   B       2   B      13   B     512   B    1112   B    4864   B
MLCGSolver::bicgstab                                           55415    55415       0   B       3   B      29   B       0   B     152   B    1216   B
MLCellLinOp::applyBC()                                       4747976  4747976       0   B       4   B      20   B    1184   B    1184   B    1184   B
MLCellLinOp::prepareForSolve()                                 25244    25244       0   B       0   B       1   B     976   B     976   B     976   B
MLCellLinOp::defineBC()                                           26       26       0   B      71   B     383   B     192   B     312   B     960   B
DenseBins<T>::buildGPU                                            56       56     191   B     191   B     191   B     960   B     960   B     960   B
FabArray::setVal(val, thecmd, scomp, ncomp)                      210      210       0   B       0   B       0   B     912   B     912   B     912   B
FabArray::ParallelCopy_finish()                                13534    13534       0   B       2   B      16   B     128   B     288   B     768   B
Nyx::reactions_cvode                                           15067    15067       0   B      11   B      90   B       0   B      18   B     144   B
ParticleBufferMap::define                                         24       24      79   B      79   B      79   B      80   B      80   B      80   B
MLMG::actualBottomSolve()                                      11083    11083       0   B       0   B       1   B       0   B       8   B      64   B
-----------------------------------------------------------------------------------------------------------------------------------------------------

Async Memory Usage:
--------------------------------------------------------------------------------------------------------------------
Name                           Nalloc  Nfree  AvgMem min  AvgMem avg  AvgMem max  MaxMem min  MaxMem avg  MaxMem max
--------------------------------------------------------------------------------------------------------------------
Nyx::umeth()                    79876  79876       0   B      35 KiB     283 KiB       0   B      65 MiB     523 MiB
Nyx::advance_hydro_pc_umdrv()   10510  10510       0   B      11 KiB      91 KiB       0   B    9054 KiB      70 MiB
Nyx::umdrv()                    10510  10510       0   B    7163   B      55 KiB       0   B    6171 KiB      48 MiB
--------------------------------------------------------------------------------------------------------------------

Managed Memory Usage:
----------------------------------------------------------------------------------------------------------------------
Name                             Nalloc  Nfree  AvgMem min  AvgMem avg  AvgMem max  MaxMem min  MaxMem avg  MaxMem max
----------------------------------------------------------------------------------------------------------------------
The_Managed_Arena::Initialize()       8      8       1   B       3   B       7   B    8192 KiB    8192 KiB    8192 KiB
----------------------------------------------------------------------------------------------------------------------

Pinned Memory Usage:
---------------------------------------------------------------------------------------------------------------------------------------------------
Name                                                         Nalloc   Nfree  AvgMem min  AvgMem avg  AvgMem max  MaxMem min  MaxMem avg  MaxMem max
---------------------------------------------------------------------------------------------------------------------------------------------------
VisMF::Write(FabArray)                                          480     480       1   B    4395   B      34 KiB    1842 KiB    3916 KiB      18 MiB
The_Pinned_Arena::Initialize()                                    8       8     182   B     184   B     186   B    8192 KiB    8192 KiB    8192 KiB
ParticleContainer<NSR, NSI, NAR, NAI>::InitFromBinaryFile()    2432    2432     451   B     537   B     567   B     356 KiB     356 KiB     356 KiB
FillBoundary_nowait()                                         67802   67802       2   B    3200   B      23 KiB      11 KiB      16 KiB      32 KiB
FillBoundary_finish()                                            80      80       2   B       2   B       2   B      11 KiB      11 KiB      11 KiB
FabArray::ParallelCopy_nowait()                               30504   30504       0   B       2   B      13   B     512   B    1112   B    4864   B
MLCellLinOp::applyBC()                                       560862  560862       0   B       3   B      19   B    1168   B    1168   B    1168   B
MLCellLinOp::prepareForSolve()                                25244   25244       0   B       0   B       1   B     976   B     976   B     976   B
FabArray::setVal(val, thecmd, scomp, ncomp)                     210     210       0   B       0   B       0   B     912   B     912   B     912   B
FabArray::ParallelCopy_finish()                               13534   13534       0   B       2   B      16   B     128   B     288   B     768   B
ParticleContainer::RedistributeGPU()                          16928   16928     223   B     223   B     224   B     432   B     432   B     432   B
FabArray::norminf()                                          240446  240446       0   B      34   B     272   B     144   B     192   B     400   B
amrex::Dot()                                                 115808  115808       0   B       0   B       6   B       0   B      50   B     400   B
FabArray::sum()                                              148682  148682       0   B       2   B      17   B     144   B     160   B     272   B
MultiFab::min()                                               16818   16818       0   B      31   B     255   B      16   B      48   B     272   B
Nyx::est_time_step()                                          16832   16832       0   B       0   B       0   B     144   B     144   B     144   B
Nyx::reactions_cvode                                          15067   15067       0   B      11   B      90   B       0   B      18   B     144   B
ParticleCopyPlan::buildMPIStart                                  25      25       0   B       0   B       0   B      80   B     102   B     112   B
MLCellLinOp::setLevelBC()                                     25244   25244       0   B       0   B       0   B      96   B      96   B      96   B
Nyx::compute_rho_temp()::ReduceOpsOnDevice                    16824   16824       0   B       0   B       0   B      64   B      64   B      64   B
Nyx::compute_gas_fractions()::ReduceOpsOnDevice               16824   16824       0   B       0   B       0   B      64   B      64   B      64   B
ParticleCopyPlan::build                                       16864   16864       0   B       0   B       0   B      48   B      48   B      48   B
Nyx::reactions_cvsetup                                         2102    2102       0   B       1   B      12   B       0   B       2   B      16   B
NyxParticleContainer<NSR,NSI,NAR,NAI>::estTimestep(lev)       16824   16824       0   B       0   B       0   B      16   B      16   B      16   B
ParticleContainer::WriteParticles()                              36      36       0   B       0   B       0   B      16   B      16   B      16   B
Redistribute_partition                                         2256    2256       0   B       0   B       0   B      16   B      16   B      16   B
WriteBinaryParticleData()                                       464     464       0   B       0   B       0   B      16   B      16   B      16   B
Gravity::set_mass_offset()                                        8       8       0   B       0   B       0   B      16   B      16   B      16   B
vol_weight_sum                                                33688   33688       0   B       9   B      12   B      16   B      16   B      16   B
---------------------------------------------------------------------------------------------------------------------------------------------------

Comms Memory Usage:
----------------------------------------------------------------------------------------------------------------------
Name                             Nalloc  Nfree  AvgMem min  AvgMem avg  AvgMem max  MaxMem min  MaxMem avg  MaxMem max
----------------------------------------------------------------------------------------------------------------------
FabArray::ParallelCopy_nowait()   44484  44484       2   B     423   B    2605   B    1536 KiB    2688 KiB      10 MiB
The_Comms_Arena::Initialize()         8      8       7   B      12   B      16   B    8192 KiB    8192 KiB    8192 KiB
FillBoundary_nowait()              2864   2864      10   B      11   B      12   B     868 KiB     868 KiB     868 KiB
----------------------------------------------------------------------------------------------------------------------

Cpu Memory Usage:
-------------------------------------------------------------------------------------------------------------
Name                    Nalloc  Nfree  AvgMem min  AvgMem avg  AvgMem max  MaxMem min  MaxMem avg  MaxMem max
-------------------------------------------------------------------------------------------------------------
Nyx::reactions_cvsetup   37836  37836       0   B    3621 KiB      28 MiB       0   B    4608 KiB      36 MiB
Nyx::reactions_alloc     25224  25224       0   B    2418 KiB      18 MiB       0   B    3072 KiB      24 MiB
-------------------------------------------------------------------------------------------------------------

Total GPU global memory (MB) spread across MPI: [40326 ... 40326]
Free  GPU global memory (MB) spread across MPI: [8960 ... 9646]
[The         Arena] max space (MB) allocated spread across MPI: [30244 ... 30244]
[The         Arena] max space (MB) used      spread across MPI: [49 ... 248]
[The Managed Arena] max space (MB) allocated spread across MPI: [8 ... 8]
[The Managed Arena] max space (MB) used      spread across MPI: [0 ... 0]
[The  Pinned Arena] max space (MB) allocated spread across MPI: [8 ... 52]
[The  Pinned Arena] max space (MB) used      spread across MPI: [1 ... 18]
[The   Comms Arena] max space (MB) allocated spread across MPI: [8 ... 18]
[The   Comms Arena] max space (MB) used      spread across MPI: [1 ... 10]
AMReX (25.10-31-g9a2bff00537e) finalized
Finished on Wed 05 Nov 2025 04:04:58 PM PST
```
