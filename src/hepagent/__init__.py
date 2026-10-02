"""HepAgent package.

Importing the package applies the OpenAI Agents SDK tracing setting, which is
disabled unless ``HEPAGENT_OPENAI_TRACING`` is set — every entry point (CLI,
REPL, web, tests) goes through this import.
"""

from hepagent.helpers import configure_openai_tracing

configure_openai_tracing()
