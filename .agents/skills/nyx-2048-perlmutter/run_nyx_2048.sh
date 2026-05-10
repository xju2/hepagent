#!/usr/bin/env bash
# Nyx 2048^3 LyA run on NERSC Perlmutter.
# Run this script from a clean working directory inside a tmux session.
set -euo pipefail

TARBALL_SRC="/global/cfs/cdirs/m3443/usr/xju/FoundationUniverse/nyx_sim_pre_compiled/inputs_40Mpcbyh_2048.tar"
SEEDS_LOG="/global/cfs/cdirs/m3443/usr/xju/FoundationUniverse/nyx_sim_pre_compiled/known_seeds.md"
PAR_FILE="input_40Mpcbyh_2048.par"
TF_FILE="inputs_4096_80Mpcbyh_Jacobus_etal00_tk.dat"
QSUB_FILE="run_batch_job_512_GPU.qsub"
NYX_AMR_INPUTS="inputs_2048_40Mpcbyh_LCDM"
RUNDIR="$(pwd)"

# ---------------------------------------------------------------------------
# Step 0: Precondition checks
# ---------------------------------------------------------------------------
echo "[Step 0] Checking preconditions..."

if [ -z "${TMUX:-}" ]; then
    echo "ERROR: Not in a tmux session. Start one first: tmux new -s nyx2048"
    exit 1
fi

if [ "$(ls -A . 2>/dev/null | wc -l)" -ne 0 ]; then
    echo "WARNING: Working directory is not empty: $RUNDIR"
    read -rp "Continue anyway? [y/N]: " CONFIRM
    [[ "$CONFIRM" =~ ^[Yy]$ ]] || exit 1
fi

echo "Run directory: $RUNDIR"

# ---------------------------------------------------------------------------
# Step 1: Stage inputs
# ---------------------------------------------------------------------------
echo "[Step 1] Copying and extracting inputs tarball..."
cp "$TARBALL_SRC" .
tar -xf inputs_40Mpcbyh_2048.tar
echo "Extraction complete."

# ---------------------------------------------------------------------------
# Step 2: Move extracted raw/ contents into run directory
# ---------------------------------------------------------------------------
echo "[Step 2] Moving extracted contents into run directory..."
if [ -d raw ]; then
    mv raw/* .
    rmdir raw
    echo "Moved raw/ contents into $RUNDIR."
else
    echo "No raw/ directory found after extraction; skipping move."
fi

# ---------------------------------------------------------------------------
# Step 3: Edit IC seed
# ---------------------------------------------------------------------------
echo "[Step 3] Editing IC seed in $PAR_FILE..."
echo "  Known prior seeds: 1242346435, 12423464357575"
read -rp "  Enter new IC seed: " IC_SEED

# Replace the first line matching 'seed' (case-insensitive key) with new value.
# Adjust the pattern if the par file uses a different format.
if grep -qi "seed" "$PAR_FILE"; then
    sed -i -E "s/^([[:space:]]*[Ss]eed[[:space:]]*=)[[:space:]]*.*/\1 $IC_SEED/" "$PAR_FILE"
else
    echo "WARNING: No 'seed' key found in $PAR_FILE. Manually verify the seed."
fi

# Record seed in the shared log.
echo "- $(date +%Y-%m-%d): Run in $RUNDIR, seed $IC_SEED" >> "$SEEDS_LOG"
echo "  Seed $IC_SEED written to $SEEDS_LOG"

# ---------------------------------------------------------------------------
# Steps 4 & 5: Interactive CPU allocation + IC generation
# ---------------------------------------------------------------------------
echo "[Step 4-5] Requesting interactive CPU nodes and generating ICs..."
mkdir -p ICFiles_2048

# salloc launches a sub-shell inside the allocation; bash -c runs srun there.
salloc --nodes 4 --qos interactive --time 04:00:00 --constraint cpu --account=m3443 \
    bash -c "srun -N 4 -n 512 --ntasks-per-node=128 \
        ./init '$PAR_FILE' '$TF_FILE' ICFiles_2048/IC_File \
        2>&1 | tee '$RUNDIR/cosmicic_2048.log'"

echo "  IC generation complete. Log: $RUNDIR/cosmicic_2048.log"
echo "  NOTE: CosmicIC auto-generates FileList.txt with wrong relative paths; ignore it."

# ---------------------------------------------------------------------------
# Step 6: Build FileList_2048.txt with absolute paths
# ---------------------------------------------------------------------------
echo "[Step 6] Building FileList_2048.txt..."
ls $PWD/ICFiles_2048/* | sort -V  > FileList_2048.txt

LINE_COUNT=$(wc -l < FileList_2048.txt)
echo "  FileList_2048.txt: $LINE_COUNT lines (expected 512)."
if [ "$LINE_COUNT" -ne 512 ]; then
    echo "  WARNING: Expected 512 IC files, got $LINE_COUNT. Inspect ICFiles_2048/ before continuing."
fi

# ---------------------------------------------------------------------------
# Step 7: Pre-submit check + batch submission
# ---------------------------------------------------------------------------
echo "[Step 7] Checking required files before batch submission..."

if [ ! -f "$NYX_AMR_INPUTS" ]; then
    echo "ERROR: Required file '$NYX_AMR_INPUTS' is missing from the run directory."
    echo "       This file is NOT included in the tarball. Provide it before submitting."
    exit 1
fi

if [ ! -f "$QSUB_FILE" ]; then
    echo "ERROR: Batch script '$QSUB_FILE' not found in run directory."
    exit 1
fi

echo "  Submitting Nyx batch job (128 nodes / 512 GPUs)..."
SUBMIT_OUTPUT=$(sbatch "$QSUB_FILE")
JOB_ID=$(echo "$SUBMIT_OUTPUT" | awk '{print $NF}')
echo "  $SUBMIT_OUTPUT"

# ---------------------------------------------------------------------------
# Step 8: Write progress report
# ---------------------------------------------------------------------------
REPORT="$RUNDIR/submission_report.md"
cat > "$REPORT" << EOF
# Nyx 2048^3 LyA Run — Submission Report

- **Date**: $(date '+%Y-%m-%d %H:%M:%S')
- **Run directory**: $RUNDIR
- **IC seed**: $IC_SEED
- **Slurm job ID**: $JOB_ID
- **Expected runtime**: ~1.23 hours (queue wait not included)
- **Reference results**: /pscratch/sd/n/nataraj2/Nyx/cosmo-suite/modcon-cosmology/NyxRuns/LyA_2048_40Mpcbyh_LCDM

## Commands executed

\`\`\`bash
# IC generation (4 CPU nodes, 512 tasks)
srun -N 4 -n 512 --ntasks-per-node=128 \\
    ./init $PAR_FILE $TF_FILE ICFiles_2048/IC_File

# Batch submission
sbatch $QSUB_FILE
# -> $SUBMIT_OUTPUT
\`\`\`

## Monitoring

\`\`\`bash
squeue -j $JOB_ID
tail -f cosmicic_2048.log
\`\`\`
EOF

echo "[Step 8] Progress report written to: $REPORT"
echo ""
echo "Done. Monitor the job with: squeue -j $JOB_ID"
