import sys
import textwrap
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


@pytest.fixture
def without_web_extras():
    """Run a block with the `web` extra's dependencies unimportable, as in CI.

    A function-local `import fastapi` is fine; a function-local import of a
    *module that imports FastAPI* is not, and reading the import statements in
    `web/` cannot tell the two apart. So reproduce CI instead: block the extras
    and run the code. See invariant 1 of `docs/WEB.md`.

    `plan_api` is dropped from `sys.modules` too — otherwise a cached copy left
    behind by the tests that do have FastAPI would satisfy the import and hide
    the bug, making this pass or fail depending on test ordering.
    """
    blocked = {"fastapi", "chainlit", "uvicorn", "starlette"}

    class Blocker:
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in blocked:
                raise ModuleNotFoundError(f"No module named {name!r}", name=name)
            return None

    @contextmanager
    def blocking():
        stashed = {n: m for n, m in sys.modules.items() if n.split(".")[0] in blocked}
        stashed["hepagent.web.plan_api"] = sys.modules.get("hepagent.web.plan_api")
        for name in stashed:
            sys.modules.pop(name, None)

        blocker = Blocker()
        sys.meta_path.insert(0, blocker)
        try:
            yield
        finally:
            sys.meta_path.remove(blocker)
            for name, module in stashed.items():
                if module is not None:
                    sys.modules[name] = module

    return blocking


@pytest.fixture(scope="session")
def agent_registry_temp(tmp_path_factory):
    """Creates a persistent but temporary .agents directory for the whole session."""
    # Create a unique temp folder for this test session
    base_dir = tmp_path_factory.mktemp("agent_registry")
    agents_dir = base_dir / ".agents"

    # Setup common directory (for ETHICS, SOUL, OPERATION)
    common_dir = agents_dir / "common"
    common_dir.mkdir(parents=True)
    (common_dir / "ETHICS.md").write_text(
        textwrap.dedent("""\
        - Always prioritize user safety and well-being.
        - Do not engage in harmful activities or provide assistance for them.
        - Respect user privacy and confidentiality.
    """)
    )
    (common_dir / "IDENTITY.md").write_text(
        textwrap.dedent("""\
        - You are a skilled assistant with a focus on scientific research and data analysis.
        - Your vibe is professional, curious, and methodical.
    """)
    )
    (common_dir / "OPERATION.md").write_text(
        textwrap.dedent("""\
        - Always verify the success of a tool execution before proceeding.
        - If a tool fails, log the error and the corrective insight in the logbook.
        - When a user requests a skill, check the catalog and load the relevant details execution.
    """)
    )

    # Setup Nyx Skill
    skills_dir = agents_dir / "skills" / "nyx"
    skills_dir.mkdir(parents=True)

    # Create a dummy SKILL.md with frontmatter
    skill_md = skills_dir / "SKILL.md"
    skill_md.write_text(
        textwrap.dedent("""\
        ---
        name: nyx
        description: Test Nyx simulation skill.
        ---
        # Nyx Instructions
        Step 1: Setup directory.
        Step 2: Run simulation.
    """)
    )

    # Create an empty LOGBOOK.md
    (skills_dir / "LOGBOOK.md").write_text("# LOGBOOK\n")

    # Setup Global Storage
    storage_dir = agents_dir / "storage"
    storage_dir.mkdir()
    (storage_dir / "MEMORY.md").write_text("Global shared memory.")

    return agents_dir


@pytest.fixture(autouse=True)
def mock_agent_env(agent_registry_temp):
    """Automatically patches get_agent_dir for every test to use the session registry."""
    # We patch the low-level helper.
    # Because you used late-binding (import inside function), this works perfectly!
    with patch("hepagent.helpers.get_agent_dir", return_value=agent_registry_temp):
        yield agent_registry_temp


@pytest.fixture(autouse=True)
def fake_api_keys(monkeypatch):
    """Provide deterministic fake API keys so provider-dependent tests pass in CI."""
    monkeypatch.setenv("CBORG_API_KEY", "test-cborg-api-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-api-key")
    monkeypatch.setenv("AMSC_API_KEY", "test-amsc-api-key")


JFC_PROMPT = "Measure the Z->bb cross section in 140/fb of ATLAS Run 2 data."


@pytest.fixture
def jfc_plan():
    """The shipped seven-node measurement plan, instantiated for a test analysis.

    Tests that exercise the JFC runtime use this rather than a hand-built plan:
    it is the structure users actually get, so a template change that would break
    the runtime shows up here.
    """
    from hepagent.plan.templates import instantiate

    return instantiate(
        "jfc-measurement",
        analysis_name="demo",
        analysis_type="measurement",
        physics_prompt=JFC_PROMPT,
    )


@pytest.fixture
def jfc_analysis(tmp_path, jfc_plan):
    """An analysis root carrying `plan.json` and `prompt.md`, but no artifacts yet."""
    from hepagent.plan.store import save_plan

    root = tmp_path / "demo"
    root.mkdir()
    (root / "prompt.md").write_text(f"# Physics Prompt\n\n{JFC_PROMPT}\n", encoding="utf-8")
    save_plan(root, jfc_plan)
    return root
