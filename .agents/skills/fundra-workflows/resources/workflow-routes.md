# Workflow Routes

Use this reference to choose the right Fundra workflow. Confirm current options
with `uv run fundra <group> <command> --help` because commands evolve.

## Top-Level Map

Primary docs:

- `docs/Workflows.md`: full pipeline and current command examples.
- `README.md`: installation, common commands, datasets, and quick starts.
- `docs/data_preprocessing.md`: legacy notes for 4096^3 MPI chunking.
- `docs/visualization.md`: plotting guidance.
- `docs/GPT.md`: GPT prior architecture and launch notes.
- `docs/lya_flux_power_spectrum.md`: AMReX/HDF5/P1D details.

Main pipeline:

1. Nyx plotfiles or raw HDF5 exist.
2. Catalog and ingest runs with `fundra suite`.
3. Convert AMReX plotfiles to HDF5 and compute P1D with shell wrappers.
4. Chunk HDF5 into MPI chunks or legacy patch arrays.
5. Train VQVAE with `fundra train`.
6. Tokenize with `stage=predict`.
7. Evaluate tokenization with `fundra eval tokens`.
8. Train BERT/GPT prior on token `.pt` files or DDPM on embedding `.pt` files.
9. Sample, reconstruct, compare, and plot.

## Suite Catalog

Use the catalog as the source of truth for run IDs, artifact paths, split
assignments, and next actions.

Common commands:

```bash
CATALOG=/global/cfs/cdirs/m3443/data/foundational_universe/catalogs/nyx_suite_v0

uv run fundra suite init --catalog-dir "$CATALOG"
uv run fundra suite plan --catalog-dir "$CATALOG" --suite-dir /path/to/suite --stage 1 --dry-run
uv run fundra suite ingest --catalog-dir "$CATALOG"
uv run fundra suite status --catalog-dir "$CATALOG"
uv run fundra suite propose --catalog-dir "$CATALOG"
uv run fundra suite status --catalog-dir "$CATALOG" --run-id <run_id>
uv run fundra suite export-config --catalog-dir "$CATALOG" --config scripts/post_nyx_config.json
uv run fundra suite check-paths --catalog-dir "$CATALOG"
```

Before deleting plotfiles, refresh the catalog and run a dry run:

```bash
uv run fundra suite ingest --catalog-dir "$CATALOG"
uv run fundra suite delete-plt-files --catalog-dir "$CATALOG" --dry-run
```

Only use `--yes` after the user explicitly approves deletion.

## Post-Nyx Processing

Use `scripts/post_nyx_config.json` as the shared run manifest for conversion,
P1D, histograms, and chunk wrappers.

AMReX plotfile to HDF5 and P1D:

```bash
sbatch scripts/submit_post_nyx.sh -r L40_N2048_z3_s1
```

P1D only for an existing HDF5:

```bash
sbatch scripts/submit_p1d.sh -r L80_N4096_z3_s2_RECO_x0-7-1_v3
```

Histograms:

```bash
salloc -N 4 -q interactive -C cpu -A m3443 -t 04:00:00
./scripts/run_histogram.sh -r L40_N2048_z3_s1
```

For direct MPI histogramization:

```bash
srun -n 64 fundra_histogramize_mpi data.hdf5 \
    -f temperature -r ranges.csv -o histograms --apply-scales
```

Use `--cuts "field_a > value && field_b < value"` for scaled-field selections.

## Data Preparation

Per-field min/max analysis:

```bash
uv run fundra data analyze /path/to/input.hdf5 -o /path/to/cosmo_fields.csv -w 16
```

MPI chunking for L40_N2048:

```bash
bash scripts/submit_chunk_L40_N2048.sh --dry-run -r L40_N2048_z3_s1
salloc -N 4 -q interactive -C cpu -A m3443 -t 04:00:00
./scripts/submit_chunk_L40_N2048.sh -r L40_N2048_z3_s1 -r L40_N2048_z3_s2
```

