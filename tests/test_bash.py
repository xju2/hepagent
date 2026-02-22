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
    assert "preflight report fails only because manifest-scoped files are missing" in captured["instructions"]


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


def test_progress_guard_blocks_repeated_low_progress_loop(monkeypatch):
    guard = bash._ProgressGuardState()
    monkeypatch.setenv("HEPAGENT_MAX_NO_PROGRESS_STEPS", "1")
    guard.record_result("ls -F class/", 0)
    guard.record_result("ls -F class/", 0)
    blocked = guard.evaluate("ls -F class/", "inspect again")
    assert blocked is not None
    assert "low-progress loop" in blocked


def test_progress_guard_allows_new_read_targets_under_no_progress_limit(monkeypatch):
    guard = bash._ProgressGuardState()
    monkeypatch.setenv("HEPAGENT_MAX_NO_PROGRESS_STEPS", "1")
    guard.record_result("cat hepagent_instruction.txt", 0)
    assert guard.evaluate("cat registry.yaml", "read next file") is None


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


def test_progress_guard_allows_missing_manifest_scoped_runs_path_without_forced_clarification():
    guard = bash._ProgressGuardState()
    # Simulate manifest scope discovery from a prior successful read.
    manifest_output = (
        "output_report: runs/pipeline_demo_001/orchestrator/orchestrator_preflight.json\n"
        "downstream_manifest: runs/pipeline_demo_001/cosmicic_manifest.yaml\n"
    )
    guard.record_result("cat orchestrator/example_manifest.yaml", 0, manifest_output)
    # Missing path is within known scoped runs path.
    guard.record_result(
        "ls runs/pipeline_demo_001/cosmicic_manifest.yaml",
        1,
        "ls: runs/pipeline_demo_001/cosmicic_manifest.yaml: No such file or directory",
    )
    # Next tool call should not be blocked by missing-path clarification guard.
    assert guard.evaluate("mkdir -p runs/pipeline_demo_001", "create scaffold") is None


def test_progress_guard_requires_setup_action_after_scoped_missing_path():
    guard = bash._ProgressGuardState()
    guard.record_result(
        "cat hepagent_instruction.txt",
        0,
        "Task:\nPrepare a production preflight plan for the canonical chain\nRun only orchestrator preflight",
    )
    manifest_output = (
        "output_report: runs/pipeline_demo_001/orchestrator/orchestrator_preflight.json\n"
        "downstream_manifest: runs/pipeline_demo_001/cosmicic_manifest.yaml\n"
    )
    guard.record_result("cat orchestrator/example_manifest.yaml", 0, manifest_output)
    guard.record_result(
        "ls runs/pipeline_demo_001/cosmicic_manifest.yaml",
        1,
        "ls: runs/pipeline_demo_001/cosmicic_manifest.yaml: No such file or directory",
    )
    blocked = guard.evaluate("cat orchestrator/example_manifest.yaml", "re-read")
    assert blocked is not None
    assert "in-scope setup action" in blocked
    assert "Suggested next command" in blocked
    assert "mkdir -p" in blocked
    # Non-read setup action should be allowed immediately.
    assert guard.evaluate("mkdir -p runs/pipeline_demo_001", "create scaffold") is None


def test_progress_guard_persists_scaffold_requirement_until_nonread_action():
    guard = bash._ProgressGuardState()
    guard.record_result(
        "cat hepagent_instruction.txt",
        0,
        "Task:\nPrepare a production preflight plan for the canonical chain\nRun only orchestrator preflight",
    )
    guard.record_result(
        "cat orchestrator/example_manifest.yaml",
        0,
        "output_report: runs/pipeline_demo_001/orchestrator/orchestrator_preflight.json\n",
    )
    guard.record_result(
        "ls runs/pipeline_demo_001/cosmicic_manifest.yaml",
        1,
        "ls: runs/pipeline_demo_001/cosmicic_manifest.yaml: No such file or directory",
    )
    blocked1 = guard.evaluate("cat class/contract.yaml", "keep reading")
    assert blocked1 is not None
    assert "manifest-scoped paths are missing under runs/" in blocked1
    blocked2 = guard.evaluate("cat nyx/contract.yaml", "keep reading")
    assert blocked2 is not None
    assert "manifest-scoped paths are missing under runs/" in blocked2
    assert guard.evaluate("mkdir -p runs/pipeline_demo_001", "setup") is None
    # After successful non-read action, scaffold block should clear.
    guard.record_result("mkdir -p runs/pipeline_demo_001", 0, "")
    assert guard.evaluate("cat class/contract.yaml", "continue") is None


