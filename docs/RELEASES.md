# Release Instructions for AI Agents

This document is the single source of truth for preparing and publishing a new HepAgent release.

## Goal

Produce a clean, reproducible release by:

1. validating code quality and tests,
2. updating package and changelog metadata and coverage.
3. creating and pushing a version tag, and
4. triggering the GitHub release workflow.

## Preconditions

Before starting, ensure all are true:

- You are in the repo root.
- You have a clean working tree (or only intentional release-related changes).
- You know the next semantic version number (example: `0.3.2`).
- You have permission to push commits and tags.

## Mandatory Constraints

- Do not include unrelated refactors in the release commit.
- Do not skip tests unless explicitly instructed by a maintainer.
- Keep release edits limited to version/changelog/docs as much as possible.
- If CI is red on default branch, stop and report instead of releasing.

## Release Procedure

Follow these steps in order.

### 1. Sync and Baseline Checks

Run from repository root:

```bash
uv sync --all-extras --all-packages --group dev
make format
make tests
make coverage
```

If any command fails:

- stop,
- summarize the failure clearly, and
- do not continue to tagging.

### 2. Choose Version and Update Metadata

Let `X.Y.Z` be the new version and `vX.Y.Z` the git tag.

1. Update `pyproject.toml`:
   - set `[project].version` to `X.Y.Z`.
2. Update `CHANGELOG.md`:
   - add a new top section `## [vX.Y.Z] - YYYY-MM-DD`,
   - include a short overview,
   - include key user-facing changes (features/fixes/breaking changes),
   - include link references at the bottom (compare links if available).

Changelog style should match existing entries in `CHANGELOG.md`.

### 3. Re-run Validation After Edits

Run:

```bash
make format
make tests
```

If the release modifies runtime behavior or dependencies, also run:

```bash
make coverage
```

Update the coverage in `README.md` if it has changed.

### 4. Commit Release Changes

Stage only intended release files, typically:

- `pyproject.toml`
- `CHANGELOG.md`
- optional release docs updates

Create a commit:

```bash
git add pyproject.toml CHANGELOG.md docs/RELEASES.md
git commit -m "release: vX.Y.Z"
```

If `docs/RELEASES.md` was not changed in this cycle, omit it from `git add`.

### 5. Create and Push Tag

Create an annotated tag and push both commit and tag:

```bash
git tag -a vX.Y.Z -m "Release vX.Y.Z"
git push origin HEAD
git push origin vX.Y.Z
```

## Output Format for Agent Report

At completion, report:

1. new version and tag,
2. files changed,
3. validation commands run and pass/fail summary,
4. commit SHA,
5. confirmation that tag was pushed,
6. confirmation that release workflow was triggered (and status if known),
7. any follow-up action required from a human maintainer.

## Fast Checklist

- [ ] `pyproject.toml` version bumped to `X.Y.Z`
- [ ] `CHANGELOG.md` has `vX.Y.Z` section dated today
- [ ] local checks pass (`format-check`, `lint`, `tests`, optionally `coverage`)
- [ ] release commit created
- [ ] annotated tag `vX.Y.Z` created and pushed
- [ ] `Create Release` workflow run with `tag=vX.Y.Z`
- [ ] release verified on GitHub