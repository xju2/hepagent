# Development and Validation

Use this reference when changing code or documentation in the Fundra repository.

## Code Style

- Match the existing direct, utility-first style.
- Keep edits scoped to the requested workflow or defect.
- Use `Path` for filesystem work.
- Keep line length compatible with `pyproject.toml` Ruff settings.
- Avoid adding dependencies until checking for existing equivalents.
- Do not move existing commands between CLI groups unless the user explicitly
  asks for a CLI redesign.

## Tests

Choose narrow tests by touched area:

- Catalog/suite: `tests/test_catalog.py`, `tests/test_post_nyx_config_loader.py`.
- Histogram logic: `tests/test_histogram.py`, `tests/test_histogramize_mpi.py`,
  `tests/test_run_histogram_script.py`, `tests/test_compare_histograms_script.py`.
- P1D plotting/comparison: `tests/test_compare_p1d_script.py`.
- Tokenization/eval: `tests/test_eval_tokenization.py`,
  `tests/test_tokenize_3d_data.py`, `tests/test_vqvae3d.py`.
- HDF5 reconstruction: `tests/test_vqvae_inference_on_hdf5.py`.
- Suite chunk datasets: `tests/test_suite_chunk.py`,
  `tests/test_suite_chunk_datamodule.py`,
  `tests/test_universe_mpi_dataset.py`.
- Legacy patch datamodule: `tests/test_universe_patch_datamodule.py`.
- Training utilities/models: `tests/test_log_hyperparameters.py`,
  `tests/test_net_mlp.py`.

Run examples:

```bash
uv run ruff check src/fundra/cli src/fundra/scripts tests/test_target.py
uv run pytest tests/test_target.py
```

If a command or wrapper changes, also run:

```bash
uv run fundra <group> <command> --help
```

For shell wrappers, run their dry-run/help path if present. Avoid submitting
SLURM jobs unless the user explicitly asks.

## Documentation Updates

If a doc note requested the feature, update that doc with a brief completion
summary and keep it task-oriented.

Primary docs to update:

- `docs/Workflows.md` for workflow and command changes.
- `README.md` for quick-start or common operational changes.
- `docs/data_preprocessing.md` for chunking/preprocessing changes.
- `docs/visualization.md` for plotting behavior.
- `docs/GPT.md` for GPT prior changes.
- `docs/lya_flux_power_spectrum.md` for P1D and LyA workflow changes.

## Review Checklist

Before finishing:

1. Confirm no large-data code path reads whole HDF5 volumes unnecessarily.
2. Confirm multiprocessing HDF5 handles are opened inside workers.
3. Confirm user-facing commands are under `fundra` unless they are required MPI
   root scripts.
4. Confirm new or changed CLI commands have help text and focused tests.
5. Confirm docs mention any new workflow entry point or changed command syntax.
