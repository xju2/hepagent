from pathlib import Path
import subprocess


def read_markdown(path: str) -> str:
    """Read a markdown instruction file."""
    return Path(path).read_text()


def run_shell_command(cmd: str) -> str:
    """Run a shell command and return stdout/stderr."""
    proc = subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        text=True
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"Command failed: {cmd}\n"
            f"stdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}"
        )
    return proc.stdout


def report_status(message: str) -> None:
    """Report progress (could later map to logging / Slack / Indico)."""
    print(message)
