import asyncio
from time import monotonic

import hepagent.longrun as longrun_module
from hepagent.longrun import LongRun


def _lr(tmp_path, deadline=None):
    return LongRun(deadline, "task", workdir=tmp_path)


def test_wait_wakes_when_job_exits_and_reports_log(tmp_path, monkeypatch):
    monkeypatch.setattr(longrun_module.asyncio, "sleep", _fast_sleep)
    lr = _lr(tmp_path)
    assert "Started job 'j'" in lr.start_job("j", "echo hello; sleep 0.3; echo done")
    out = asyncio.run(lr.wait(minutes=1))
    assert "finished: j" in out
    assert "job j: exited 0 | hello | done" in out


def test_wait_wakes_on_log_pattern_and_stop_kills_group(tmp_path, monkeypatch):
    monkeypatch.setattr(longrun_module.asyncio, "sleep", _fast_sleep)
    lr = _lr(tmp_path)
    lr.start_job("j", "sleep 0.2; echo READY; sleep 30")
    out = asyncio.run(lr.wait(minutes=1, log_pattern="READY"))
    assert "log match in j" in out
    lr.stop("j")
    lr.jobs["j"].proc.wait(timeout=5)
    assert lr.jobs["j"].proc.poll() is not None


def test_wait_returns_at_timeout_capped_by_deadline(tmp_path):
    lr = _lr(tmp_path, deadline=monotonic() + longrun_module.DEADLINE_MARGIN_S + 0.2)
    start = monotonic()
    out = asyncio.run(lr.wait(minutes=10))
    assert monotonic() - start < 5 and "time is up" in out


def test_keeper_snapshots_improvements_and_restores_worse_file(tmp_path):
    lr = _lr(tmp_path)
    out = tmp_path / "result.txt"
    out.write_text("5\n")
    # Score = the number in the file; lower is better; "bad" is invalid.
    msg = lr.keep(str(out), "python3 -c 'import sys; print(float(open(sys.argv[1]).read()))'")
    assert "new best 5" in msg

    out.write_text("3\n")
    assert any("new best 3" in n for n in lr.check_outputs())

    out.write_text("9\n")
    assert any("restored best 3" in n for n in lr.check_outputs())
    assert out.read_text() == "3\n"

    out.write_text("bad")
    lr.check_outputs()
    assert out.read_text() == "3\n"
    assert lr.improvements == 2


def test_checkpoints_and_state_in_status(tmp_path):
    lr = _lr(tmp_path)
    assert lr.checkpoints(0.1) == []
    notes = lr.checkpoints(0.35)
    assert any("15%" in n for n in notes) and any("nothing is running" in n for n in notes)
    assert "STATE.md does not exist yet" in lr.status(0.1)
    (tmp_path / "STATE.md").write_text("plan: step 1")
    assert "plan: step 1" in lr.status(0.1)


def test_delegate_runs_subagents_in_parallel_and_wait_collects_reports(tmp_path, monkeypatch):
    lr = _lr(tmp_path)

    async def fake_sub(name, prompt, deadline):
        assert "Your assignment:\nbrief " + name in prompt
        await asyncio.sleep(0.05)
        return f"report from {name}"

    lr.run_subagent = fake_sub

    async def scenario():
        assert "started" in lr.delegate("a", "brief a", 5)
        assert "started" in lr.delegate("b", "brief b", 5)
        await asyncio.gather(*(s.task for s in lr.subagents.values()))
        return await lr.wait(minutes=1)

    out = asyncio.run(scenario())
    assert "finished: a, b" in out
    assert "report from a" in out and "report from b" in out
    assert (tmp_path / "sub" / "a").is_dir()


def test_delegate_reports_subagent_failure(tmp_path):
    lr = _lr(tmp_path)

    async def broken(name, prompt, deadline):
        raise RuntimeError("boom")

    lr.run_subagent = broken

    async def scenario():
        lr.delegate("x", "brief", 5)
        await lr.subagents["x"].task

    asyncio.run(scenario())
    assert lr.subagents["x"].result == "Failed: RuntimeError: boom"


def test_subagent_tools_are_prefixed_and_cannot_delegate(tmp_path):
    lr = _lr(tmp_path)
    names = [t.name for t in lr.tools(orchestrator=False, prefix="a/")]
    assert "delegate" not in names
    assert "delegate" in [t.name for t in lr.tools()]


_real_sleep = asyncio.sleep


async def _fast_sleep(seconds):
    await _real_sleep(min(seconds, 0.05))
