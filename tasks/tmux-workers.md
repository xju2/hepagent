# Task: Let HepAgent use tmux CLI to run multiple agents in parallel

Inspired by the [oh-my-claude](https://github.com/Yeachan-Heo/oh-my-claudecode#tmux-cli-workers--codex--gemini-v440) project, we want to enable HepAgent to run multiple agents in parallel using tmux CLI. This allows us to manage and monitor multiple agents simultaneously, improving efficiency and productivity.

In a simple scenario, using the `tmux` CLI enables agents to first request an interactive SLURM allocation from Perlmutter, and then send the task commands to the active tmux pane. This is the only way the agents can leverage the much higher priority assigned to interactive jobs, which is crucial for many time-sensitive tasks.

## Concrete Plan

### Goal
Add a `tmux` tool module (`src/hepagent/tools/tmux.py`) with `@function_tool`-decorated functions that the skilled agent can call to:
1. Create and manage named tmux sessions/windows
2. Send shell commands (including `salloc`) into a pane
3. Capture pane output to read results or wait for a prompt

Register these tools in `src/hepagent/agents/skilled.py`.

### Tools to implement

| Tool | Purpose |
|---|---|
| `tmux_list_sessions` | List active tmux sessions and their windows |
| `tmux_create_session` | Create a new detached tmux session with an optional window name |
| `tmux_send_keys` | Send a command string (plus Enter) to a session:window pane |
| `tmux_capture_pane` | Capture current visible text in a pane |
| `tmux_wait_for_pattern` | Poll pane output until a regex pattern appears (with timeout) |
| `tmux_kill_session` | Kill a named tmux session |
| `request_slurm_interactive` | High-level: run `salloc` in a tmux pane and wait for the shell prompt |

### Typical agent workflow
```
1. tmux_create_session("work", "slurm")
2. request_slurm_interactive("work", "slurm", nodes=1, time="01:00:00", qos="interactive")
   → internally: tmux_send_keys salloc ... then tmux_wait_for_pattern r"\$\s*$"
3. tmux_send_keys("work", "slurm", "python my_script.py")
4. tmux_wait_for_pattern("work", "slurm", r"\$\s*$", timeout=300)
5. tmux_capture_pane("work", "slurm")   # read output
6. tmux_kill_session("work")
```

### Files to create/modify
- **NEW** `src/hepagent/tools/tmux.py` — all tool implementations
- **MODIFY** `src/hepagent/agents/skilled.py` — import and register new tools
- **NEW** `tests/test_tmux_tools.py` — basic unit/integration tests

## Progress report

- [x] Concrete plan written
- [x] Implement `src/hepagent/tools/tmux.py` — 7 tools: list/create/send/capture/wait/kill + salloc helper
- [x] Register tools in `skilled.py`
- [x] Write tests (`tests/test_tmux_tools.py`, 11 tests, all passing)
