import hepagent.agents.bash as bash
import json
import asyncio
from agents.tool import ToolContext


def test_create_uses_execute_bash_tool(monkeypatch):
    captured = {}

    class StubAgent:
        def __init__(self, name, instructions, model, tools):
            captured["name"] = name
            captured["instructions"] = instructions
            captured["model"] = model
            captured["tools"] = tools

    sentinel_model = object()
    monkeypatch.setattr(bash, "Agent", StubAgent)
    monkeypatch.setattr(bash, "get_model_provider", lambda **_kwargs: sentinel_model)

    agent = bash.create()

    assert isinstance(agent, StubAgent)
    assert captured["name"] == "Bash Agent"
    assert captured["model"] is sentinel_model
    # Check that we have bash execution and journal tools.
    assert len(captured["tools"]) == 2
    tool_names = [getattr(t, "name", "") for t in captured["tools"]]
    assert any("execute_bash_command" in n for n in tool_names)
    assert any("get_execution_journal" in n for n in tool_names)
    assert "THOUGHT" in captured["instructions"]


def test_execute_bash_command_blocks_broad_scan_by_default(monkeypatch):
    def fail_run(*_args, **_kwargs):
        raise AssertionError("subprocess.run should not be called for blocked commands")

    monkeypatch.delenv("HEPAGENT_ALLOW_BROAD_SCAN", raising=False)
    monkeypatch.setattr(bash.subprocess, "run", fail_run)

    result = bash.execute_bash_command("ls -R", cwd="")
    assert result["returncode"] == 2
    assert "Command blocked by safety guard" in result["output"]
    assert "Blocked command policy:" in result["output"]


def test_execute_bash_command_truncates_large_output(monkeypatch):
    class DummyProc:
        def __init__(self, stdout: str, returncode: int):
            self.stdout = stdout
            self.returncode = returncode

    big = "x" * 12050

    monkeypatch.setenv("HEPAGENT_ALLOW_BROAD_SCAN", "1")
    monkeypatch.setenv("HEPAGENT_OUTPUT_CHAR_LIMIT", "10000")
    monkeypatch.setattr(
        bash.subprocess,
        "run",
        lambda *args, **kwargs: DummyProc(stdout=big, returncode=0),
    )

    result = bash.execute_bash_command("echo large", cwd="")
    assert result["returncode"] == 0
    assert "output truncated: omitted" in result["output"]
    assert len(result["output"]) < len(big)


def test_execute_bash_command_blocks_multifile_cat(monkeypatch):
    def fail_run(*_args, **_kwargs):
        raise AssertionError("subprocess.run should not be called for blocked commands")

    monkeypatch.delenv("HEPAGENT_ALLOW_BROAD_SCAN", raising=False)
    monkeypatch.setattr(bash.subprocess, "run", fail_run)

    result = bash.execute_bash_command("cat a.txt b.txt", cwd="")
    assert result["returncode"] == 2
    assert "multi-file 'cat'" in result["output"]
    assert "Blocked command policy:" in result["output"]


def test_execute_bash_command_allows_heredoc_cat_write(monkeypatch):
    class DummyProc:
        def __init__(self):
            self.stdout = "ok"
            self.returncode = 0

    monkeypatch.delenv("HEPAGENT_ALLOW_BROAD_SCAN", raising=False)
    monkeypatch.setattr(bash.subprocess, "run", lambda *args, **kwargs: DummyProc())
    result = bash.execute_bash_command("cat <<EOF > x.txt\nhello\nEOF", cwd="")
    assert result["returncode"] == 0


def test_execute_bash_command_allows_heredoc_cat_write_redirect_first(monkeypatch):
    class DummyProc:
        def __init__(self):
            self.stdout = "ok"
            self.returncode = 0

    monkeypatch.delenv("HEPAGENT_ALLOW_BROAD_SCAN", raising=False)
    monkeypatch.setattr(bash.subprocess, "run", lambda *args, **kwargs: DummyProc())
    result = bash.execute_bash_command("cat > x.txt <<EOF\nhello\nEOF", cwd="")
    assert result["returncode"] == 0


def test_execute_bash_command_blocks_fragile_sed_i(monkeypatch):
    def fail_run(*_args, **_kwargs):
        raise AssertionError("subprocess.run should not be called for blocked commands")

    monkeypatch.delenv("HEPAGENT_ALLOW_FRAGILE_EDIT", raising=False)
    monkeypatch.setattr(bash.subprocess, "run", fail_run)

    result = bash.execute_bash_command("sed -i 's/a/b/g' x.txt", cwd="")
    assert result["returncode"] == 2
    assert "platform-fragile" in result["output"]


