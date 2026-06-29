# CLI and Configs

Use this reference when invoking or modifying commands, configs, scripts, or
SLURM wrappers.

## Command Layout

All normal user-facing commands live under the `fundra` CLI in
`src/fundra/cli/`.

Current command groups and notable commands:

- `fundra suite init|plan|ingest|export-config|check-paths|status|propose|delete-plt-files`
- `fundra config create`
- `fundra data analyze|preprocess`
- `fundra train`
- `fundra eval tokens|sample|reconstruct-hdf5|compare`
- `fundra plot fields|histograms|p1d`

Standalone scripts in `[project.scripts]`:

- `fundra_histogramize_mpi = fundra.scripts.histogramize_mpi:main`
- `fundra_chunkify_mpi = fundra.datamodules.universe_mpi:main`
- `fundra_vqvae_reco_mpi = fundra.scripts.vqvae_inference_on_hdf5:main`

Keep MPI scripts standalone because `srun` must launch them as the root
process. Do not move them under `fundra`.

## Adding Commands

Follow existing patterns:

1. Add a Click command or group in `src/fundra/cli/`.
2. Use `@with_config` from `src/fundra/cli/common.py` for commands that accept
   Hydra configs via `-f`.
3. Keep backend logic in `src/fundra/scripts/` or a domain package. It should be
   callable as `main_fn(...)` or an equivalent function with no `argparse` and
   no `if __name__ == "__main__"` frontend.
4. Let `src/fundra/cli/main.py` auto-discover top-level Click `Command` and
   `Group` objects. Avoid duplicate registration by following neighboring
   modules.
5. Add focused tests and run the command's `--help` path.

## Config Patterns

Hydra composition is used by commands decorated with `@with_config`.

Config roots:

- `src/fundra/configs/experiment/`: composable experiment entry points.
- `src/fundra/configs/datamodule/`: data source variants.
- `src/fundra/configs/model/`: model definitions.
- `src/fundra/configs/trainer/`: trainer presets.
- `src/fundra/configs/resolved/`: frozen YAMLs passed to production commands.

Create resolved configs with:

```bash
uv run fundra config create -f src/fundra/configs/experiment/<name>.yaml \
    -o src/fundra/configs/resolved/<family>/<version>.yaml \
    -c key=value other.key=value
```

Use dotlist overrides with `-c` after the config path for `fundra train`,
`fundra data preprocess`, `fundra eval tokens`, `fundra eval sample`, and
`fundra eval reconstruct-hdf5`.

Before copying an old config, inspect the latest neighboring resolved YAML and
update personal paths, cache directories, logs, checkpoints, dataset splits, and
W&B names. Do not assume another user's CFS or scratch path is accessible.

## SLURM Wrappers

Important wrappers:

- `scripts/interactive_continous.sh`: launches training from JSON configs.
- `scripts/submit_training.sh`: batch training helper.
- `scripts/submit_post_nyx.sh`: AMReX plotfile conversion plus P1D.
- `scripts/submit_p1d.sh`: P1D for existing HDF5.
- `scripts/run_histogram.sh`: histogram production.
- `scripts/submit_chunk_L40_N2048.sh`: L40_N2048 chunking.
- `scripts/run_chunk_files.sh`: L80_N4096 chunking.
- `scripts/submit_reco_h5.sh`: VQVAE reconstruction wrapper.
- `scripts/load_post_nyx_config.sh`: shared config loader for wrappers.

Run dry-run modes where available before submitting. For long interactive jobs,
recommend `tmux` and record allocation, run IDs, paths, and expected outputs.

## Data and Memory Discipline

- Use `Path` for filesystem work.
- Stream, chunk, or reduce large arrays; do not use full-array materialization
  for HDF5 volumes.
- For multiprocessing HDF5 work, pass file paths into workers and open files
  inside each worker.
- For MPI I/O, follow existing `fundra.datamodules.universe_mpi`,
  `fundra.scripts.histogramize_mpi`, and `fundra.scripts.vqvae_inference_on_hdf5`
  patterns.
- Keep output markers such as `.done` idempotent when wrappers support resume.

## Useful Checks

```bash
uv run fundra --help
uv run fundra suite --help
uv run fundra data analyze --help
uv run fundra eval reconstruct-hdf5 --help
uv run fundra plot p1d --help
```

Use `rg` to find the actual implementation before changing behavior:

```bash
rg "command\\(" src/fundra/cli
rg "main_fn" src/fundra/scripts
rg "fundra_chunkify_mpi|fundra_histogramize_mpi|fundra_vqvae_reco_mpi" -n
```
