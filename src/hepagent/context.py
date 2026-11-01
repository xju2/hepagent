"""Keep each model request inside the context window on long runs.

Applied per call via `RunConfig.call_model_input_filter`, so it changes only what is
sent; the run's own history (and the `--trajectory` record) stays complete.

1. Observation masking: once the request passes `MASK_FRACTION` of `max_tokens`, tool
   outputs older than the last `KEEP_OUTPUTS` become a one-line placeholder (as in
   SWE-agent). Masking from the start made a strong model re-read its own files.
2. If the estimate is still over `max_tokens`, the oldest items after the task prompt
   are dropped down to `RESET_FRACTION` of it (a context reset: the agent continues
   from its STATE.md, which the status message carries), never splitting a tool call
   from its output.
"""

from __future__ import annotations

import json

from agents.run import CallModelData, ModelInputData

KEEP_OUTPUTS = 10
MASK_FRACTION = 0.6
RESET_FRACTION = 0.5
MASK_ABOVE_CHARS = 300
# shortcut: crude chars-per-token estimate, low on purpose; use a tokenizer if it misjudges
CHARS_PER_TOKEN = 3


def _tokens(items: list, instructions: str | None) -> int:
    return (len(json.dumps(items, default=str)) + len(instructions or "")) // CHARS_PER_TOKEN


def _type(item) -> str | None:
    return item.get("type") if isinstance(item, dict) else None


def trim(items: list, instructions: str | None, max_tokens: int) -> list:
    items = list(items)
    if _tokens(items, instructions) <= MASK_FRACTION * max_tokens:
        return items
    outputs = [i for i, it in enumerate(items) if _type(it) == "function_call_output"]
    for i in outputs[:-KEEP_OUTPUTS]:
        text = str(items[i].get("output", ""))
        if len(text) > MASK_ABOVE_CHARS:
            items[i] = {
                **items[i],
                "output": f"[old output elided ({len(text)} chars); rerun the command if needed]",
            }

    # Keep items[0] (the task prompt); cut a prefix of the rest, deep enough that the
    # next resets are many calls away.
    cut = 1
    target = max_tokens
    if _tokens(items, instructions) > max_tokens:
        target = RESET_FRACTION * max_tokens
    while cut < len(items) - 1 and _tokens(items[:1] + items[cut:], instructions) > target:
        cut += 1
    # Outputs whose call was cut would be rejected by the API.
    while cut < len(items) and _type(items[cut]) == "function_call_output":
        cut += 1
    if cut == 1:
        return items
    note = {
        "role": "user",
        "content": f"[{cut - 1} earlier conversation items were dropped to fit the context "
        "window. Continue from your STATE.md and files for anything from before.]",
    }
    return items[:1] + [note] + items[cut:]


WRAP_UP_AT = 0.75
WRAP_UP = (
    "WRAP UP NOW: {pct:.0f}% of your {what} budget is used. Model calls stop at the "
    "limit, but background processes keep running until the deadline. Launch your best "
    "long-running method on all cores as background jobs that atomically write each "
    "improvement into the final output files, check the output files are valid, then "
    "poll rarely."
)


def budget_status(
    time_left: float | None, time_budget: float | None, spent: float, max_cost: float | None
) -> str | None:
    """Status section for the instructions: time and spend left, plus a wrap-up order."""
    lines, used = [], []
    if time_budget:
        lines.append(f"Time left: {max(0, time_left):.0f} s of {time_budget:.0f} s.")
        used.append((1 - time_left / time_budget, "time"))
    if max_cost:
        lines.append(f"Model spend: ${spent:.2f} of ${max_cost:.2f}.")
        used.append((spent / max_cost, "model-cost"))
    if not lines:
        return None
    frac, what = max(used)
    if frac >= WRAP_UP_AT:
        lines.append(WRAP_UP.format(pct=100 * frac, what=what))
    return "# BUDGET\n" + "\n".join(lines)


def input_filter(max_tokens: int, status=None):
    """`status()` returns text sent as a trailing message on every call, or None.

    Trailing, not in the instructions, so the request prefix stays cacheable.
    """

    def apply(data: CallModelData) -> ModelInputData:
        md = data.model_data
        items = trim(md.input, md.instructions, max_tokens)
        extra = status() if status else None
        if extra:
            items.append({"role": "user", "content": extra})
        return ModelInputData(input=items, instructions=md.instructions)

    return apply
