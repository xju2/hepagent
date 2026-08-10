"""The analysis plan: the authored structure of an analysis.

The provenance graph in :mod:`hepagent.graph` records what an analysis *did*.
This package holds what it is *supposed to do*: a mutable, user-authored document
describing the nodes of work, the prompt each one runs, and the dependencies
between them.

The two are not rivals. `plan.json` is the design and is edited wholesale;
`graph/*.jsonl` is the ledger and is append-only. :func:`plan_to_graph` compiles
the former into the seed of the latter, which is what lets the existing planner,
validation and resume logic keep working unchanged.

See `docs/PLAN.md` for the schema and the invariants.
"""

from hepagent.plan.compile import execution_order, plan_to_graph
from hepagent.plan.layout import layer
from hepagent.plan.schema import (
    EDGE_KINDS,
    GATE_TIMINGS,
    GATES,
    INJECT_MODES,
    NODE_KINDS,
    PLAN_SCHEMA_VERSION,
    ROLES,
    AnalysisPlan,
    PlanContract,
    PlanEdge,
    PlanGate,
    PlanNode,
    PlanSchemaError,
)
from hepagent.plan.store import (
    HISTORY_DIRNAME,
    PLAN_FILENAME,
    PlanFormatError,
    PlanNotFoundError,
    has_plan,
    load_plan,
    plan_path,
    save_plan,
)
from hepagent.plan.validate import validate_plan

__all__ = [
    "EDGE_KINDS",
    "GATES",
    "GATE_TIMINGS",
    "HISTORY_DIRNAME",
    "INJECT_MODES",
    "NODE_KINDS",
    "PLAN_FILENAME",
    "PLAN_SCHEMA_VERSION",
    "ROLES",
    "AnalysisPlan",
    "PlanContract",
    "PlanEdge",
    "PlanFormatError",
    "PlanGate",
    "PlanNode",
    "PlanNotFoundError",
    "PlanSchemaError",
    "execution_order",
    "has_plan",
    "layer",
    "load_plan",
    "plan_path",
    "plan_to_graph",
    "save_plan",
    "validate_plan",
]
