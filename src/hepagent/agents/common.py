from dataclasses import dataclass


@dataclass
class AgentContext:
    agent_name: str
    active_skill: str | None = None  # Tracks which SKILL.md is 'open'
