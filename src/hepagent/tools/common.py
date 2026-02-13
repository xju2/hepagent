import re
import textwrap
from pathlib import Path

from agents import RunContextWrapper, function_tool
from hepagent.agents.common import AgentContext
from hepagent.helpers import read_md


def _get_skill_dir(skill_name: str) -> Path:
    """Internal helper to resolve paths dynamically."""
    from hepagent.helpers import get_agent_dir

    return get_agent_dir() / "skills" / skill_name


def _get_memory_path() -> Path:
    """Internal helper to resolve memory file path."""
    from hepagent.helpers import get_agent_dir

    return get_agent_dir() / "storage" / "MEMORY.md"


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
    file_path = _get_memory_path()
    new_entry = f"- **{category}:** {observation}"
    if correction:
        new_entry += f" | **Correction:** {correction}"

    with open(file_path, "a", encoding="utf-8") as f:
        f.write(f"{new_entry}\n")

    return "Memory successfully updated."


@function_tool
def load_skill_details(ctx: RunContextWrapper[AgentContext], skill_name: str) -> str:
    """Activates a specific skill and loads its SOP and Logbook.

    Args:
        skill_name: The identifier of the skill (e.g., 'nyx').
    """

    skill_dir = _get_skill_dir(skill_name)
    skill_file = skill_dir / "SKILL.md"
    resource_dir = skill_dir / "resources"

    if not skill_file.exists():
        ctx.context.active_skill = None  # Clear active skill if not found
        return f"Error: Skill '{skill_name}' does not exist."

    # Set the active skill in the context
    ctx.context.active_skill = skill_name

    # 1. Get the main instructions (stripping YAML)
    raw_content = read_md(skill_file)
    instruction_body = re.sub(r"^---.*?---", "", raw_content, flags=re.DOTALL).strip()

    # 2. Map available resources
    resources = []
    if resource_dir.exists():
        resources = [f"- {f.stem}" for f in resource_dir.glob("*.md")]

    resource_list = "\n".join(resources) if resources else "No supplementary resources available."

    logbook_content = read_md(skill_dir / "LOGBOOK.md")

    instruction_content = textwrap.dedent(f"""
        # FULL INSTRUCTIONS FOR {skill_name.upper()}
        {instruction_body}

        # LESSONS LEARNED (LOGBOOK)
        {logbook_content if logbook_content else "No previous logs for this skill."}

        # AVAILABLE RESOURCE MANUALS
        (Use 'read_resource("{skill_name}", "resource_name")' to read these)
        {resource_list}
    """).strip()

    return f"Context updated: Now using the '{skill_name}' skill set.\n\n" + instruction_content


@function_tool
def read_resource(ctx: RunContextWrapper[AgentContext], resource_name: str) -> str:
    """Reads a specific technical manual (e.g., 'TF', 'IC') for the currently active skill.

    Args:
        resource_name: The name of the markdown file in the resources folder (without .md).
    """
    # resource_name could be "TF", we append .md
    skill_name = ctx.context.active_skill
    if not skill_name:
        return "Error: No active skill. Please load a skill first using 'load_skill_details'."

    res_path = _get_skill_dir(skill_name) / "resources" / f"{resource_name}.md"
    if not res_path.exists():
        return f"Resource {resource_name} not found in {skill_name}."
    return read_md(res_path)


@function_tool
def ask_user_for_info(ctx: RunContextWrapper[AgentContext], prompt: str) -> str:
    """
    Pauses execution to ask the user for missing information or clarification.
    Use this when a required parameter (like a directory path) is missing.
    Ask one parameter at a time, and be specific in the prompt to guide the user.

    Args:
        prompt: The question to display to the user.

    Returns:
        str: The user's input exactly as entered.
    """
    user_input = input(f"{prompt}: ")
    return user_input
