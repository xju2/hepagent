"""Bash-specific tool wrapper for TextualAgent."""

from agents import function_tool
from hepagent.agents.bash import TOOL_CANCEL_MESSAGE, execute_bash_command

OUTPUT_TRUNCATE_LENGTH = 500


class BashToolWrapper:
    """Wrap bash tool to add Textual confirmation and output rendering."""

    def _handle_rejection(self, adapter, reason: str) -> dict:
        adapter.add_message("user", f"❌ Rejected: {reason}")
        adapter.textual_app.call_from_thread(adapter.textual_app.on_message_added)
        return {"output": TOOL_CANCEL_MESSAGE, "returncode": 1}

    def _is_bash_tool(self, tool) -> bool:
        name = getattr(tool, "name", "")
        return "execute_bash_command" in name

    def _create_tool(self, adapter):
        @function_tool
        def execute_bash_command_with_confirmation(
            cmd: str, cwd: str = "", thought: str = ""
        ) -> dict:
            """Execute a bash command with user's confirmation and return the output."""
            if thought:
                adapter.add_message("assistant", f"💭 THOUGHT: {thought}")
                adapter.textual_app.call_from_thread(adapter.textual_app.on_message_added)

            adapter.add_message(
                "assistant",
                f"🔧 Preparing to execute:\n```bash\n{cmd}\n```"
                f"\nWorking directory: {cwd or 'current'}",
            )
            adapter.textual_app.call_from_thread(adapter.textual_app.on_message_added)

            if adapter.config.mode == "yolo":
                adapter.add_message("system", "✓ Auto-approved (YOLO mode)")
                adapter.textual_app.call_from_thread(adapter.textual_app.on_message_added)
            elif adapter.config.mode == "confirm":
                prompt = "Confirm execution? (press Enter to accept, or type your reason to reject)"
                response = adapter.textual_app.input_container.request_input(prompt)
                if response.strip():
                    return self._handle_rejection(adapter, response)
                adapter.add_message("user", "✓ Approved")
                adapter.textual_app.call_from_thread(adapter.textual_app.on_message_added)
            elif adapter.config.mode == "human":
                prompt = (
                    "⚠️ Agent called tool in HUMAN mode. Allow? "
                    "(Enter to allow, type reason to reject)"
                )
                response = adapter.textual_app.input_container.request_input(prompt)
                if response.strip():
                    return self._handle_rejection(adapter, response)

            result = execute_bash_command(cmd, cwd=cwd)

            result_icon = "✓" if result["returncode"] == 0 else "✗"
            output = result["output"]
            truncated_output = output[:OUTPUT_TRUNCATE_LENGTH] + (
                "..." if len(output) > OUTPUT_TRUNCATE_LENGTH else ""
            )
            adapter.add_message(
                "system",
                f"{result_icon} Return code: {result['returncode']}\n"
                f"Truncated Output:\n{truncated_output}",
            )
            adapter.textual_app.call_from_thread(adapter.textual_app.on_message_added)

            return result

        return execute_bash_command_with_confirmation

    def wrap_tools(self, tools: list, adapter) -> list:
        wrapped = []
        replaced = False
        for tool in tools:
            if self._is_bash_tool(tool):
                wrapped.append(self._create_tool(adapter))
                replaced = True
            else:
                wrapped.append(tool)
        return wrapped if replaced else list(tools)
