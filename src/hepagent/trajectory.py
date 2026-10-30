"""Append-only JSONL record of a `hepagent run`: every LLM response and tool result.

Each event is flushed as it happens, so a run killed by an external timeout still
leaves a complete record of what it did and what it cost up to that point.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents import RunHooks, Usage
from hepagent.token_costs import calculate_cost


class TrajectoryRecorder(RunHooks):
    def __init__(self, path: str | Path, model_name: str):
        self.model_name = model_name
        self._system_prompt_written = False
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(path, "a", encoding="utf-8")  # noqa: SIM115 - lives for the run

    def write(self, event: str, **fields: Any) -> None:
        record = {"event": event, "timestamp": datetime.now(UTC).isoformat(), **fields}
        self._fh.write(json.dumps(record, default=str) + "\n")
        self._fh.flush()

    async def on_llm_start(self, context, agent, system_prompt, input_items) -> None:
        if not self._system_prompt_written and system_prompt:
            self._system_prompt_written = True
            self.write("system_prompt", content=system_prompt)

    async def on_llm_end(self, context, agent, response) -> None:
        usage: Usage = response.usage
        details = usage.input_tokens_details
        self.write(
            "llm_response",
            model=self.model_name,
            output=[item.model_dump(exclude_none=True) for item in response.output],
            input_tokens=usage.input_tokens,
            cached_tokens=getattr(details, "cached_tokens", 0) or 0,
            output_tokens=usage.output_tokens,
            cost_usd=calculate_cost(usage, self.model_name),
        )

    async def on_tool_end(self, context, agent, tool, result) -> None:
        self.write("tool_result", tool=getattr(tool, "name", ""), output=str(result))

    def close(self) -> None:
        self._fh.close()
