import asyncio
import json
from types import SimpleNamespace

import pytest
from openai.types.responses import ResponseFunctionToolCall
from openai.types.responses.response_usage import InputTokensDetails, OutputTokensDetails

from agents import Usage
from hepagent.trajectory import CostLimitReached, TrajectoryRecorder


def test_recorder_writes_flushed_jsonl_events(tmp_path):
    path = tmp_path / "logs" / "trajectory.jsonl"
    rec = TrajectoryRecorder(path, "openai/gpt-5")
    usage = Usage(
        requests=1,
        input_tokens=1_000_000,
        input_tokens_details=InputTokensDetails(cached_tokens=400),
        output_tokens=1_000_000,
        output_tokens_details=OutputTokensDetails(reasoning_tokens=0),
        total_tokens=2_000_000,
    )
    call = ResponseFunctionToolCall(
        type="function_call", call_id="c1", name="bash", arguments='{"cmd": "ls"}'
    )
    response = SimpleNamespace(output=[call], usage=usage)

    rec.write("user", content="task")
    asyncio.run(rec.on_llm_start(None, None, "sys", []))
    asyncio.run(rec.on_llm_start(None, None, "sys", []))
    asyncio.run(rec.on_llm_end(None, None, response))
    asyncio.run(rec.on_tool_end(None, None, SimpleNamespace(name="bash"), "out"))

    # Readable before close(): a killed run must still leave its record behind.
    events = [json.loads(line) for line in path.read_text().splitlines()]
    rec.close()

    assert [e["event"] for e in events] == ["user", "system_prompt", "llm_response", "tool_result"]
    llm = events[2]
    assert llm["output"][0]["call_id"] == "c1"
    assert llm["cached_tokens"] == 400
    assert llm["cost_usd"] == 1.75 + 15.00
    assert events[3] == {**events[3], "tool": "bash", "output": "out"}


def test_recorder_raises_once_cost_limit_reached():
    rec = TrajectoryRecorder(None, "openai/gpt-5", max_cost=1.0)
    usage = Usage(requests=1, input_tokens=400_000, output_tokens=0, total_tokens=400_000)
    response = SimpleNamespace(output=[], usage=usage)
    asyncio.run(rec.on_llm_end(None, None, response))  # $0.70
    with pytest.raises(CostLimitReached):
        asyncio.run(rec.on_llm_end(None, None, response))  # $1.40
    assert rec.total_cost == pytest.approx(1.40)
    # A delegate sharing the recorder must not start another paid request.
    with pytest.raises(CostLimitReached):
        asyncio.run(rec.on_llm_start(None, None, "sys", []))
