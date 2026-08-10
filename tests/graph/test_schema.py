"""Tests for the graph node/edge contract."""

import pytest

from hepagent.graph.schema import (
    EDGE_DOMAIN,
    EDGE_TYPES,
    NODE_TYPES,
    Edge,
    GraphSchemaError,
    Node,
    check_edge_domain,
    make_id,
    slugify,
)


def test_make_id_is_deterministic():
    a = make_id("artifact", "phase1_strategy/outputs/STRATEGY.md")
    b = make_id("artifact", "phase1_strategy/outputs/STRATEGY.md")
    assert a == b == "artifact:phase1_strategy/outputs/STRATEGY.md"


def test_make_id_rejects_unknown_type():
    with pytest.raises(GraphSchemaError):
        make_id("wormhole", "x")


def test_slugify_preserves_paths_and_strips_unsafe():
    assert slugify("phase2/outputs/figures/m jj.png") == "phase2/outputs/figures/m-jj.png"
    assert slugify("   ") == "unnamed"


def test_node_rejects_unknown_type_and_status():
    with pytest.raises(GraphSchemaError):
        Node(id="x:1", type="nonsense", label="x")
    with pytest.raises(GraphSchemaError):
        Node(id="x:1", type="artifact", label="x", status="haunted")


def test_node_round_trips_through_dict():
    node = Node(
        id="dataset:mc23_ttbar",
        type="dataset",
        label="mc23 ttbar",
        content_ref="phase2_exploration/outputs/EXPLORATION.md",
        metadata={"ami_tag": "e8514_s4162", "campaign": "mc23a", "lumi_fb": 140.0},
        phase="2",
    )
    restored = Node.from_dict(node.to_dict())
    assert restored == node


def test_node_from_dict_ignores_unknown_fields():
    restored = Node.from_dict(
        {"id": "artifact:a", "type": "artifact", "label": "A", "future_field": 1}
    )
    assert restored.id == "artifact:a"


def test_edge_rejects_self_edge_and_unknown_type():
    with pytest.raises(GraphSchemaError):
        Edge(src="artifact:a", dst="artifact:a", type="derives_from")
    with pytest.raises(GraphSchemaError):
        Edge(src="artifact:a", dst="artifact:b", type="teleports_to")


def test_edge_key_identifies_the_triple():
    edge = Edge(src="artifact:a", dst="artifact:b", type="derives_from")
    assert edge.key == ("artifact:a", "artifact:b", "derives_from")


def test_check_edge_domain_accepts_legal_pair():
    edge = Edge(src="evidence:closure", dst="artifact:strategy", type="supports")
    assert check_edge_domain(edge, "evidence", "artifact") == ""


def test_check_edge_domain_rejects_illegal_source():
    edge = Edge(src="problem:q", dst="artifact:strategy", type="supports")
    error = check_edge_domain(edge, "problem", "artifact")
    assert "cannot start at a 'problem' node" in error


def test_check_edge_domain_rejects_illegal_target():
    edge = Edge(src="commitment:D1", dst="problem:q", type="resolves")
    error = check_edge_domain(edge, "commitment", "problem")
    assert "cannot end at a 'problem' node" in error


def test_every_edge_type_has_a_domain_entry():
    assert set(EDGE_DOMAIN) == set(EDGE_TYPES)


def test_edge_domain_references_only_known_node_types():
    for src_types, dst_types in EDGE_DOMAIN.values():
        assert src_types <= set(NODE_TYPES)
        assert dst_types <= set(NODE_TYPES)