def test_progress_guard_does_not_apply_runs_scope_guard_by_default():
    guard = bash._ProgressGuardState()
    assert guard.evaluate("ls -F runs/", "discover") is None


def test_progress_guard_blocks_runs_root_discovery_in_preflight_workflow():
    guard = bash._ProgressGuardState()
    guard.record_result(
        "cat hepagent_instruction.txt",
        0,
        "Task:\nPrepare a production preflight plan for the canonical chain\nRun only orchestrator preflight",
    )
    blocked = guard.evaluate("ls -F runs/", "discover")
    assert blocked is not None
    assert "top-level 'runs/' discovery" in blocked


def test_progress_guard_allows_scoped_runs_subpath_after_manifest_read():
    guard = bash._ProgressGuardState()
    guard.record_result(
        "cat hepagent_instruction.txt",
        0,
        "Task:\nPrepare a production preflight plan for the canonical chain\nRun only orchestrator preflight",
    )
    manifest_output = (
        "output_report: runs/pipeline_demo_001/orchestrator/preflight.json\n"
        "upstream_data: runs/pipeline_demo_001/class/class_out_tk.dat\n"
    )
    guard.record_result("cat orchestrator/example_manifest.yaml", 0, manifest_output)
    assert guard.evaluate("ls -F runs/pipeline_demo_001/", "inspect scoped path") is None


def test_progress_guard_policy_mode_defaults(monkeypatch):
    guard = bash._ProgressGuardState()
    monkeypatch.setenv("HEPAGENT_POLICY_MODE", "conservative")
    assert guard._limits() == (6, 1, 1, 800)
    monkeypatch.setenv("HEPAGENT_POLICY_MODE", "balanced")
    assert guard._limits() == (10, 2, 2, 1200)
    monkeypatch.setenv("HEPAGENT_POLICY_MODE", "exploratory")
    assert guard._limits() == (16, 3, 3, 1800)


def test_progress_guard_default_mode_is_balanced(monkeypatch):
    guard = bash._ProgressGuardState()
    monkeypatch.delenv("HEPAGENT_POLICY_MODE", raising=False)
    assert guard._limits() == (10, 2, 2, 1200)


def test_progress_guard_no_progress_resets_after_execution(monkeypatch):
    guard = bash._ProgressGuardState()
    monkeypatch.setenv("HEPAGENT_MAX_NO_PROGRESS_STEPS", "1")
    guard.record_result("cat hepagent_instruction.txt", 0, "ok")
    guard.record_result("cat hepagent_instruction.txt", 0, "ok")
    blocked = guard.evaluate("cat hepagent_instruction.txt", "repeat read")
    assert blocked is not None
    guard.record_result("python3 orchestrator/run.py --config x.yaml", 0, "ok")
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


def test_progress_guard_forces_finalize_after_preflight_report_read():
    guard = bash._ProgressGuardState()
    guard.record_result(
        "cat hepagent_instruction.txt",
        0,
        "Task:\nPrepare a production preflight plan for the canonical chain\nRun only orchestrator preflight",
    )
    guard.record_result(
        "cat runs/pipeline_demo_001/orchestrator/orchestrator_preflight.json",
        0,
        '{"qa": {"all_handoffs_valid": false}, "handoff_results": []}',
    )
    blocked = guard.evaluate("ls -F orchestrator/", "keep exploring")
    assert blocked is not None
    assert "preflight report has been read" in blocked


def test_progress_guard_does_not_finalize_after_reading_contract_yaml():
    guard = bash._ProgressGuardState()
    guard.record_result(
        "cat orchestrator/contract.yaml",
        0,
        'provenance_contract:\n  required_fields:\n    - "qa"\n    - "handoff_results"\n',
    )
    assert guard.evaluate("cat AGENTS.md", "continue bootstrap") is None


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


