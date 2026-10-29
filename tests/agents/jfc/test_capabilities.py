"""The one place the domain registries meet the domain-agnostic plan layer."""

from __future__ import annotations

from hepagent.agents.jfc.capabilities import plan_vocabulary


def test_the_tool_catalog_is_the_executors_own_tool_set():
    """Offering a name the executor could not honour is the failure to avoid."""
    from hepagent.agents.jfc.executor import executor_tools

    assert plan_vocabulary().tools == sorted(tool.name for tool in executor_tools())


def test_the_reviewer_catalog_is_the_reviewer_registry():
    from hepagent.agents.jfc.reviewers import REVIEWER_NAMES

    assert set(plan_vocabulary().reviewers) == set(REVIEWER_NAMES)


def test_the_default_template_validates_against_the_real_vocabulary():
    """A shipped template that its own installation rejects would be unusable."""
    from hepagent.plan.templates import DEFAULT_TEMPLATE, instantiate
    from hepagent.plan.validate import validate_plan

    plan = instantiate(
        DEFAULT_TEMPLATE,
        analysis_name="zbb",
        analysis_type="measurement",
        physics_prompt="Measure something.",
    )
    assert validate_plan(plan, vocabulary=plan_vocabulary()).blocking == []


def test_every_catalog_is_a_list_of_names():
    """`plan/` must never be handed a registry object to introspect."""
    vocabulary = plan_vocabulary()
    for catalog in (
        vocabulary.reviewers,
        vocabulary.tools,
        vocabulary.skills,
        vocabulary.mcp_servers,
    ):
        assert isinstance(catalog, list)
        assert all(isinstance(name, str) for name in catalog)
