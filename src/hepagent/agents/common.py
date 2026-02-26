from dataclasses import dataclass

# Max length of bash command output in words,
# can be overridden by HEPAGENT_OUTPUT_WORD_LIMIT env var
# str.split() is used to count words.
OUTPUT_TRUNCATE_LENGTH = 10_000


@dataclass
class AgentContext:
    agent_name: str
    active_skill: str | None = None  # Tracks which SKILL.md is 'open'