def test_yolo_surfaces_immediate_scaffold_directive_after_scoped_missing_path(monkeypatch):
    bash._reset_progress_guard_for_tests()
    monkeypatch.setenv("HEPAGENT_YOLO", "1")

    # Seed manifest-scoped runs paths first.
    manifest_like_output = (
        "output_report: runs/pipeline_demo_001/orchestrator/orchestrator_preflight.json\n"
        "downstream_manifest: runs/pipeline_demo_001/cosmicic_manifest.yaml\n"
    )
    monkeypatch.setattr(
        bash,
        "execute_bash_command",
        lambda cmd, cwd="": {"output": manifest_like_output, "returncode": 0},
    )
    payload_seed = json.dumps(
        {
            "cmd": "cat orchestrator/example_manifest.yaml",
            "cwd": ".",
            "thought": "read manifest",
        }
    )
    ctx_seed = ToolContext(
        tool_name=getattr(bash.execute_bash_command_with_confirmation, "name", ""),
        tool_call_id="seed-call",
        tool_arguments=payload_seed,
        context=None,
    )
    _ = asyncio.run(bash.execute_bash_command_with_confirmation.on_invoke_tool(ctx_seed, payload_seed))

    # Next, emulate scoped missing path.
    monkeypatch.setattr(
        bash,
        "execute_bash_command",
        lambda cmd, cwd="": {
            "output": "ls: runs/pipeline_demo_001/cosmicic_manifest.yaml: No such file or directory",
            "returncode": 1,
        },
    )
    payload_missing = json.dumps(
        {
            "cmd": "ls runs/pipeline_demo_001/cosmicic_manifest.yaml",
            "cwd": ".",
            "thought": "check missing path",
        }
    )
    ctx_missing = ToolContext(
        tool_name=getattr(bash.execute_bash_command_with_confirmation, "name", ""),
        tool_call_id="missing-call",
        tool_arguments=payload_missing,
        context=None,
    )
    r = asyncio.run(bash.execute_bash_command_with_confirmation.on_invoke_tool(ctx_missing, payload_missing))
    assert r["returncode"] == 2
    assert "manifest-scoped paths are missing under runs/" in r["output"]
    assert "Suggested next command" in r["output"]


