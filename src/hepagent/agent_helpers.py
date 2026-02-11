from agents import Agent, RunContextWrapper, Usage
from hepagent.agents.common import AgentContext
from hepagent.helpers import get_agent_dir, read_md
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


class AgentManifestLoader:
    def __init__(self):
        self.agents_dir = get_agent_dir()

        # Paths to specific modules
        self.common_path = self.agents_dir / "common"
        self.storage_path = self.agents_dir / "storage"

    def get_instructions(
        self, context: RunContextWrapper[AgentContext], agent: Agent[AgentContext]
    ) -> str:
        """Assembles the full system prompt from the .agents registry."""
        agent_path = self.agents_dir / "skills" / context.agent_name
        if not agent_path.exists():
            raise FileNotFoundError(f"Agent manifest directory not found: {agent_path}")
        # soul = read_md(agent_path / "SOUL.md")
        # world = read_md(agent_path / "WORLD.md")
        # ethics = read_md(self.common_path / "ETHICS.md")
        memory = read_md(self.storage_path / "MEMORY.md")
        operational = read_md(agent_path / "OPERATION.md")

        # Building a structured prompt
        components = [
            "# OPERATIONAL RULES",
            operational,
            # "# IDENTITY & PERSONALITY", soul,
            # "# OPERATIONAL BOUNDARIES", ethics,
            # "# DOMAIN KNOWLEDGE (HEP)", world,
            "# LONG-TERM MEMORY & LESSONS",
            memory,
        ]

        # Filter out empty components and join
        return "\n\n".join([c for c in components if c])
