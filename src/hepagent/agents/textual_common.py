"""Common tool wrappers for TextualAgent."""

from agents import function_tool


class AskUserToolWrapper:
    """Route ask_user_for_info prompts through the Textual UI."""

    def _is_ask_user_tool(self, tool) -> bool:
        name = getattr(tool, "name", "")
        return "ask_user_for_info" in name

    def _create_tool(self, adapter):
        @function_tool
        def ask_user_for_info(prompt: str) -> str:
            adapter.add_message("assistant", f"❓ {prompt}")
            adapter.textual_app.call_from_thread(adapter.textual_app.on_message_added)
            response = adapter.textual_app.input_container.request_input(f"{prompt}:")
            return response.strip()

        return ask_user_for_info

    def wrap_tools(self, tools: list, adapter) -> list:
        wrapped = []
        replaced = False
        for tool in tools:
            if self._is_ask_user_tool(tool):
                wrapped.append(self._create_tool(adapter))
                replaced = True
            else:
                wrapped.append(tool)
        return wrapped if replaced else list(tools)


class CompositeToolWrapper:
    """Apply multiple tool wrappers in order."""

    def __init__(self, *wrappers):
        self._wrappers = list(wrappers)

    def wrap_tools(self, tools: list, adapter) -> list:
        wrapped = list(tools)
        for wrapper in self._wrappers:
            wrapped = wrapper.wrap_tools(wrapped, adapter)
        return wrapped