def test_yolo_end_to_end_preflight_trajectory_without_path_question(monkeypatch):
    bash._reset_progress_guard_for_tests()
    bash._reset_execution_journal_for_tests()
    monkeypatch.setenv("HEPAGENT_YOLO", "1")

    outputs = {
        "cat hepagent_instruction.txt": (
            "Task:\n"
            "  Prepare a production preflight plan for the canonical chain, but do not run CLASS/CosmicIC/Nyx executors.\n"
            "  5) Run only orchestrator preflight:\n"
            "     python3 orchestrator/run.py --config <your_manifest_path>\n"
        ),
        "cat registry.yaml": "ok",
        "sed -n '1,200p' orchestrator/contract.yaml": "ok",
        "sed -n '1,200p' AGENTS.md": "ok",
        "cat orchestrator/example_manifest.yaml": (
            "mode: production\n"
            "output_report: runs/pipeline_demo_001/orchestrator/orchestrator_preflight.json\n"
            "handoffs:\n"
            "  - downstream_manifest: runs/pipeline_demo_001/cosmicic_manifest.yaml\n"
            "  - downstream_manifest: runs/pipeline_demo_001/nyx_manifest.yaml\n"
        ),
        "ls runs/pipeline_demo_001/cosmicic_manifest.yaml runs/pipeline_demo_001/nyx_manifest.yaml": (
            "ls: runs/pipeline_demo_001/cosmicic_manifest.yaml: No such file or directory\n"
            "ls: runs/pipeline_demo_001/nyx_manifest.yaml: No such file or directory\n"
        ),
        "mkdir -p runs/pipeline_demo_001 runs/pipeline_demo_001/orchestrator": "",
        "python3 orchestrator/run.py --config orchestrator/example_manifest.yaml": (
            "[INFO] JSON report: /tmp/preflight_report.json\n"
            "[INFO] Dashboard URL: http://127.0.0.1:8765/preflight_report.html\n"
            "Traceback ... RuntimeError: Preflight failed. See report: /tmp/preflight_report.json\n"
        ),
        "cat /tmp/preflight_report.json": (
            '{"qa":{"all_handoffs_valid":false},"handoff_results":[{"name":"class_to_cosmicic","ok":false}]}'
        ),
    }

    def fake_exec(cmd, cwd=""):
        if cmd in outputs:
            out = outputs[cmd]
            # Missing manifest files should fail with rc=1.
            if "No such file or directory" in out:
                return {"output": out, "returncode": 1}
            # Preflight failure should return rc=1.
            if "Preflight failed. See report:" in out:
                return {"output": out, "returncode": 1}
            return {"output": out, "returncode": 0}
        return {"output": f"unexpected cmd: {cmd}", "returncode": 1}

    monkeypatch.setattr(bash, "execute_bash_command", fake_exec)

    def invoke(cmd: str, thought: str, call_id: str):
        payload = json.dumps({"cmd": cmd, "cwd": ".", "thought": thought})
        ctx = ToolContext(
            tool_name=getattr(bash.execute_bash_command_with_confirmation, "name", ""),
            tool_call_id=call_id,
            tool_arguments=payload,
            context=None,
        )
        return asyncio.run(bash.execute_bash_command_with_confirmation.on_invoke_tool(ctx, payload))

    assert invoke("cat hepagent_instruction.txt", "read task", "c1")["returncode"] == 0
    assert invoke("cat registry.yaml", "read registry", "c2")["returncode"] == 0
    assert invoke("sed -n '1,200p' orchestrator/contract.yaml", "read contract", "c3")["returncode"] == 0
    assert invoke("sed -n '1,200p' AGENTS.md", "read agents", "c4")["returncode"] == 0
    assert invoke("cat orchestrator/example_manifest.yaml", "read manifest", "c5")["returncode"] == 0

    missing = invoke(
        "ls runs/pipeline_demo_001/cosmicic_manifest.yaml runs/pipeline_demo_001/nyx_manifest.yaml",
        "check downstream manifests",
        "c6",
    )
    assert missing["returncode"] == 2
    assert "manifest-scoped paths are missing under runs/" in missing["output"]
    assert "Suggested next command" in missing["output"]

    assert (
        invoke(
            "mkdir -p runs/pipeline_demo_001 runs/pipeline_demo_001/orchestrator",
            "scaffold in-scope paths",
            "c7",
        )["returncode"]
        == 0
    )
    assert (
        invoke(
            "python3 orchestrator/run.py --config orchestrator/example_manifest.yaml",
            "run preflight",
            "c8",
        )["returncode"]
        == 1
    )
    assert invoke("cat /tmp/preflight_report.json", "read report", "c9")["returncode"] == 0

    # After report read, tool-calling should stop and summarize.
    final_block = invoke("ls orchestrator/", "keep exploring", "c10")
    assert final_block["returncode"] == 0
    assert "provide final summary now" in final_block["output"]
    assert "FINALIZE_NOW" in final_block["output"]


def test_preflight_workflow_allows_intermediate_steps_before_run():
    guard = bash._ProgressGuardState()
    guard.record_result(
        "cat hepagent_instruction.txt",
        0,
        "Task:\nPrepare a production preflight plan for the canonical chain\nRun only orchestrator preflight",
    )
    guard.record_result("cat registry.yaml", 0, "ok")
    guard.record_result("cat orchestrator/contract.yaml", 0, "ok")
    guard.record_result("cat AGENTS.md", 0, "ok")
    # Workflow policy should not hard-pin the exact next command.
    assert guard.evaluate("ls orchestrator/example_manifest.yaml", "inspect manifest path") is None
    assert guard.evaluate("sed -n '1,200p' orchestrator/example_manifest.yaml", "read manifest") is None
    assert guard.evaluate("ls class/", "explore more") is None


def test_preflight_workflow_does_not_block_manifest_reads_after_bootstrap():
    guard = bash._ProgressGuardState()
    guard.record_result(
        "cat hepagent_instruction.txt",
        0,
        "Task:\nPrepare a production preflight plan for the canonical chain\nRun only orchestrator preflight",
    )
    guard.record_result("cat registry.yaml", 0, "ok")
    guard.record_result("cat orchestrator/contract.yaml", 0, "ok")
    guard.record_result("cat AGENTS.md", 0, "ok")
    assert guard.evaluate("ls orchestrator/example_manifest.yaml", "check file") is None
    assert guard.evaluate("cat orchestrator/example_manifest.yaml", "read manifest") is None


