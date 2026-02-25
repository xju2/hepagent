"""REPL for bash agent."""

import asyncio
import os

from hepagent.agents.bash import create
from hepagent.agents.repl import run_demo_loop


def _get_max_turns() -> int:
    raw = os.getenv("HEPAGENT_MAX_TURNS", "").strip()
    if not raw:
        return 40
    try:
        parsed = int(raw)
    except ValueError:
        return 40
    return max(1, parsed)


async def main() -> None:
    agent = create()
    await run_demo_loop(agent, stream=True, context=None, max_turns=_get_max_turns())


if __name__ == "__main__":
    asyncio.run(main())
