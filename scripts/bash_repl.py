"""REPL for bash agent."""

import asyncio

from hepagent.agents.bash import create
from hepagent.agents.repl import run_demo_loop


async def main() -> None:
    agent = create()
    await run_demo_loop(agent, stream=True, context=None, max_turns=20)


if __name__ == "__main__":
    asyncio.run(main())