def test_preflight_workflow_forces_report_read_after_run():
    guard = bash._ProgressGuardState()
    guard.record_result(
        "cat hepagent_instruction.txt",
        0,
        "Task:\nPrepare a production preflight plan for the canonical chain\nRun only orchestrator preflight",
    )
    guard.record_result(
        "python3 orchestrator/run.py --config orchestrator/example_manifest.yaml",
        1,
        "RuntimeError: Preflight failed. See report: /tmp/preflight.json",
    )
    blocked = guard.evaluate("ls orchestrator/", "explore")
    assert blocked is not None
    assert "Read the preflight JSON report next" in blocked
    assert guard.evaluate("cat /tmp/preflight.json", "read report") is None


def test_preflight_workflow_blocks_disabling_report_ui_flags():
    guard = bash._ProgressGuardState()
    guard.record_result(
        "cat hepagent_instruction.txt",
        0,
        "Task:\nPrepare a production preflight plan for the canonical chain\nRun only orchestrator preflight",
    )
    blocked = guard.evaluate(
        "python3 orchestrator/run.py --config orchestrator/example_manifest.yaml --no-open-report --no-serve-report",
        "run preflight",
    )
    assert blocked is not None
    assert "do not disable report UI" in blocked
    assert guard.evaluate(
        "python3 orchestrator/run.py --config orchestrator/example_manifest.yaml",
        "run preflight",
    ) is None


def test_preflight_workflow_forces_run_after_too_many_reads():
    guard = bash._ProgressGuardState()
    guard.record_result(
        "cat hepagent_instruction.txt",
        0,
        "Task:\nPrepare a production preflight plan for the canonical chain\nRun only orchestrator preflight",
    )
    for _ in range(12):
        guard.record_result("cat registry.yaml", 0, "ok")
    blocked = guard.evaluate("cat AGENTS.md", "keep reading")
    assert blocked is not None
    assert "Run `python3 orchestrator/run.py --config <manifest>` next." in blocked


def test_preflight_workflow_forces_run_once_manifest_is_ready():
    guard = bash._ProgressGuardState()
    guard.record_result(
        "cat hepagent_instruction.txt",
        0,
        "Task:\nPrepare a production preflight plan for the canonical chain\nRun only orchestrator preflight",
    )
    guard.record_result(
        "cat orchestrator/example_manifest.yaml",
        0,
        (
            "mode: production\n"
            "output_report: runs/pipeline_demo_001/orchestrator/orchestrator_preflight.json\n"
            "handoffs:\n"
            "  - name: class_to_cosmicic\n"
            "  - name: cosmicic_to_nyx\n"
        ),
    )
    blocked = guard.evaluate("cat class/contract.yaml", "double-check contract")
    assert blocked is not None
    assert "preflight manifest is ready" in blocked
    assert "run.py --config <manifest>" in blocked


def test_preflight_workflow_blocks_setup_writes_before_preflight_attempt():
    guard = bash._ProgressGuardState()
    guard.record_result(
        "cat hepagent_instruction.txt",
        0,
        "Task:\nPrepare a production preflight plan for the canonical chain\nRun only orchestrator preflight",
    )
    guard.record_result(
        "cat orchestrator/example_manifest.yaml",
        0,
        (
            "mode: production\n"
            "output_report: runs/pipeline_demo_001/orchestrator/orchestrator_preflight.json\n"
            "handoffs:\n"
            "  - name: class_to_cosmicic\n"
            "  - name: cosmicic_to_nyx\n"
        ),
    )
    blocked = guard.evaluate("mkdir -p runs/pipeline_demo_001/class", "setup dirs")
    assert blocked is not None
    assert "preflight manifest is ready" in blocked


