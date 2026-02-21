import hepagent.agents.bash as bash


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
    # Check that we have a bash command execution tool
    assert len(captured["tools"]) == 1
    tool = captured["tools"][0]
    assert hasattr(tool, "name")
    assert "execute_bash_command" in tool.name
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
    second = guard.evaluate("cat registry.yaml", "bootstrap read")

    assert first is None
    assert second is None


def test_progress_guard_requires_clarification_after_missing_path(monkeypatch):
    guard = bash._ProgressGuardState()
    guard.record_result("ls -F missing/path", 1, "ls: missing/path: No such file or directory")
    blocked = guard.evaluate("sed -n '1,50p' AGENTS.md", "read again")
    assert blocked is not None
    assert "missing-path error" in blocked


def test_progress_guard_policy_mode_defaults(monkeypatch):
    guard = bash._ProgressGuardState()
    monkeypatch.setenv("HEPAGENT_POLICY_MODE", "conservative")
    assert guard._limits() == (4, 1, 1, 800)
    monkeypatch.setenv("HEPAGENT_POLICY_MODE", "balanced")
    assert guard._limits() == (8, 2, 2, 1200)
    monkeypatch.setenv("HEPAGENT_POLICY_MODE", "exploratory")
    assert guard._limits() == (14, 3, 3, 1800)