def test_execute_bash_command_allows_fragile_sed_i_with_override(monkeypatch):
    class DummyProc:
        def __init__(self):
            self.stdout = "ok"
            self.returncode = 0

    monkeypatch.setenv("HEPAGENT_ALLOW_FRAGILE_EDIT", "1")
    monkeypatch.setattr(bash.subprocess, "run", lambda *args, **kwargs: DummyProc())
    result = bash.execute_bash_command("sed -i 's/a/b/g' x.txt", cwd="")
    assert result["returncode"] == 0


def test_progress_guard_blocks_repeated_command(monkeypatch):
    guard = bash._ProgressGuardState()
    monkeypatch.setenv("HEPAGENT_MAX_SAME_COMMAND_STREAK", "1")
    first = guard.evaluate("ls -F .", "read once")
    second = guard.evaluate("ls -F .", "read once")
    assert first is None
    assert second is not None
    assert "repeated command proposals" in second


def test_progress_guard_blocks_excessive_read_only_streak(monkeypatch):
    guard = bash._ProgressGuardState()
    monkeypatch.setenv("HEPAGENT_MAX_READ_STEPS", "1")
    first = guard.evaluate("ls -F class/", "inspect")
    guard.record_result("ls -F class/", 0)
    second = guard.evaluate("cat README.md", "inspect next")
    assert first is None
    assert second is not None
    assert "too many read-only steps" in second


def test_progress_guard_ignores_bootstrap_reads(monkeypatch):
    guard = bash._ProgressGuardState()
    monkeypatch.setenv("HEPAGENT_MAX_READ_STEPS", "1")

    first = guard.evaluate("cat hepagent_instruction.txt", "bootstrap read")
    guard.record_result("cat hepagent_instruction.txt", 0)
    second = guard.evaluate("cat registry.yaml", "bootstrap read step 2")

    assert first is None
    assert second is None


def test_progress_guard_requires_clarification_after_missing_path(monkeypatch):
    guard = bash._ProgressGuardState()
    guard.record_result("ls -F missing/path", 1, "ls: missing/path: No such file or directory")
    blocked = guard.evaluate("sed -n '1,50p' AGENTS.md", "read again")
    assert blocked is not None
    assert "missing-path error" in blocked


def test_progress_guard_requires_clarification_after_missing_path_for_any_tool(monkeypatch):
    guard = bash._ProgressGuardState()
    guard.record_result("ls -F missing/path", 1, "ls: missing/path: No such file or directory")
    blocked = guard.evaluate("python3 orchestrator/run.py --config x.yaml", "execute")
    assert blocked is not None
    assert "blocking clarification question now" in blocked


def test_progress_guard_blocks_runs_root_discovery_without_scope():
    guard = bash._ProgressGuardState()
    blocked = guard.evaluate("ls -F runs/", "discover")
    assert blocked is not None
    assert "top-level 'runs/' discovery" in blocked


def test_progress_guard_allows_scoped_runs_subpath_after_manifest_read():
    guard = bash._ProgressGuardState()
    manifest_output = (
        "output_report: runs/pipeline_demo_001/orchestrator/preflight.json\n"
        "upstream_data: runs/pipeline_demo_001/class/class_out_tk.dat\n"
    )
    guard.record_result("cat orchestrator/example_manifest.yaml", 0, manifest_output)
    assert guard.evaluate("ls -F runs/pipeline_demo_001/", "inspect scoped path") is None


def test_progress_guard_policy_mode_defaults(monkeypatch):
    guard = bash._ProgressGuardState()
    monkeypatch.setenv("HEPAGENT_POLICY_MODE", "conservative")
    assert guard._limits() == (4, 1, 1, 800)
    monkeypatch.setenv("HEPAGENT_POLICY_MODE", "balanced")
    assert guard._limits() == (8, 2, 2, 1200)
    monkeypatch.setenv("HEPAGENT_POLICY_MODE", "exploratory")
    assert guard._limits() == (14, 3, 3, 1800)


def test_progress_guard_blocks_excessive_bootstrap_reads(monkeypatch):
    guard = bash._ProgressGuardState()
    monkeypatch.setenv("HEPAGENT_MAX_BOOTSTRAP_READ_STEPS", "2")

    assert guard.evaluate("cat hepagent_instruction.txt", "bootstrap 1") is None
    guard.record_result("cat hepagent_instruction.txt", 0, "ok")
    assert guard.evaluate("cat registry.yaml", "bootstrap 2") is None
    guard.record_result("cat registry.yaml", 0, "ok")
    blocked = guard.evaluate("cat AGENTS.md", "bootstrap 3")
    assert blocked is not None
    assert "bootstrap reading budget reached" in blocked


