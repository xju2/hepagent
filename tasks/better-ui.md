# Task: Claude Code-like REPL
Create a REPL (Read-Eval-Print Loop) interface that mimics the style of Claude Code.
The leaked Claude Code can be found here: claude-code/

The REPL should allow users to ask questions, view LLM responses, show code changes, execute code snippets
with user's approval as well as automatically execute code snippets when appropriate,
and display the results in a clear and concise manner.
The interface should be user-friendly and visually appealing, with features such as syntax highlighting,
error messages, and support for multiple programming languages.

## Basic Features to Implement:
1. **Input Area**: A text input area where users can type their questions or code snippets.
2. **Response Display**: A section to display the LLM's responses, including any code changes or suggestions.
3. **Syntax Highlighting**: Implement syntax highlighting for code snippets in the response display area

## Advanced Features to Consider:
1. **Slash Commands**: Allow users to execute specific commands. See later sections for examples.


#### Example Slash Commands:
- `/quit`: Exit the REPL interface.
- `/help`: Display a list of available commands and their descriptions.
- `/clear`: Clear the conversation history and reset the REPL interface.
- `/agents`: List all available agents
- `/agent [agent_name]`: Switch to a specific agent for handling the conversation.


## Progress Tracking:
