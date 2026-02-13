from dataclasses import dataclass

OUTPUT_TRUNCATE_LENGTH = 1000  # Max length of command output to display in the UI


@dataclass
class AgentContext:
    agent_name: str
    active_skill: str | None = None  # Tracks which SKILL.md is 'open'
