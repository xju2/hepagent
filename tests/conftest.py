import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


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
