from hepagent.context import KEEP_OUTPUTS, trim


def _turn(i, size):
    return [
        {"type": "function_call", "call_id": f"c{i}", "name": "bash", "arguments": "{}"},
        {"type": "function_call_output", "call_id": f"c{i}", "output": "x" * size},
    ]


def test_masks_only_old_long_outputs():
    items = [{"role": "user", "content": "task"}]
    for i in range(KEEP_OUTPUTS + 2):
        items += _turn(i, 1000)
    items += _turn(99, 10)
    assert trim(items, "sys", max_tokens=10**9) == items  # far under budget: untouched
    out = trim(items, "sys", max_tokens=5000)
    outputs = [it["output"] for it in out if it.get("type") == "function_call_output"]
    assert sum("elided" in o for o in outputs) == 3
    assert outputs[-1] == "x" * 10
    assert items[2]["output"] == "x" * 1000  # input not mutated


def test_drops_oldest_without_orphaning_outputs():
    items = [{"role": "user", "content": "task"}]
    for i in range(30):
        items += _turn(i, 200)
    out = trim(items, "sys", max_tokens=1000)
    assert out[0] == items[0] and "dropped" in out[1]["content"]
    rest = out[2:]
    assert rest[0]["type"] == "function_call"
    calls = {it["call_id"] for it in rest if it["type"] == "function_call"}
    assert all(it["call_id"] in calls for it in rest if it["type"] == "function_call_output")
    assert len(out) < len(items)


def test_small_history_unchanged():
    items = [{"role": "user", "content": "task"}, *_turn(0, 50)]
    assert trim(items, "sys", max_tokens=10_000) == items
