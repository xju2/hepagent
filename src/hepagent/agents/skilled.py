from agents import Agent
from hepagent.agent_helpers import AgentManifestLoader, update_logbook, update_user_profile
from hepagent.agents.bash import execute_bash_command_with_confirmation
from hepagent.agents.common import AgentContext
from hepagent.model_providers import get_model_provider
from hepagent.tools.common import (
    ask_user_for_info,
    load_skill_details,
    read_resource,
    wait_for_slurm_job_completion,
)
from hepagent.tools.nyx.transfer_function import create_transfer_function
from hepagent.tools.tmux import (
    request_slurm_interactive,
    tmux_capture_pane,
    tmux_create_session,
    tmux_kill_session,
    tmux_list_sessions,
    tmux_send_keys,
    tmux_wait_for_pattern,
)


def create(
    model_provider: str = "cborg",
    model_name: str | None = None,
) -> Agent[AgentContext]:
    # 1. Initialize the loader for a specific soldier
    # It will look in .agents/skills/"agent_name"/ for SOUL.md and WORLD.md
    loader = AgentManifestLoader()

    agent = Agent[AgentContext](
        name="Skilled Agent",
        instructions=loader.get_instructions,
        model=get_model_provider(model_provider=model_provider, model_name=model_name),
        tools=[
            execute_bash_command_with_confirmation,  # The "Bash Command Execution" tool
            update_logbook,  # The "Learned Lessons Logging" tool
            update_user_profile,  # The "User Profile Memory" tool
            load_skill_details,  # The "Skill Discovery" tool
            read_resource,  # The "Knowledge Retrieval" tool
            ask_user_for_info,  # The "User Interaction" tool
            create_transfer_function,
            wait_for_slurm_job_completion,
            tmux_list_sessions,
            tmux_create_session,
            tmux_send_keys,
            tmux_capture_pane,
            tmux_wait_for_pattern,
            tmux_kill_session,
            request_slurm_interactive,
        ],
    )
    return agent


async def main(agent_name: str = "nyx"):
    from agents import Runner, SQLiteSession

    task_prompt = "List the files in the current directory and tell me how many there are."
    agent = create()
    context = AgentContext(agent_name=agent_name)
    # In-memory database (lost when process ends)
    session = SQLiteSession("user_123")
    result = await Runner.run(agent, task_prompt, context=context, session=session)
    print(result.final_output)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