L80_N4096 chunking:

```bash
./scripts/run_chunk_files.sh --box-size 4096
```

Legacy patch-array preprocessing remains available:

```bash
uv run fundra data preprocess -f src/fundra/configs/resolved/vqvae/v0.6.0.yaml \
    -c datamodule.num_workers=4
```

Prefer suite chunks for current training because `fundra_chunkify_mpi` applies
v2 physics scaling during chunking.

## VQVAE

Create or freeze a config. In the current tree, VQVAE-style training is
configured through `experiment/tokenization_N4096.yaml` and existing resolved
configs under `src/fundra/configs/resolved/vqvae/`; do not assume an
`experiment/vqvae.yaml` file exists.

```bash
uv run fundra config create -f src/fundra/configs/experiment/tokenization_N4096.yaml \
    -o src/fundra/configs/resolved/vqvae/<version>.yaml
```

Train:

```bash
uv run fundra train -f src/fundra/configs/resolved/vqvae/<version>.yaml \
    -c trainer.devices=1 trainer.num_nodes=1
```

SLURM/GPU launch examples often use `scripts/interactive_continous.sh` with
`scripts/configs/*.json`; inspect the nearest versioned JSON before editing.

Tokenize with the trained encoder:

```bash
uv run fundra train -f src/fundra/configs/resolved/vqvae/<version>.yaml \
    -c trainer.devices=1 stage=predict
```

Evaluate tokenization:

```bash
uv run fundra eval tokens -f src/fundra/configs/resolved/vqvae/<version>.yaml \
    --ckpt-file /path/to/best.ckpt -o logs/eval/<version> -n 100 -j 16
```

Reconstruct HDF5:

```bash
uv run fundra eval reconstruct-hdf5 -f <config.yaml> \
    -i input.hdf5 -o reco.hdf5 --ckpt-file best.ckpt
```

For large files, use the MPI script:

```bash
srun -n <N> fundra_vqvae_reco_mpi -f <config.yaml> \
    -c best.ckpt -i input.hdf5 -o reco.hdf5
```

## Prior Models

BERT and GPT consume token `.pt` files written by VQVAE prediction.

```bash
uv run fundra config create -f src/fundra/configs/experiment/bert.yaml \
    -o src/fundra/configs/resolved/bert_pretrain/<version>.yaml
uv run fundra train -f src/fundra/configs/resolved/bert_pretrain/<version>.yaml

uv run fundra config create -f src/fundra/configs/experiment/gpt.yaml \
    -o src/fundra/configs/resolved/gpt/<version>.yaml
uv run fundra train -f src/fundra/configs/resolved/gpt/<version>.yaml
```

Confirm the resolved directory names in `src/fundra/configs/resolved/`; existing
BERT configs live under `bert_pretrain`.

## Latent Diffusion

DDPM consumes VQVAE continuous embeddings `z_e`.

```bash
uv run fundra config create -f src/fundra/configs/experiment/ddpm.yaml \
    -o src/fundra/configs/resolved/ldm/<version>.yaml
uv run fundra train -f src/fundra/configs/resolved/ldm/<version>.yaml \
    -c trainer.devices=1 trainer.num_nodes=1
uv run fundra eval sample -f src/fundra/configs/resolved/ldm/<version>.yaml -b 1 -d cuda
uv run fundra eval compare /path/to/simulation_chunk.npy generated.npz \
    -o logs/comparison/<version> -v <version>
```

## Plotting

Use headless/noninteractive execution.

```bash
uv run fundra plot fields -f src/fundra/configs/resolved/vqvae/<version>.yaml \
    -o logs/plots -n 50 -j 8
uv run fundra plot histograms ref.npz new.npz -o comparison.png --logy
uv run fundra plot p1d ref.txt new.txt -o p1d_comparison.png --summary ratios.csv
```
