import re
import textwrap

from agents import RunContextWrapper, function_tool
from hepagent.agents.common import AgentContext
from hepagent.helpers import get_agent_dir, read_md


@function_tool
def update_memory(
    ctx: RunContextWrapper[AgentContext], category: str, observation: str, correction: str = ""
) -> str:
    """
    Updates the agent's long-term memory to prevent repeating errors or store facts.

    Args:
        ctx: The context wrapper containing the agent's context.
        category: Either 'Corrective Insight' or 'Preference'
        observation: What happened or what was learned.
        correction: The specific action to take next time to avoid the error.
    """
    file_path = get_agent_dir() / "storage" / "MEMORY.md"
    new_entry = f"- **{category}:** {observation}"
    if correction:
        new_entry += f" | **Correction:** {correction}"

    with open(file_path, "a") as f:
        f.write(f"{new_entry}\n")

    return "Memory successfully updated."


@function_tool
def load_skill_details(ctx: RunContextWrapper[AgentContext], skill_name: str) -> str:
    """
    Loads the full SOP and identifies available resource manuals for a specific skill.
    Use this when you have identified a skill in the catalog that matches the user's task.
    """
    skill_dir = get_agent_dir() / "skills" / skill_name
    skill_file = skill_dir / "SKILL.md"
    resource_dir = skill_dir / "resources"

    if not skill_file.exists():
        return f"Error: Skill '{skill_name}' does not exist in the registry."

    # 1. Get the main instructions (stripping YAML)
    raw_content = read_md(skill_file)
    instruction_body = re.sub(r"^---.*?---", "", raw_content, flags=re.DOTALL).strip()

    # 2. Map available resources
    resources = []
    if resource_dir.exists():
        resources = [f"- {f.stem}" for f in resource_dir.glob("*.md")]

    resource_list = "\n".join(resources) if resources else "No supplementary resources available."

    return textwrap.dedent(f"""
        # FULL INSTRUCTIONS FOR {skill_name.upper()}
        {instruction_body}

        # AVAILABLE RESOURCE MANUALS
        (Use 'read_resource("{skill_name}", "resource_name")' to read these)
        {resource_list}
    """).strip()


@function_tool
def read_resource(ctx: RunContextWrapper[AgentContext], skill_name: str, resource_name: str) -> str:
    """Reads a specific resource file (e.g., 'TF.md') for a skill."""
    # resource_name could be "TF", we append .md
    res_path = get_agent_dir() / "skills" / skill_name / "resources" / f"{resource_name}.md"
    if not res_path.exists():
        return f"Resource {resource_name} not found in {skill_name}."
    return read_md(res_path)
