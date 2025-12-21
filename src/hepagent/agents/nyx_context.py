from dataclasses import dataclass


@dataclass
class NyxContext:
    agent_workdir: str
    cosmicic_code_dir: str
    nyx_code_dir: str
    gimlet2_code_dir: str