def test_scaffold_or_workflow_nudge_blocks_additional_reads():
    guard = bash._ProgressGuardState()
    guard.record_result(
        "cat hepagent_instruction.txt",
        0,
        "Task:\nPrepare a production preflight plan for the canonical chain\nRun only orchestrator preflight",
    )
    # Trigger the workflow "run preflight now" threshold.
    for _ in range(12):
        guard.record_result("cat registry.yaml", 0, "ok")

    # Discover manifest-scoped runs path and then hit missing scoped file.
    guard.record_result(
        "cat orchestrator/example_manifest.yaml",
        0,
        "output_report: runs/pipeline_demo_001/orchestrator/orchestrator_preflight.json\n",
    )
    guard.record_result(
        "ls runs/pipeline_demo_001/cosmicic_manifest.yaml",
        1,
        "ls: runs/pipeline_demo_001/cosmicic_manifest.yaml: No such file or directory",
    )

    blocked = guard.evaluate("cat cosmicic/contract.yaml", "read contract")
    assert blocked is not None
    assert (
        "manifest-scoped paths are missing under runs/" in blocked
        or "enough context has been gathered for preflight workflow" in blocked
    )


def test_preflight_workflow_allows_relative_report_read_when_path_in_output_is_absolute():
    guard = bash._ProgressGuardState()
    guard.record_result(
        "cat hepagent_instruction.txt",
        0,
        "Task:\nPrepare a production preflight plan for the canonical chain\nRun only orchestrator preflight",
    )
    guard.record_result(
        "python3 orchestrator/run.py --config orchestrator/example_manifest.yaml",
        1,
        (
            "RuntimeError: output_report already exists: "
            "/Users/example/project/runs/pipeline_demo_001/orchestrator/orchestrator_preflight.json"
        ),
    )
    # Relative-path read should still be accepted as a valid report-read step.
    assert (
        guard.evaluate(
            "sed -n '1,100p' runs/pipeline_demo_001/orchestrator/orchestrator_preflight.json",
            "read report",
        )
        is None
    )


def test_preflight_workflow_finalize_after_report_read():
    guard = bash._ProgressGuardState()
    guard.record_result(
        "cat hepagent_instruction.txt",
        0,
        "Task:\nPrepare a production preflight plan for the canonical chain\nRun only orchestrator preflight",
    )
    guard.record_result(
        "python3 orchestrator/run.py --config orchestrator/example_manifest.yaml",
        1,
        "RuntimeError: Preflight failed. See report: /tmp/preflight.json",
    )
    guard.record_result(
        "cat /tmp/preflight.json",
        0,
        '{"qa": {"all_handoffs_valid": false}, "handoff_results": []}',
    )
    blocked = guard.evaluate("ls orchestrator/", "continue")
    assert blocked is not None
    assert "provide final summary now" in blocked


def test_scaffold_requirement_does_not_block_report_read_after_preflight():
    guard = bash._ProgressGuardState()
    guard.record_result(
        "cat hepagent_instruction.txt",
        0,
        "Task:\nPrepare a production preflight plan for the canonical chain\nRun only orchestrator preflight",
    )
    guard.record_result(
        "cat orchestrator/example_manifest.yaml",
        0,
        "output_report: runs/pipeline_demo_001/orchestrator/orchestrator_preflight.json\n",
    )
    guard.record_result(
        "ls runs/pipeline_demo_001/cosmicic_manifest.yaml",
        1,
        "ls: runs/pipeline_demo_001/cosmicic_manifest.yaml: No such file or directory",
    )
    # Preflight sets report path; stale scaffold requirement should not block report read.
    guard.record_result(
        "python3 orchestrator/run.py --config orchestrator/example_manifest.yaml",
        1,
        (
            "[INFO] JSON report: /Users/example/project/runs/pipeline_demo_001/orchestrator/orchestrator_preflight.json\n"
            "RuntimeError: Preflight failed. See report: /Users/example/project/runs/pipeline_demo_001/orchestrator/orchestrator_preflight.json"
        ),
    )
    assert (
        guard.evaluate(
            "cat runs/pipeline_demo_001/orchestrator/orchestrator_preflight.json",
            "read report",
        )
        is None
    )


