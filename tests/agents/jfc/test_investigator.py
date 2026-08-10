"""Tests for JFC investigator agent."""


def test_parse_regression_ticket_full(tmp_path):
    from hepagent.agents.jfc.investigator import _parse_regression_ticket

    ticket = tmp_path / "REGRESSION_TICKET.md"
    ticket.write_text(
        "# Regression Ticket\n\n"
        "## Origin Phase\nselection\n\n"
        "## Affected Downstream Phases\nselection, inference_expected, inference_partial\n\n"
        "## Root Cause\nBad selection cut.\n"
    )
    origin, affected = _parse_regression_ticket(ticket)
    assert origin == "selection"
    assert affected == ["selection", "inference_expected", "inference_partial"]


def test_parse_regression_ticket_lowercases_node_ids(tmp_path):
    """Node ids are lowercase slugs; the investigator's prose often is not."""
    from hepagent.agents.jfc.investigator import _parse_regression_ticket

    ticket = tmp_path / "REGRESSION_TICKET.md"
    ticket.write_text(
        "## Origin Phase\nInference_Expected\n\n"
        "## Affected Downstream Phases\nInference_Expected, INFERENCE_PARTIAL\n"
    )
    origin, affected = _parse_regression_ticket(ticket)
    assert origin == "inference_expected"
    assert affected == ["inference_expected", "inference_partial"]


def test_parse_regression_ticket_missing_sections(tmp_path):
    from hepagent.agents.jfc.investigator import _parse_regression_ticket

    ticket = tmp_path / "REGRESSION_TICKET.md"
    ticket.write_text("# Regression Ticket\n\nNo structured sections here.\n")
    origin, affected = _parse_regression_ticket(ticket)
    assert origin is None
    assert affected == []


def test_regression_ticket_dataclass():
    from hepagent.agents.jfc.investigator import RegressionTicket

    t = RegressionTicket(
        detected_phase="4a",
        origin_phase=3,
        symptom="wrong binning",
        affected_phases=[3, "4a"],
    )
    assert t.detected_phase == "4a"
    assert t.origin_phase == 3
    assert 3 in t.affected_phases
