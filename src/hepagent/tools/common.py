from agents import function_tool


@function_tool
def update_memory(file_path: str, category: str, observation: str, correction: str = "") -> str:
    """
    Updates the agent's long-term memory to prevent repeating errors or store facts.

    Args:
        param file_path: Path to the MEMORY.md file to update.
        param category: Either 'Corrective Insight' or 'Preference'
        param observation: What happened or what was learned.
        param correction: The specific action to take next time to avoid the error.
    """
    new_entry = f"- **{category}:** {observation}"
    if correction:
        new_entry += f" | **Correction:** {correction}"

    with open(file_path, "a") as f:
        f.write(f"{new_entry}\n")

    return "Memory successfully updated."
