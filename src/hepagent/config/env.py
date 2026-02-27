from dataclasses import dataclass
from functools import cached_property

from hepagent.helpers import get_env_var


@dataclass(frozen=True, slots=True)
class HepAgentEnvConfig:
    """Configuration for HepAgent environment variables."""

    @cached_property
    def output_word_limit(self) -> int:
        """Get the output word limit from environment variable or env_vars.toml."""
        return get_env_var("HEPAGENT_OUTPUT_WORD_LIMIT", int)

    @cached_property
    def yolo_mode(self) -> bool:
        """Check if YOLO mode is enabled via environment variable or env_vars.toml."""
        return get_env_var("HEPAGENT_YOLO", bool)


env_config = HepAgentEnvConfig()