def test_yolo_scaffold_nudge_clears_once_preflight_report_path_exists(monkeypatch):
    bash._reset_progress_guard_for_tests()
    bash._reset_execution_journal_for_tests()
    monkeypatch.setenv("HEPAGENT_YOLO", "1")

    outputs = {
        "cat hepagent_instruction.txt": (
            "Task:\n"
            "  Prepare a production preflight plan for the canonical chain, but do not run CLASS/CosmicIC/Nyx executors.\n"
            "  5) Run only orchestrator preflight:\n"
            "     python3 orchestrator/run.py --config <your_manifest_path>\n"
        ),
        "cat orchestrator/example_manifest.yaml": (
            "mode: production\n"
            "output_report: runs/pipeline_demo_001/orchestrator/orchestrator_preflight.json\n"
        ),
        "ls runs/pipeline_demo_001/cosmicic_manifest.yaml": (
            "ls: runs/pipeline_demo_001/cosmicic_manifest.yaml: No such file or directory\n"
        ),
        "python3 orchestrator/run.py --config orchestrator/example_manifest.yaml": (
            "[INFO] JSON report: /tmp/orchestrator_preflight.json\n"
            "RuntimeError: Preflight failed. See report: /tmp/orchestrator_preflight.json\n"
        ),
        "cat /tmp/orchestrator_preflight.json": (
            '{"qa":{"all_handoffs_valid":false},"handoff_results":[{"name":"class_to_cosmicic","ok":false}]}'
        ),
    }

    def fake_exec(cmd, cwd=""):
        out = outputs.get(cmd, "ok")
        if "No such file or directory" in out or "RuntimeError: Preflight failed" in out:
            return {"output": out, "returncode": 1}
        return {"output": out, "returncode": 0}

    monkeypatch.setattr(bash, "execute_bash_command", fake_exec)

    def invoke(cmd: str, thought: str, call_id: str):
        payload = json.dumps({"cmd": cmd, "cwd": ".", "thought": thought})
        ctx = ToolContext(
            tool_name=getattr(bash.execute_bash_command_with_confirmation, "name", ""),
            tool_call_id=call_id,
            tool_arguments=payload,
            context=None,
        )
        return asyncio.run(bash.execute_bash_command_with_confirmation.on_invoke_tool(ctx, payload))

    assert invoke("cat hepagent_instruction.txt", "read task", "c1")["returncode"] == 0
    assert invoke("cat orchestrator/example_manifest.yaml", "read manifest", "c2")["returncode"] == 0
    scoped_missing = invoke("ls runs/pipeline_demo_001/cosmicic_manifest.yaml", "check path", "c3")
    assert scoped_missing["returncode"] == 2
    assert "manifest-scoped paths are missing under runs/" in scoped_missing["output"]
    assert (
        invoke("python3 orchestrator/run.py --config orchestrator/example_manifest.yaml", "run preflight", "c4")[
            "returncode"
        ]
        == 1
    )
    report_read = invoke("cat /tmp/orchestrator_preflight.json", "read report", "c5")
    assert report_read["returncode"] == 0


def test_finalize_guard_emits_finalize_now_signal_in_tool_output(monkeypatch):
    bash._reset_progress_guard_for_tests()
    monkeypatch.setenv("HEPAGENT_YOLO", "1")

    guard = bash._PROGRESS_GUARD
    guard.record_result(
        "cat hepagent_instruction.txt",
        0,
        "Task:\nPrepare a production preflight plan for the canonical chain\nRun only orchestrator preflight",
    )
    guard.record_result(
        "python3 orchestrator/run.py --config orchestrator/example_manifest.yaml",
        1,
        "RuntimeError: Preflight failed. See report: /tmp/preflight.json",
    )
    guard.record_result(
        "cat /tmp/preflight.json",
        0,
        '{"qa": {"all_handoffs_valid": false}, "handoff_results": []}',
    )

    payload = json.dumps(
        {
            "cmd": "ls orchestrator/",
            "cwd": ".",
            "thought": "continue exploring",
        }
    )
    ctx = ToolContext(
        tool_name=getattr(bash.execute_bash_command_with_confirmation, "name", ""),
        tool_call_id="finalize-call",
        tool_arguments=payload,
        context=None,
    )
    r = asyncio.run(bash.execute_bash_command_with_confirmation.on_invoke_tool(ctx, payload))
    assert r["returncode"] == 0
    assert "FINALIZE_NOW" in r["output"]
