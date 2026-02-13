from unittest.mock import patch

import pytest

from agents import RunContextWrapper, Runner
from hepagent.agents.common import AgentContext
from hepagent.agents.skilled import create as create_skilled_agent
from hepagent.tools.common import ask_user_for_info


@pytest.mark.asyncio
@pytest.mark.allow_call_model_methods  # Allow actual model calls for this test
async def test_skilled_agent_skill_cycle(mock_agent_env):
    from hepagent.helpers import get_agent_dir

    print(f"\n[DEBUG] Mock Dir: {mock_agent_env}")
    print(f"[DEBUG] Helper returns: {get_agent_dir()}")

    # 1. Initialize Agent and Context
    agent = create_skilled_agent()
    context = AgentContext(agent_name="Scientific Researcher")

    # 2. Verify Skill Loading (State Management)
    # The agent should call load_skill_details because it sees 'nyx' in the catalog
    task = "I need to start a Nyx project."
    result = await Runner.run(agent, task, context=context)
    print(f"\n[DEBUG] Agent Output:\n{result.final_output}")
    print(f"\n[DEBUG] Tools Used:\n{result.raw_responses}")

    # 3. Verify Logbook Tool (Structured Input)
    # We simulate a failure and check if the agent logs it
    error_task = "The bash command failed with error 'Timeout'. Log this immediately."
    await Runner.run(agent, error_task, context=context)

    # Basic sanity check that the context is correctly initialized and attached.
    assert context.agent_name == "Scientific Researcher"

    # Check the actual file in the mock directory
    logbook_file = mock_agent_env / "skills" / "nyx" / "LOGBOOK.md"
    log_content = logbook_file.read_text()

    assert "Timeout" in log_content


def test_ask_user_for_info():
    """Test that ask_user_for_info properly collects and returns user input."""
    # Create a mock context
    context = AgentContext(agent_name="Test Agent")
    ctx_wrapper = RunContextWrapper(context=context)

    # Mock the input function to return a specific value
    with patch("builtins.input", return_value="test_input_value"):
        result = ask_user_for_info(ctx_wrapper, "What is your name?")

    # Verify the function returns the mocked input exactly
    assert result == "test_input_value"


def test_ask_user_for_info_with_different_prompts():
    """Test that ask_user_for_info works with different prompts."""
    # Create a mock context
    context = AgentContext(agent_name="Test Agent")
    ctx_wrapper = RunContextWrapper(context=context)

    # Test with different prompts and responses
    test_cases = [
        ("Enter directory path", "/home/user/project"),
        ("Confirm action (y/n)", "y"),
        ("Enter number", "42"),
        ("Enter text with spaces", "hello world"),
    ]

    for prompt, expected_input in test_cases:
        with patch("builtins.input", return_value=expected_input):
            result = ask_user_for_info(ctx_wrapper, prompt)
            assert result == expected_input
