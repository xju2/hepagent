"""Transport-agnostic streaming turn loop.

This is the algorithm from :meth:`hepagent.agents.cli_repl.CliRepl._run_turn`
with the Rich rendering replaced by :class:`~hepagent.web.bridge.TurnUI`
callbacks. The recovery behaviours are deliberately preserved, and their pure
helpers are imported from ``cli_repl`` rather than duplicated:

* a lone ```` ```bash ```` block emitted as *text* is executed through the
  approval flow and fed back to the model,
* a ``FINALIZE_NOW`` marker in tool output forces a non-streamed summary turn,
* a turn that produced no assistant text is retried once.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openai.types.responses.response_text_delta_event import ResponseTextDeltaEvent

from agents import Agent, Runner
from agents.exceptions import MaxTurnsExceeded
from agents.items import TResponseInputItem
from agents.result import RunResultBase
from agents.stream_events import (
    AgentUpdatedStreamEvent,
    RawResponsesStreamEvent,
    RunItemStreamEvent,
)
from hepagent.agents.cli_repl import (
    DEAD_AIR_RETRY_PROMPT,
    _build_command_feedback,
    _extract_single_bash_command,
    _tool_output_contains_finalize_signal,
)
from hepagent.web.bridge import TurnUI

FINALIZE_PROMPT = "FINALIZE_NOW received. Provide the final summary only; do not call tools."


@dataclass
class TurnOutcome:
    """Result of one user turn."""

    result: RunResultBase | None = None
    text: str = ""
    error: str | None = None
    input_items: list[TResponseInputItem] = field(default_factory=list)
    last_agent: Agent | None = None


def _resolve_tool_name(item: Any, tool_names: dict[str, str]) -> str:
    """Recover the tool name for a tool-output item via its call id."""
    raw = getattr(item, "raw_item", None)
    call_id = raw.get("call_id", "") if isinstance(raw, dict) else getattr(raw, "call_id", "")
    if call_id and call_id in tool_names:
        return tool_names[call_id]
    if isinstance(raw, dict) and raw.get("name"):
        return str(raw["name"])
    return "tool"


def _followup_input(
    result: RunResultBase, session: Any | None, prompt: str
) -> str | list[TResponseInputItem]:
    """Build the next model input, honouring SDK-managed session history."""
    if session is not None:
        return prompt
    followup = result.to_input_list()
    followup.append({"role": "user", "content": prompt})
    return followup


async def run_turn(
    *,
    agent: Agent,
    user_input: str,
    context: Any,
    max_turns: int,
    session: Any | None,
    ui: TurnUI,
    input_items: list[TResponseInputItem] | None = None,
) -> TurnOutcome:
    """Stream one user turn to ``ui`` and return its outcome."""
    history = list(input_items or [])
    user_item: TResponseInputItem = {"role": "user", "content": user_input}
    turn_input: str | list[TResponseInputItem] = (
        user_input if session is not None else history + [user_item]
    )

    current_agent = agent
    result: RunResultBase | None = None
    last_text = ""
    dead_air_retry_used = False
    command_proposals_run = 0
    tool_names: dict[str, str] = {}

    while True:
        try:
            result = Runner.run_streamed(
                current_agent,
                input=turn_input,
                context=context,
                max_turns=max_turns,
                session=session,
            )
            saw_text = False
            saw_tool_event = False
            saw_finalize_signal = False
            deltas: list[str] = []

            async for event in result.stream_events():
                if isinstance(event, RawResponsesStreamEvent):
                    if isinstance(event.data, ResponseTextDeltaEvent):
                        saw_text = True
                        deltas.append(event.data.delta)
                        await ui.on_text_delta(event.data.delta)
                elif isinstance(event, RunItemStreamEvent):
                    saw_tool_event = True
                    if event.item.type == "tool_call_item":
                        raw = getattr(event.item, "raw_item", None)
                        name = getattr(raw, "name", "") or "tool"
                        call_id = getattr(raw, "call_id", "")
                        if call_id:
                            tool_names[call_id] = name
                        await ui.on_tool_call(name, getattr(raw, "arguments", ""))
                    elif event.item.type == "tool_call_output_item":
                        # The SDK's function_call_output carries only a call_id,
                        # so the tool name has to come from the matching call.
                        await ui.on_tool_output(
                            _resolve_tool_name(event.item, tool_names),
                            event.item.output,
                        )
                        if _tool_output_contains_finalize_signal(event.item.output):
                            saw_finalize_signal = True
                elif isinstance(event, AgentUpdatedStreamEvent):
                    await ui.on_agent_updated(event.new_agent.name)

            full_text = "".join(deltas)
            if saw_text:
                last_text = full_text
                await ui.on_text_done(full_text)

                bash_proposal = None if saw_tool_event else _extract_single_bash_command(full_text)
                if bash_proposal is not None:
                    if command_proposals_run >= max_turns:
                        await ui.on_notice(
                            f"Max command proposals reached while continuing the turn: "
                            f"{max_turns}.",
                            level="error",
                        )
                        break
                    command_proposals_run += 1
                    command_result = await ui.run_text_bash_proposal(
                        bash_proposal.cmd, bash_proposal.thought
                    )
                    feedback = _build_command_feedback(bash_proposal.cmd, command_result)
                    turn_input = _followup_input(result, session, feedback)
                    current_agent = result.last_agent
                    dead_air_retry_used = False
                    continue

            if saw_finalize_signal and not saw_text:
                forced = await Runner.run(
                    result.last_agent,
                    input=_followup_input(result, session, FINALIZE_PROMPT),
                    context=context,
                    max_turns=max_turns,
                    session=session,
                )
                if forced.final_output is not None:
                    last_text = str(forced.final_output)
                    await ui.on_text_done(last_text)
                result = forced
                break

            if not saw_text:
                if not dead_air_retry_used:
                    await ui.on_notice(
                        "No assistant output detected. Retrying once automatically.",
                        level="warning",
                    )
                    dead_air_retry_used = True
                    turn_input = _followup_input(result, session, DEAD_AIR_RETRY_PROMPT)
                    current_agent = result.last_agent
                    continue
                await ui.on_notice(
                    "Still no assistant text after retry. Continuing to the next prompt.",
                    level="warning",
                )
            break
        except MaxTurnsExceeded:
            message = (
                f"Max turns reached: {max_turns}. Narrow the task or raise the limit "
                "with `/max-turn <turns>`."
            )
            await ui.on_notice(message, level="error")
            return TurnOutcome(result=None, text=last_text, error=message, input_items=history)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user verbatim
            await ui.on_notice(str(exc), level="error")
            return TurnOutcome(result=None, text=last_text, error=str(exc), input_items=history)

    if result is None:
        return TurnOutcome(result=None, text=last_text, input_items=history)

    return TurnOutcome(
        result=result,
        text=last_text,
        input_items=result.to_input_list(),
        last_agent=result.last_agent,
    )
