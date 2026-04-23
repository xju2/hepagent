---
name: nyx-2048-perlmutter
description: Reproduce and troubleshoot the Nyx 2048^3 LyA run on NERSC Perlmutter, including IC generation, batch submission, and missing library fixes.
---

# Nyx 2048^3 LyA Run on Perlmutter

Use this skill when the user asks to reproduce, rerun, or debug the Nyx 2048^3 LyA simulation workflow on Perlmutter.

## Source Context
- Source note: `raw/technical/nyx/sim_L2048.md`
- Created: April 15, 2026
- Important update: April 18, 2026 (runtime library fix)

## Preconditions
- You are running on NERSC Perlmutter.
- You have a valid allocation/account `m3443`.
- The prepacked run inputs are available at:
  - `/global/cfs/cdirs/m3443/usr/xju/FoundationUniverse/nyx_sim_pre_compiled/inputs_40Mpcbyh_2048.tar`
- You have permission to submit jobs and run interactive allocations.
- Use `tmux` for long-running interactive sessions to avoid disconnections.

## Workflow
0. Make sure in a tmux session and in a clean working directory.

1. Stage inputs
```bash
cp /global/cfs/cdirs/m3443/usr/xju/FoundationUniverse/nyx_sim_pre_compiled/inputs_40Mpcbyh_2048.tar .
tar -xf inputs_40Mpcbyh_2048.tar
```
Note: the archive is `.tar`, not `.tar.gz`.

2. Move extracted `raw/` contents into your run directory.

3. Edit IC seed in `input_40Mpcbyh_2048.par`.
   Known prior seeds:
   - `1242346435`
   - `12423464357575`
   Write down the seed you use in `/global/cfs/cdirs/m3443/usr/xju/FoundationUniverse/nyx_sim_pre_compiled/known_seeds.md` for future reference.

4. Start interactive CPU allocation:
```bash
salloc --nodes 4 --qos interactive --time 04:00:00 --constraint cpu --account=m3443
```

5. Create the IC output directory and generate IC files with precompiled CosmicIC:
```bash
mkdir ICFiles_2048
srun -N 4 -n 512 --ntasks-per-node=128 ./init input_40Mpcbyh_2048.par inputs_4096_80Mpcbyh_Jacobus_etal00_tk.dat ICFiles_2048/IC_File 2>&1 | tee cosmicic_2048.log
```
Note: CosmicIC also auto-generates a `FileList.txt` with wrong relative paths (`./init/ICFiles_2048/...`). Ignore it.

6. Build `FileList_2048.txt` with full absolute paths for all generated IC files:
```bash
ls ICFiles_2048/ | sort -V | awk '{print "/path/to/runN/ICFiles_2048/" $0}' > FileList_2048.txt
```
Verify it has 512 lines (`wc -l FileList_2048.txt`).

7. Submit Nyx batch run (128 nodes / 512 GPUs):
```bash
sbatch run_batch_job_512_GPU.qsub
```

8. Expected runtime is about 1.23 hours. However, because the job may not start immediately due to queue times, write a progress report including shell command exectued, the submission ID.

## Reference Results Location
- `/pscratch/sd/n/nataraj2/Nyx/cosmo-suite/modcon-cosmology/NyxRuns/LyA_2048_40Mpcbyh_LCDM`

## Known Issues
- `inputs_2048_40Mpcbyh_LCDM` (the Nyx AMR inputs file) is not included in the tarball but is required by `run_batch_job_512_GPU.qsub`. Ensure it is present in the run directory before submitting the batch job.

## Assistant Behavior Guidance
- If jobs fail, inspect logs for missing libs and queue/account/constraint mismatches.
- Prefer minimal, reproducible command sequences and preserve original file names from this workflow.
- Do not copy any files from a prior run directory to keep each run fully independent.
