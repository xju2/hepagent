---
name: fundra-workflows
description: Operate and modify the Fundra foundational-universe project workflows for large cosmology data on Perlmutter. Use when HepAgent SkillAgent or Codex needs to run or extend Fundra data preprocessing, Nyx suite catalog/provenance, AMReX-to-HDF5/P1D/histogram post-processing, VQVAE training/tokenization/reconstruction, BERT/GPT prior training, latent diffusion sampling/comparison, plotting, Hydra config creation, SLURM wrappers, or project-specific tests and CLI changes.
---

# Fundra Workflows

Use this skill with HepAgent's `SkillAgent` to work in the
`foundational_universe` repository. Fundra targets large cosmology datasets, so
assume arrays do not fit in memory and prefer chunked I/O, MPI/SLURM workflows,
and bounded reductions.

## Start Here

1. Work from the repository root.
2. Read `AGENTS.md` and preserve its constraints.
3. If this skill was not already loaded, call
   `load_skill_details("fundra-workflows")`.
4. Inspect the current CLI before acting:
   `uv run fundra --help`, then the narrow subgroup help needed for the task.
   With HepAgent, run shell commands through
   `execute_bash_command_with_confirmation`; use tmux tools for interactive
   SLURM allocations.
5. Use `fundra suite propose` when a catalog exists and the user asks what to
   run next; it can surface exact commands stored in artifact metadata.
6. Prefer `docs/Workflows.md` as the top-level workflow map, then load one
   resource below only when it matches the task.

## HepAgent Resources

- Call `read_resource("workflow-routes")` for end-to-end workflow routing,
  command order, and Perlmutter/SLURM entry points.
- Call `read_resource("cli-and-configs")` when adding, changing, or invoking
  `fundra` CLI commands, Hydra configs, standalone MPI scripts, or wrappers.
- Call `read_resource("development")` when editing code, tests, docs, or
  validation.
- For Nyx 2048^3 simulation reproduction/debugging, use the adjacent skill at
  `.agents/skills/nyx-2048-perlmutter/SKILL.md` in addition to this one when
  running inside HepAgent.

HepAgent's `read_resource` reads from the currently active skill, so load this
skill before reading these resource names.

## Operating Rules

- Do not materialize full HDF5 fields or all chunks unless a neighboring script
  already proves the data volume is bounded.
- Open HDF5 files inside each multiprocessing worker; do not share file handles
  across processes.
- Keep CPU-side analysis and plotting worker-scaled and memory bounded.
- Keep user-facing commands under `src/fundra/cli/` as Click commands. Keep
  backend logic callable from `src/fundra/scripts/` without frontends.
- Leave `fundra_histogramize_mpi`, `fundra_chunkify_mpi`, and
  `fundra_vqvae_reco_mpi` as root-level project scripts because `srun` must
  launch them directly.
- For plotting work, force headless behavior and verify with noninteractive
  tests or a small sample path.

## Validation

- For local changes, run the narrowest relevant `uv run ruff check ...` and
  `uv run pytest ...` targets first.
- If adding or changing a CLI command, run its `--help` path.
- If changing scripts used by SLURM, run dry-run/help paths or focused unit
  tests before recommending batch submission.
- If implementing a feature requested by a doc note, update that doc with a
  short completion summary.
- When a command fails or a user correction changes the workflow, call
  `update_logbook` with the corrective insight.
