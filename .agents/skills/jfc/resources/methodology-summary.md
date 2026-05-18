# JFC Methodology Summary

Condensed reference for executor and reviewer agents. Full spec in
`testarea/jfc/src/methodology/`.

---

## Phase Entry and Exit Criteria

### Phase 1: Strategy
**Entry:** Physics prompt + analysis type provided.
**Must produce:** `STRATEGY.md` with:
- Physics motivation, signal/background classification
- Selection approach (≥2 candidate approaches to explore in Phase 3)
- Systematic plan covering all sources from `conventions/` + reference analyses
- 2–3 reference analyses with tabulated systematic programs
- Extracted published numerical results (comparison targets for Phase 4c)
- Flagship figures list (~6 "money plots")
- Methodology diagrams plan
- Commitment labels [D1]-[DN], constraint labels [A1]-[AN], limitation labels [L1]-[LN]
- `COMMITMENTS.md` stub with all [D] items

**Exit gate:** 4-bot review (physics + critical + constructive + arbiter). PASS required.

### Phase 2: Exploration
**Entry:** `STRATEGY.md` (PASS).
**Must produce:** `EXPLORATION.md` with:
- Sample inventory (files, trees, branches, events, cross-sections)
- Data quality validation (pathologies, unphysical values)
- Data/MC agreement on candidate selection variables (chi2/ndf per variable)
- Published yield cross-check (f_presel = N_obs/N_exp per energy point)
- Data archaeology: all weight/flag branches checked for non-triviality
- Baseline yields after preselection
- Strategy revision inputs flagged if Phase 2 discoveries invalidate Phase 1 assumptions
- PDF build test (stub `pixi run build-pdf`)

**Exit gate:** Self-review + plot validator. Any plot RED FLAG → Category A.

### Phase 3: Processing
**Entry:** `EXPLORATION.md` (PASS) + `STRATEGY.md`.
**Must produce:** `SELECTION.md` with:
- ≥2 selection approaches tried and compared quantitatively
- For MVA: variable quality gate table (discrimination × data/MC chi2/ndf)
- Response matrix (diagonal fraction ≥ 50%, early gate check)
- Closure test: chi2/ndf in [0.1, 3.0], no pull > 5-sigma
- Stress test: passes at level of expected data/MC differences
- Flat-prior test: ≥50% bins with <20% shift
- Alternative method closure: chi2/ndf < 5
- Method health assessment section ("Is the method working?")
- `COMMITMENTS.md` updated for any [D] items addressed

**Exit gate:** 1-bot (critical + plot validator). PASS required.

### Phase 4a: Expected Results (Asimov only)
**Entry:** `SELECTION.md` (PASS) + `COMMITMENTS.md` all-resolved gate.
**Must produce:** `INFERENCE_EXPECTED.md` + `ANALYSIS_NOTE_4a_v1.md` + compiled PDF:
- Systematic completeness table (source × conventions, ref1, ref2, this analysis)
- Every systematic: propagated through chain (not borrowed flat), non-zero bin impact
- Systematic implementation self-check (5-point checklist)
- Signal injection tests (searches) or closure tests (measurements)
- GoF: chi2/ndf AND toy-based p-value
- Full covariance matrix (stat + per-syst + total)
- Per-systematic impact figures
- Extraction method: differential fit preferred over mean value
- Published overlay with chi2 on all measurement figures
- `COMMITMENTS.md` updated

**Exit gate:** 4-bot + bibtex validator. PDF must exist before review.

### Phase 4b: 10% Data Validation
**Entry:** `INFERENCE_EXPECTED.md` (PASS).
**Must produce:** `INFERENCE_PARTIAL.md` + `ANALYSIS_NOTE_4b_v1.md` + compiled PDF:
- 10% data (fixed seed), MC normalized to 10% luminosity
- GoF, NP pulls, impact ranking on 10% data
- Comparison to Phase 4a expected (overlay, chi2)
- Figure reference verification before PDF compilation
- Updated AN with 10% results

**Exit gate:** 4-bot + bibtex → **human gate**. Human must receive compiled PDF.

### Phase 4c: Full Data
**Entry:** Human APPROVE after Phase 4b.
**Must produce:** `INFERENCE_OBSERVED.md` + `ANALYSIS_NOTE_4c_v1.md` + compiled PDF:
- Full chain on complete dataset
- Systematics re-evaluated on full data (not just transferred from MC)
- Comparison to both 10% and Asimov expected
- Fit pathologies investigated (no silent workarounds)
- Fit triviality gate: chi2 ≈ 0 → investigate algebraic circularity
- Machine-readable results in `phase5_documentation/outputs/results/*.json`
- Viability check on ALL reported measurements

