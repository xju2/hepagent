"""Web UI for hepagent.

The modules here are split so that only :mod:`hepagent.web.app` depends on
Chainlit. Everything else (session state, tool wrapping, the streaming turn
loop) is transport-agnostic and importable without the optional ``web`` extra,
which keeps it unit-testable in CI.
"""

from hepagent.web.bridge import ApprovalDecision, TurnUI, WebBridge
from hepagent.web.session import CommandOutcome, WebSessionState, create_web_session_id
from hepagent.web.tools import LONG_BLOCKING_TOOL_NAMES, WebToolWrapper, offload_blocking_tool
from hepagent.web.turn import TurnOutcome, run_turn

__all__ = [
    "LONG_BLOCKING_TOOL_NAMES",
    "ApprovalDecision",
    "CommandOutcome",
    "TurnOutcome",
    "TurnUI",
    "WebBridge",
    "WebSessionState",
    "WebToolWrapper",
    "create_web_session_id",
    "offload_blocking_tool",
    "run_turn",
]
