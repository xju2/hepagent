import re
import textwrap
from typing import Literal

import yaml

from agents import Agent, RunContextWrapper, Usage, function_tool
from hepagent.agents.common import AgentContext
from hepagent.helpers import read_md
from hepagent.token_costs import calculate_cost


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
        ethics = read_md(self.common_path / "ETHICS.md")  # Guardrail guidelines
        identity = read_md(self.common_path / "IDENTITY.md")  # Personality & Vibe
        operation = read_md(self.common_path / "OPERATION.md")  # Operational rules
        memory = read_md(self.storage_path / "MEMORY.md")  # Project/User preferences, etc.

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

        # Load agent-specific instructions if an agent name is set in context
        agent_name = getattr(getattr(context, "context", None), "agent_name", None)
        if agent_name:
            agent_instructions = self.get_agent_instructions(agent_name)
            if agent_instructions:
                components.append(f"# AGENT ROLE\n{agent_instructions}")

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
                    meta = self._extract_yaml(skill_file)
                    skill_name = meta.get("name", skill_dir.name)
                    desc = meta.get("description", "No description provided.")
                    catalog.append(f"- **{skill_name}**: {desc}")

        return "\n".join(catalog)

    def get_agent_catalog(self) -> list[dict[str, str]]:
        """Scans all agent directories and returns their metadata.

        Returns:
            A list of dicts with 'name' and 'description' keys for each agent.
        """
        agents = []
        agents_root = self.agents_dir / "agents"
        if not agents_root.exists():
            return agents

        for agent_dir in sorted(agents_root.iterdir()):
            if agent_dir.is_dir():
                agent_file = agent_dir / "AGENT.md"
                if agent_file.exists():
                    meta = self._extract_yaml(agent_file)
                    agents.append(
                        {
                            "name": meta.get("name", agent_dir.name),
                            "description": meta.get("description", "No description provided."),
                        }
                    )

        return agents

    def get_agent_instructions(self, agent_name: str) -> str:
        """Loads agent-specific instructions from the agents registry.

        Args:
            agent_name: The name of the agent configuration to load.

        Returns:
            The agent-specific instruction text, or an empty string if not found.
        """
        agent_file = self.agents_dir / "agents" / agent_name / "AGENT.md"
        return read_md(agent_file)

    def _extract_yaml(self, path):
        content = read_md(path)
        match = re.search(r"^---\s*(.*?)\s*---", content, re.DOTALL)
        return yaml.safe_load(match.group(1)) if match else {}
