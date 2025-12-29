#!/usr/bin/env python3
"""Example script demonstrating TextualAgent with bash agent integration.

This script shows how to create and run a bash agent with TextualAgent's
interactive TUI. It includes fallback to DummyAgent if API keys are not configured.
"""

import sys
import os


def main():
    # Check for required imports
    try:
        from scripts.bash_textual import TextualAgent, AgentAdapter
        from hepagent.agents.bash import create as create_bash_agent
    except ImportError as e:
        print(f"Error: Failed to import required modules: {e}")
        print("Make sure you're running from the repository root:")
        print("  python3 scripts/example_bash_textual.py")
        sys.exit(1)
    
    # Try to create the real bash agent
    use_real_agent = True
    if not os.environ.get("CBORG_API_KEY"):
        print("Warning: CBORG_API_KEY environment variable not set.")
        print("Falling back to DummyAgent for demonstration.")
        use_real_agent = False
    
    # Create TextualAgent app
    app = TextualAgent(model="gpt-4", env={})
    
    if use_real_agent:
        try:
            # Create bash agent
            bash_agent = create_bash_agent()
            
            # Wrap with adapter to make it work with TextualAgent
            app.agent = AgentAdapter(bash_agent, app)
            
            print("Starting TextualAgent with real bash agent...")
            print("\nUI Controls:")
            print("  - Press 'y' or Ctrl+Y to switch to YOLO mode (auto-approve commands)")
            print("  - Press 'c' to switch to CONFIRM mode (ask before each command)")
            print("  - Press 'u' or Ctrl+U to switch to HUMAN mode (disable auto-commands)")
            print("  - Press 'left'/'h' or 'right'/'l' to navigate execution steps")
            print("  - Press 'q' or Ctrl+Q to quit")
            print()
            
            # Run with a sample task
            task = "List the files in the current directory and tell me how many there are."
            exit_status, result = app.run(task=task)
            
            print(f"\nAgent exited with status: {exit_status}")
            print(f"Result: {result}")
            
        except Exception as e:
            print(f"Error running bash agent: {e}")
            print("Falling back to DummyAgent...")
            use_real_agent = False
    
    if not use_real_agent:
        print("Starting TextualAgent with DummyAgent (demo mode)...")
        print("\nUI Controls:")
        print("  - Press 'left'/'h' or 'right'/'l' to navigate execution steps")
        print("  - Press 'q' or Ctrl+Q to quit")
        print()
        
        exit_status, result = app.run(task="Demonstrate the Textual Bash Agent UI")
        
        print(f"\nAgent exited with status: {exit_status}")
        print(f"Result: {result}")


if __name__ == "__main__":
    main()
