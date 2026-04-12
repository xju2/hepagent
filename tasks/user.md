# Task: Add USER.md
The task is include `USER.md` in the skilled agent's instruction registry.

## Requirements
The `USER.md` file will be located in the hepagent configuration directory `$HOME/.hepagent/`. A template will be created with basic sections if the file does not exist when the skilled agent is first run.

The skilled agent should update the `USER.md` file when user interactions reveal new information about user preferences, goals, projects, expertise, interests, or constraints that are not already captured in the existing `USER.md`. This ensures that the agent can learn and adapt to the user's needs over time. But the udpate is not mandatory for every interaction, only when significant new information is obtained.

Remember "Less is more."

Suggest a reasonable structure for the `USER.md` file that can effectively capture and organize this information. The structure should be flexible enough to accommodate various types of information about the user while being easy to read and update.

## Progress report
- Added USER.md bootstrap support in helper utilities with a concise default template at `$HOME/.hepagent/USER.md`.
- Extended the skilled agent manifest assembly to include USER.md content under a dedicated `# USER PROFILE` section.
- Added a new `update_user_profile` function tool for significant, deduplicated updates by category (Preference/Goal/Project/Expertise/Interest/Constraint/Other).
- Registered `update_user_profile` in the skilled agent tool list and added explicit instruction guidance for when to use it.
- Added tests covering USER.md bootstrap behavior, instruction inclusion, and deduplicated USER.md tool updates.

Next steps:
- Optional: tune category-to-section mapping (for example map `Other` to a dedicated section) if future usage suggests a clearer organization.