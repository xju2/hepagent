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
