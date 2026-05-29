# LLM-Driven HEP Analysis: Methodology Specification

## 1. Scope and Principles

This spec defines *what* each phase must produce, not *how*. The agent
selects tools, writes code, and makes physics judgments within these
constraints.

**Quality bar: publication-ready.** Every phase must meet the standard:
would a senior physicist on a review committee approve this? Not "good
enough to move on" — good enough to publish.

**Default posture: strongest achievable result.** The goal of each phase
is not the first acceptable result but the strongest defensible one. When
multiple approaches exist, try the most powerful first. When a method is
difficult, attempt it before falling back. When the spec is silent on a
judgment call, prefer the choice that makes the analysis harder to
criticize. Downscoping (§12) exists for genuine constraints — missing
data, infeasible methods, impossible requirements — not for avoiding
difficulty.

**Design principles:**
- **Artifacts over memory.** Each phase produces a self-contained report.
  Subsequent phases read reports, not conversation history.
- **Review at every level.** Plans, code, results, and writeup all reviewed.
- **The agent adapts.** Omitting an unnecessary step is correct; performing
  it without justification is not.
- **Conventions over encoded physics.** Operational knowledge in
  `conventions/`; agent consults at Phases 1, 4a, 5.
- **Downscope as last resort, don't block.** When the full-strength
  approach is genuinely infeasible — not merely difficult — fall back
  rather than stall. But downscoping without first attempting the
  stronger method (or documenting why it is infeasible) is a process
  failure, not pragmatism. See §12.
