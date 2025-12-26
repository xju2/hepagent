"""REPL for bash agent."""

import asyncio
from agents import run_demo_loop
from hepagent.agents.bash import create


async def main() -> None:
    agent = create()
    await run_demo_loop(agent)


if __name__ == "__main__":
    asyncio.run(main())
