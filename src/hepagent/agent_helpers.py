import textwrap
from typing import Literal

from agents import Agent, RunContextWrapper, Usage, function_tool
from hepagent.agents.common import AgentContext
from hepagent.helpers import ensure_user_profile_file, extract_yaml, get_user_profile_path, read_md
from hepagent.token_costs import calculate_cost


def _read_user_profile_if_available() -> str:
    """Best-effort USER.md load with opportunistic create when writable."""
    profile_path = get_user_profile_path()

    if not profile_path.exists():
        try:
            ensure_user_profile_file()
        except OSError:
            return ""

    try:
        return read_md(profile_path)
    except OSError:
        return ""


def print_usage(usage: Usage, model_name: str = "") -> None:
    """Print usage statistics and cost information.

    Args:
        usage: Usage object from result.context_wrapper.usage
        model_name: Optional model name to calculate accurate costs
    """
    print("\n=== Usage ===")
    print(f"Input tokens: {usage.input_tokens}")
    print(f"Output tokens: {usage.output_tokens}")
    print(f"Total tokens: {usage.total_tokens}")
    print(f"Requests: {usage.requests}")
    for i, request in enumerate(usage.request_usage_entries):
        print(f"  {i + 1}: {request.input_tokens} input, {request.output_tokens} output")

    # Calculate and print cost
    if model_name:
        cost = calculate_cost(usage, model_name)
        print("\n=== Cost ===")
        print(f"Model: {model_name}")
        print(f"Total cost: ${cost:.4f}")
    else:
        print("\n(Provide model_name parameter to calculate cost)")


@function_tool
def update_logbook(
    ctx: RunContextWrapper[AgentContext],
    category: Literal["Corrective Insight", "Technical Error", "Preference"],
    observation: str,
    correction: str = "",
) -> str:
    """Records a lesson learned into the current skill's LOGBOOK.md.

    Args:
        category: The type of insight being recorded.
        observation: Description of the error or user preference.
        correction: The specific action to take next time to avoid the issue.
    """
    from hepagent.helpers import get_agent_dir

    skill_name = ctx.context.active_skill or "general"  # Use 'general' if no active skill
    skill_path = get_agent_dir() / "skills" / skill_name

    # Ensure directory exists, or default to a common path
    if not skill_path.exists():
        skill_path = get_agent_dir() / "common"

    file_path = skill_path / "LOGBOOK.md"

    # Format the entry with a timestamp or clean bullet
    new_entry = f"- **{category}:** {observation}"
    if correction:
        new_entry += f" | **Correction:** {correction}"

    with open(file_path, "a", encoding="utf-8") as f:
        f.write(f"{new_entry}\n")

    return f"Insight recorded in {skill_name} logbook."


def _append_note_to_user_section(profile_path, section_title: str, note: str) -> bool:
    """Append a bullet note under a USER.md section. Returns True when a write happens."""
    content = profile_path.read_text(encoding="utf-8")
    note_clean = note.strip()
    if not note_clean:
        return False

    bullet = f"- {note_clean}"
    existing = [line.strip().lower() for line in content.splitlines() if line.strip()]
    if bullet.lower() in existing:
        return False

    lines = content.splitlines()
    header = f"## {section_title}"

    start_idx = None
    for idx, line in enumerate(lines):
        if line.strip() == header:
            start_idx = idx
            break

    if start_idx is None:
        if lines and lines[-1].strip() != "":
            lines.append("")
        lines.extend([header, bullet, ""])
        profile_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        return True

    end_idx = len(lines)
    for idx in range(start_idx + 1, len(lines)):
        if lines[idx].lstrip().startswith("## "):
            end_idx = idx
            break

    def _is_placeholder_bullet(line: str) -> bool:
        stripped = line.strip()
        if not stripped.startswith("-"):
            return False
        tail = stripped[1:].strip()
        if not tail:
            return True
        # Treat punctuation-only bullets as placeholders (for example: "- / -").
        return not any(char.isalnum() for char in tail)

    # Drop placeholder bullets in the target section once we have
    # a real note to persist.
    section_body_start = start_idx + 1
    placeholder_indexes = []
    for idx in range(section_body_start, end_idx):
        if _is_placeholder_bullet(lines[idx]):
            placeholder_indexes.append(idx)

    if placeholder_indexes:
        for idx in reversed(placeholder_indexes):
            del lines[idx]
        end_idx -= len(placeholder_indexes)

    insert_at = end_idx
    while insert_at > start_idx + 1 and lines[insert_at - 1].strip() == "":
        insert_at -= 1
    lines.insert(insert_at, bullet)

    profile_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return True


