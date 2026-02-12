import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


@pytest.fixture
def mock_agent_dir(tmp_path, monkeypatch):
    """Creates a temporary .agents directory structure for testing."""
    agents_dir = tmp_path / ".agents"
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

    # Mock the get_agent_dir helper to point to this temp directory
    import hepagent.helpers

    monkeypatch.setattr(hepagent.helpers, "get_agent_dir", lambda: agents_dir)

    return agents_dir
