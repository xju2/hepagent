# Task: Create Agent Explorer

The objective is to create an Explorer agent that explores different research directions.
The Explorer agent will have a list of sub-agents specialized in different research areas such as: collider physics, cosmology, neutrino physics, etc.
These sub-agents will be executed asynchronously in parallel, and the Explorer agent will aggregate their responses to provide a comprehensive answer to the user's question.  The Explorer agent can also suggest new research directions based on the user's interests and the current state of the field. These answers should be useful for researchers who want to explore new areas of research or get a broad overview of a specific topic.

The Explorer agent is at the same level as the scientist agent and can be used in the same way, for example by running `hepagent repl --agent explorer`. The Explorer agent will have a different set of tools and skills than the scientist agent. Those tools and skills will be determined later.

## Progress report

- Added `src/hepagent/agents/explorer.py` with an Explorer agent and an async `explore_research_directions` tool.
- Implemented a parallel specialist panel covering collider, cosmology, neutrino, theory, and instrumentation perspectives.
- Registered `explorer` as a first-class selectable agent for `hepagent run`, `hepagent repl`, `/agents`, and `list-agents`.
- Updated README and REPL documentation examples to include `--agent explorer`.
- Added tests for specialist selection, Explorer agent creation, parallel panel execution, and CLI routing.
- Next steps: decide the Explorer-specific production tool and skill set once the intended research workflow is clearer.
