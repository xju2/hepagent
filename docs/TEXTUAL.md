# Textual TUI Architecture Notes

This document captures the current design of the Textual-based agent UI in `src/hepagent/agents/textual.py`, including the regressions that were fixed and the invariants future changes must preserve.

## Goals

- Keep full terminal usability after a task completes.
- Allow reliable step-by-step review of previous agent output.
- Keep "new task" entry available without shrinking prior-step content.
- Avoid keybinding/focus conflicts that block `left/right` step navigation.

## Core Model

### Message Steps

- Agent messages are grouped into reviewable steps by `_messages_to_steps(messages)`.
- A step boundary is created when:
  - a new assistant thought (`💭`) starts, or
  - a message has `kind == "final"`.

### Virtual Task Step

- After the agent finishes, UI adds one extra **virtual** step for new-task entry.
- This is controlled by `TextualAgent._has_task_step`.
- Total step count is:
  - `real_steps = len(_messages_to_steps(self.agent.messages))`
  - `n_steps = real_steps + (1 if _has_task_step else 0)`

This virtual step is the key fix that keeps historical step views full-height while still exposing new-task input.

## Layout and Focus Design

### Widgets

- `#content` (inside `VerticalScroll`) renders the currently selected step.
- `#task-input` is outside `VerticalScroll` but is **only displayed on the virtual task step**.
- `SmartInputContainer` handles tool-driven prompts (`ask_user_for_info`) and is independent from `#task-input`.

### Critical Focus Rule

- `self._vscroll.can_focus = False`

Why: if `VerticalScroll` can gain focus, arrow keys may be consumed by scroll behavior and stop driving app step navigation.

## Runtime Flow

### During Task Execution

- `_has_task_step = False`
- `n_steps = real_steps`
- `#task-input` hidden.

### On Agent Finish (`on_agent_finished`)

- `agent_state = "STOPPED"`
- `_has_task_step = True`
- `n_steps = real_steps + 1`
- jump to last step (`action_last_step()`), which is the virtual task step
- task input becomes visible and focused.

### Starting a New Task (`_start_new_task`)

- `_has_task_step = False`
- task step disappears
- UI returns to normal running mode.

## Input and Navigation Semantics

- Normal step navigation: existing bindings (`left/right`, `h/l`).
- On task step:
  - `left` goes to previous real step (browse).
  - `right` returns to task step.
- `escape` while focused on `#task-input` moves to previous step for browsing.

## Invariants (Do Not Break)

1. Prior/history steps must render with full available height.
2. `#task-input` must not be visible while browsing prior steps.
3. Step count must include the virtual task step only when `_has_task_step` is true.
4. `VerticalScroll` must remain non-focusable.
5. Final step after STOPPED state should be task entry, not clipped final output.

## Regressions We Fixed

### 1) Final output became hard to read after finish

Symptom:
- Task input appeared immediately and reduced message viewport height.

Root cause:
- New-task input block was effectively always present in layout while reviewing history.

Fix:
- Introduced virtual task step and show `#task-input` only on that step.

### 2) Arrow navigation did not change step content

Symptom:
- In "view mode", pressing arrows did not switch message step.

Root cause:
- Focus/key ownership conflict (focus moved away from app-level navigation path).

Fix:
- Prevented `VerticalScroll` from receiving focus.
- Simplified model to task-step browsing rather than mixed input/view mode toggles.

## Guidance for Future Changes

- If adding new panes/inputs, gate their visibility by step context; do not keep them always mounted-and-visible.
- Any new focusable widget must be evaluated for arrow-key conflicts with step navigation.
- Keep `update_content()` as the single source of truth for which UI region is visible on each step.
- When changing step semantics, update both:
  - `on_message_added()` step counting, and
  - `_update_headers()` subtitle hints.

## Suggested Smoke Checks

1. Run one task with multi-message output and final result.
2. Verify final screen opens on "NEW TASK" step.
3. Press `left` repeatedly and confirm prior steps are full-height.
4. Press `right` to return to task step and confirm input focus.
5. Submit a second task and ensure previous task-step state does not leak.
