from agents import RunContextWrapper, function_tool
from hepagent.agents.common import AgentContext
from hepagent.helpers import get_agent_dir, read_md


@function_tool
def update_memory(ctx: RunContextWrapper[AgentContext], category: str, observation: str, correction: str = "") -> str:
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