@function_tool
def update_user_profile(
    ctx: RunContextWrapper[AgentContext],
    category: Literal[
        "Preference", "Goal", "Project", "Expertise", "Interest", "Constraint", "Other"
    ],
    observation: str,
) -> str:
    """Record durable user information in ~/.hepagent/USER.md.

    Use this only when new, meaningful user context is learned and not already captured.
    """
    del ctx  # Context currently unused; kept for function_tool signature consistency.

    section_map = {
        "Preference": "Preferences",
        "Goal": "Goals",
        "Project": "Projects",
        "Expertise": "Expertise",
        "Interest": "Interests",
        "Constraint": "Constraints",
        "Other": "Preferences",
    }

    ensure_user_profile_file()
    profile_path = get_user_profile_path()
    section = section_map[category]
    did_write = _append_note_to_user_section(profile_path, section, observation)
    if not did_write:
        return "No update needed: note is empty or already captured in USER.md."
    return f"USER.md updated under '{section}'."


class AgentManifestLoader:
    def __init__(self):
        from hepagent.helpers import get_agent_dir

        self.agents_dir = get_agent_dir()

        # Paths to specific modules
        self.common_path = self.agents_dir / "common"
        self.storage_path = self.agents_dir / "storage"

        # Expose function tool for agent usage
        self.update_logbook = update_logbook

    def get_instructions(
        self, context: RunContextWrapper[AgentContext], agent: Agent[AgentContext]
    ) -> str:
        """Assembles the full system prompt from the .agents registry."""
        del context, agent

        ethics = read_md(self.common_path / "ETHICS.md")  # Guardrail guidelines
        identity = read_md(self.common_path / "IDENTITY.md")  # Personality & Vibe
        operation = read_md(self.common_path / "OPERATION.md")  # Operational rules
        memory = read_md(self.storage_path / "MEMORY.md")  # Project/User preferences, etc.
        user_profile = _read_user_profile_if_available()

        catalog = self.get_skill_catalog()

        components = []
        if identity:
            components.append(f"# IDENTITY\n{identity}")

        if operation:
            components.append(f"# OPERATIONAL RULES\n{operation}")

        if ethics:
            components.append(f"# GUARDRAIL GUIDELINES\n{ethics}")

        if memory:
            components.append(f"# SHARED MEMORY\n{memory}")

        if user_profile:
            components.append(f"{user_profile}")

        if catalog:
            components.append(
                f"# AVAILABLE SKILLS\nYou have access to the following specialized skills."
                f" Use `load_skill_details` to activate one:\n{catalog}"
            )

        components.append(
            textwrap.dedent("""# CRITICAL RULE: SELF-IMPROVEMENT
             Whenever you encounter an error, a tool failure, or a user
            correction, you MUST call `update_logbook` to record the corrective insight
            so you do not repeat the mistake.""")
        )

        components.append(
            textwrap.dedent("""# CRITICAL RULE: USER PROFILE MAINTENANCE
            When the user reveals significant new durable context (preferences, goals,
            projects, expertise, interests, or constraints), call `update_user_profile`
            to keep USER.md current. Keep entries concise and avoid duplicates.""")
        )

        # Filter out empty components and join
        return "\n\n".join(components)

    def get_skill_catalog(self) -> str:
        """Scans all skill directories and returns their YAML descriptions."""
        catalog = []
        skills_root = self.agents_dir / "skills"
        if not skills_root.exists():
            return ""  # No skills available

        for skill_dir in skills_root.iterdir():
            if skill_dir.is_dir():
                skill_file = skill_dir / "SKILL.md"
                if skill_file.exists():
                    meta, _ = extract_yaml(skill_file)
                    skill_name = meta.get("name", skill_dir.name)
                    desc = meta.get("description", "No description provided.")
                    catalog.append(f"- **{skill_name}**: {desc}")

        return "\n".join(catalog)
