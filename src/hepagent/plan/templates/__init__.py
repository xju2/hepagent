"""Built-in analysis plan templates.

A template is a plan with its prompts named rather than inlined: each node
carries a ``prompt_ref`` pointing at a markdown file in the JFC data directory.
:func:`instantiate` resolves those refs and substitutes the ``{{name}}``-style
placeholders, so the `plan.json` that lands in an analysis directory always holds
the literal prompt text — which is what makes it editable.

Templates are the starting points, not the shapes. Once instantiated a plan
belongs to its analysis and can be rewired freely.
"""

from hepagent.plan.templates.registry import (
    DEFAULT_TEMPLATE,
    TemplateNotFoundError,
    describe_templates,
    instantiate,
    list_templates,
    load_template,
)

__all__ = [
    "DEFAULT_TEMPLATE",
    "TemplateNotFoundError",
    "describe_templates",
    "instantiate",
    "list_templates",
    "load_template",
]
