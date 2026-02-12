import pathlib
from types import SimpleNamespace

import hepagent.agent_helpers as agent_helpers
from hepagent.agents.common import AgentContext


def _write(path: pathlib.Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_get_instructions_builds_prompt(monkeypatch, tmp_path):
    agents_dir = tmp_path / ".agents"

    _write(agents_dir / "storage" / "MEMORY.md", "user memory")
    _write(agents_dir / "skills" / "demo" / "OPERATION.md", "operation rules")
    _write(agents_dir / "skills" / "demo" / "LOGBOOK.md", "logbook entries")

    monkeypatch.setattr(agent_helpers, "get_agent_dir", lambda: agents_dir)
    loader = agent_helpers.AgentManifestLoader()

    ctx = SimpleNamespace(context=AgentContext(agent_name="demo"))
    prompt = loader.get_instructions(ctx, agent=None)

    assert "# OPERATIONAL RULES" in prompt
    assert "operation rules" in prompt
    assert "# TECHNICAL LESSONS LEARNED" in prompt
    assert "logbook entries" in prompt
    assert "# SHARED USER PREFERENCES & CONTEXT" in prompt
    assert "user memory" in prompt
