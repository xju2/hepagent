"""What a JFC plan node may be given, as names the plan layer can check.

`hepagent.plan` is deliberately domain-agnostic — it knows a node declares
reviewers, tools, skills and MCP servers, but not which ones exist. This module
is where the domain registries meet it: one function, assembled from the same
sources the runtime itself uses, so the editor cannot offer a name the executor
could not honour.

Every catalog is resolved on call rather than at import. Skills live on disk and
MCP servers in a user-editable TOML file; both can change between two requests
to the same server process.
"""

from __future__ import annotations

from hepagent.plan.validate import PlanVocabulary


def tool_names() -> list[str]:
    """Function tools a node may allowlist, from the executor's own tool set."""
    from hepagent.agents.jfc.executor import executor_tools

    return sorted(tool.name for tool in executor_tools())


def skill_names() -> list[str]:
    """Skills installed in the active agents directory."""
    from hepagent.agent_helpers import list_skills

    return [name for name, _ in list_skills()]


def mcp_server_names() -> list[str]:
    """MCP servers this installation declares in ``mcp.toml``.

    Declarative only — see `hepagent.plan.schema.PlanNode.mcp_servers`.
    """
    from hepagent.helpers import load_mcp_config

    return sorted(load_mcp_config())


def platform_names() -> list[str]:
    """Model providers this installation has configured in ``providers.toml``.

    Cheap — the answer is a TOML file, not a network call — which is why it can
    ride along in the vocabulary while the *models* on a platform cannot; those
    are listed on demand, see `hepagent.web.plan_api`.
    """
    from hepagent.model_providers import get_supported_model_providers

    return sorted(get_supported_model_providers())


def reviewer_names() -> list[str]:
    """Reviewer factories the review gate can build."""
    from hepagent.agents.jfc.reviewers import REVIEWER_NAMES

    return sorted(REVIEWER_NAMES)


def plan_vocabulary() -> PlanVocabulary:
    """Every catalog at once, for `validate_plan` and the editor's dropdowns."""
    return PlanVocabulary(
        reviewers=reviewer_names(),
        tools=tool_names(),
        skills=skill_names(),
        mcp_servers=mcp_server_names(),
        platforms=platform_names(),
    )