**Exit gate:** 1-bot. Escalates to 4-bot if any result > 2-sigma from Phase 4a expected.

### Phase 5: Documentation
**Entry:** `INFERENCE_OBSERVED.md` (PASS).
**Must produce:** `ANALYSIS_NOTE_5_v1.md` + compiled PDF + `results/` directory:
- Figure production (flagship figures, methodology diagrams)
- AN writing: complete self-contained document (50–100 pages)
- Minimum 4 equations (observable def, correction, systematic eval, fit model)
- Number consistency gate: all AN numbers match `results/*.json` to <1% relative
- Typesetting: pandoc → postprocess_tex.py → typesetter → tectonic
- COMMITMENTS.md: all [x] or [D], none pending

**Exit gate:** 5-bot (physics + critical + constructive + plot validator + rendering + bibtex + arbiter).

---

## Review Tier Reference

| Phase | Tier | Reviewers |
|-------|------|-----------|
| 1 | 4-bot | physics, critical, constructive (parallel) → arbiter |
| 2 | self + plot | plot validator runs automatically |
| 3 | 1-bot | critical + plot validator (parallel) |
| 4a | 4-bot + bib | physics, critical, constructive, bibtex (parallel) → arbiter |
| 4b | 4-bot + bib → human | same as 4a → human gate |
| 4c | 1-bot | critical + plot validator; escalate to 4-bot if surprises |
| 5 | 5-bot | physics, critical, constructive, plot validator, rendering, bibtex → arbiter |

**4-bot review:** physics reviewer receives ONLY physics prompt + artifact.
All others receive full methodology context + conventions.
Plot validator RED FLAGS are auto-Category A — arbiter cannot downgrade.

---

## Artifact Format Requirements

Every phase artifact (`STRATEGY.md`, `EXPLORATION.md`, etc.) must have:
1. **Summary** — what was accomplished (1 paragraph)
2. **Method** — reproducible detail
3. **Results** — tables, figures (by path), numbers with uncertainties
4. **Validation** — checks performed, quantitative outcomes
5. **Open issues** — what subsequent phases should be aware of
6. **Code reference** — `pixi run <task>` commands that produced results

Figure captions: 2–4 sentences. Name the plot, state context not visible in
the plot, give the key conclusion. Never restate axis labels or legend.

---

## Commitment Tracking Format

`COMMITMENTS.md` uses this markdown table format:

```markdown
# Phase 1 Commitments

| ID | Commitment | Status | Evidence | Phase Resolved |
|----|-----------|--------|----------|---------------|
| D1 | Compare cut-based and MVA-based selection | pending | — | — |
| D2 | Use unfolding for cross-section | pending | — | — |
| D3 | Quote jet energy scale systematic | pending | — | — |
```

Status values: `pending` | `resolved` | `downscoped`

For downscoped: Evidence must include "Attempted: [what was tried]. Failed because: [specific reason]."
Silent downscoping (no attempt documented) is Category A.

---

## Downscoping Rules

Downscoping is permitted only when the stronger approach has been:
(a) attempted and failed with documented evidence, or
(b) demonstrably infeasible (not merely difficult)

Protocol:
1. Document the constraint in `experiment_log.md`
2. Choose the best achievable fallback method
3. Label with [D] in the experiment log
4. Quantify the impact on the result
5. Carry to the AN (method section + systematic table + Future Directions)

Downscoping without evidence of attempting the stronger method is Category A.

---

## Closure Alarm Bands (Non-Negotiable)

- **chi2/ndf < 0.1** → Category A (suspicious; check for tautological test, inflated uncertainties)
- **chi2/ndf > 3 OR any pull > 5-sigma** → Category A (method failure; do not proceed)
- **`passes: false` in JSON while artifact claims acceptable** → Category A (misrepresentation)

These apply at Phases 3 AND 4a. They cannot be rationalized away.

---

## Iteration Protocol

- **PASS:** advance to next phase; git commit
- **ITERATE:** fixer agent addresses Category A/B findings; fresh review required
- **ESCALATE:** human intervention needed
- **MaxIterations (3 per phase):** raise `MaxIterationsExceeded`; pause for human

Re-review must produce a written re-review artifact verifying each original finding.
"ITERATE → fix → advance without re-review" is a process failure.

---

## Key Anti-Patterns

- Self-review for phases other than Phase 2
- Skipping artifacts under context pressure (write artifact and stop cleanly)
- Fabrication: tuning parameters to match, dropping large systematics, smoothing uncertainty bands
- Borrowed flat systematics without documented justification
- Silent commitment downscoping (no attempt documented)
- Accepting validation failures without 3+ remediation attempts
- GoF chi2/ndf identically zero without investigating algebraic circularity
