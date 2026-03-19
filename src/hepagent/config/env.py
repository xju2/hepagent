from dataclasses import dataclass
from functools import cached_property

from hepagent.helpers import get_env_var


@dataclass(frozen=True)
class HepAgentEnvConfig:
    """Configuration for HepAgent environment variables."""

    @cached_property
    def output_word_limit(self) -> int:
        """Get the output word limit from environment variable or env_vars.toml."""
        return get_env_var("HEPAGENT_OUTPUT_WORD_LIMIT", dtype=int)

    @cached_property
    def yolo_mode(self) -> bool:
        """Check if YOLO mode is enabled via environment variable or env_vars.toml."""
        return get_env_var("HEPAGENT_YOLO", dtype=bool)

    @cached_property
    def use_mlflow_tracing(self) -> bool:
        """Check if MLflow tracing is enabled via environment variable or env_vars.toml."""
        return get_env_var("HEPAGENT_USE_MLFLOW_Tracing", dtype=bool)


env_config = HepAgentEnvConfig()
