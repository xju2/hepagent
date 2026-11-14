"""Append-only JSONL record of a `hepagent run`: every LLM response and tool result.

Each event is flushed as it happens, so a run killed by an external timeout still
leaves a complete record of what it did and what it cost up to that point.
With `max_cost`, the response that pushes the total to it raises `CostLimitReached`.
With no path, only the cost is tracked.
"""

from __future__ import annotations

import json
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents import RunHooks, Usage
from hepagent.token_costs import calculate_cost


class CostLimitReached(Exception):
    pass


# The run's recorder, for nested `Runner.run` calls inside tools (explorer specialists).
current_recorder: ContextVar[TrajectoryRecorder | None] = ContextVar(
    "current_recorder", default=None
)


class TrajectoryRecorder(RunHooks):
    def __init__(self, path: str | Path | None, model_name: str, max_cost: float | None = None):
        self.model_name = model_name
        self.max_cost = max_cost
        self.total_cost = 0.0
        self._system_prompt_written = False
        self._fh = None
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._fh = open(path, "a", encoding="utf-8")  # noqa: SIM115 - lives for the run

    def write(self, event: str, **fields: Any) -> None:
        if self._fh is None:
            return
        record = {"event": event, "timestamp": datetime.now(UTC).isoformat(), **fields}
        self._fh.write(json.dumps(record, default=str) + "\n")
        self._fh.flush()

    def check_cost(self) -> None:
        if self.max_cost is not None and self.total_cost >= self.max_cost:
            raise CostLimitReached(f"${self.total_cost:.2f} spent (limit ${self.max_cost:.2f})")

    async def on_llm_start(self, context, agent, system_prompt, input_items) -> None:
        # Delegates and specialists share this recorder; refuse a request once any spent the cap.
        self.check_cost()
        if not self._system_prompt_written and system_prompt:
            self._system_prompt_written = True
            self.write("system_prompt", content=system_prompt)

    async def on_llm_end(self, context, agent, response) -> None:
        usage: Usage = response.usage
        details = usage.input_tokens_details
        cost = calculate_cost(usage, self.model_name)
        self.total_cost += cost
        self.write(
            "llm_response",
            agent=getattr(agent, "name", None),
            model=self.model_name,
            output=[item.model_dump(exclude_none=True) for item in response.output],
            input_tokens=usage.input_tokens,
            cached_tokens=getattr(details, "cached_tokens", 0) or 0,
            output_tokens=usage.output_tokens,
            cost_usd=cost,
        )
        self.check_cost()

    async def on_tool_end(self, context, agent, tool, result) -> None:
        self.write("tool_result", tool=getattr(tool, "name", ""), output=str(result))

    def close(self) -> None:
        if self._fh:
            self._fh.close()
