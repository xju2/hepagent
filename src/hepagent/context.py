"""Keep each model request inside the context window on long runs.

Applied per call via `RunConfig.call_model_input_filter`, so it changes only what is
sent; the run's own history (and the `--trajectory` record) stays complete.

1. Observation masking: once the request passes `MASK_FRACTION` of `max_tokens`, tool
   outputs older than the last `KEEP_OUTPUTS` become a one-line placeholder (as in
   SWE-agent). Masking from the start made a strong model re-read its own files.
2. If the estimate is still over `max_tokens`, the oldest items after the task prompt
   are dropped, never splitting a tool call from its output.
"""

from __future__ import annotations

import json

from agents.run import CallModelData, ModelInputData

KEEP_OUTPUTS = 10
MASK_FRACTION = 0.6
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

    # Keep items[0] (the task prompt); cut a prefix of the rest until it fits.
    cut = 1
    while cut < len(items) - 1 and _tokens(items[:1] + items[cut:], instructions) > max_tokens:
        cut += 1
    # Outputs whose call was cut would be rejected by the API.
    while cut < len(items) and _type(items[cut]) == "function_call_output":
        cut += 1
    if cut == 1:
        return items
    note = {
        "role": "user",
        "content": f"[{cut - 1} earlier conversation items were dropped to fit the context "
        "window. Rely on your notes and files for anything from before.]",
    }
    return items[:1] + [note] + items[cut:]


def input_filter(max_tokens: int):
    def apply(data: CallModelData) -> ModelInputData:
        md = data.model_data
        return ModelInputData(
            input=trim(md.input, md.instructions, max_tokens), instructions=md.instructions
        )

    return apply