def test_progress_guard_bootstrap_budget_not_consumed_by_nonbootstrap_read(monkeypatch):
    guard = bash._ProgressGuardState()
    monkeypatch.setenv("HEPAGENT_MAX_BOOTSTRAP_READ_STEPS", "2")

    assert guard.evaluate("cat hepagent_instruction.txt", "bootstrap 1") is None
    guard.record_result("cat hepagent_instruction.txt", 0, "ok")
    assert guard.evaluate("cat registry.yaml", "bootstrap 2") is None
    guard.record_result("cat registry.yaml", 0, "ok")

    # Non-bootstrap read in bootstrap mode should not consume bootstrap budget.
    assert guard.evaluate("ls -F orchestrator/", "non-bootstrap read") is None
    guard.record_result("ls -F orchestrator/", 0, "ok")

    # A third bootstrap read should now be blocked (budget was 2).
    blocked = guard.evaluate("cat AGENTS.md", "bootstrap 3")
    assert blocked is not None
    assert "bootstrap reading budget reached" in blocked


def test_progress_guard_exits_bootstrap_mode_after_action(monkeypatch):
    guard = bash._ProgressGuardState()
    monkeypatch.setenv("HEPAGENT_MAX_BOOTSTRAP_READ_STEPS", "1")
    monkeypatch.setenv("HEPAGENT_MAX_SAME_COMMAND_STREAK", "2")

    assert guard.evaluate("cat hepagent_instruction.txt", "bootstrap") is None
    guard.record_result("cat hepagent_instruction.txt", 0, "ok")
    blocked = guard.evaluate("cat registry.yaml", "bootstrap")
    assert blocked is not None
    # Successful non-read action exits bootstrap mode.
    guard.record_result("python3 orchestrator/run.py --config x.yaml", 0, "ok")
    assert guard.bootstrap_mode is False
    assert guard.evaluate("cat registry.yaml", "follow-up read") is None


def test_execution_journal_append_and_snapshot():
    bash._reset_execution_journal_for_tests()
    bash._append_execution_journal("ls -F", ".", 0, "executed")
    bash._append_execution_journal("cat x", ".", 2, "blocked_guard")
    entries = bash._snapshot_execution_journal(10)
    assert len(entries) == 2
    assert entries[0]["cmd"] == "ls -F"
    assert entries[1]["status"] == "blocked_guard"


def test_classify_tool_result():
    assert bash._classify_tool_result({"output": "ok", "returncode": 0}) == "executed"
    assert bash._classify_tool_result({"output": "Tool calling is cancelled by user.", "returncode": 1}) == "rejected_by_user"
    assert bash._classify_tool_result({"output": "blocked", "returncode": 2}) == "blocked_guard"
    assert bash._classify_tool_result({"output": "x", "returncode": 3}) == "failed"


def test_yolo_records_progress_guard_state_for_manifest_scope(monkeypatch):
    bash._reset_progress_guard_for_tests()
    monkeypatch.setenv("HEPAGENT_YOLO", "1")

    manifest_like_output = (
        "output_report: runs/pipeline_demo_001/orchestrator/orchestrator_preflight.json\n"
        "downstream_manifest: runs/pipeline_demo_001/cosmicic_manifest.yaml\n"
    )

    monkeypatch.setattr(
        bash,
        "execute_bash_command",
        lambda cmd, cwd="": {"output": manifest_like_output, "returncode": 0},
    )

    payload = json.dumps(
        {
            "cmd": "cat orchestrator/example_manifest.yaml",
            "cwd": ".",
            "thought": "read manifest",
        }
    )
    ctx = ToolContext(
        tool_name=getattr(bash.execute_bash_command_with_confirmation, "name", ""),
        tool_call_id="test-call",
        tool_arguments=payload,
        context=None,
    )
    r = asyncio.run(bash.execute_bash_command_with_confirmation.on_invoke_tool(ctx, payload))
    assert r["returncode"] == 0
    # Scope extracted from command output should allow scoped runs subpath checks.
    assert bash._PROGRESS_GUARD.evaluate("ls runs/pipeline_demo_001/cosmicic_manifest.yaml", "check path") is None
