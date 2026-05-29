"""Tests for JFC investigator agent."""


def test_parse_regression_ticket_full(tmp_path):
    from hepagent.agents.jfc.investigator import _parse_regression_ticket

    ticket = tmp_path / "REGRESSION_TICKET.md"
    ticket.write_text(
        "# Regression Ticket\n\n"
        "## Origin Phase\n3\n\n"
        "## Affected Downstream Phases\n3, 4a, 4b\n\n"
        "## Root Cause\nBad selection cut.\n"
    )
    origin, affected = _parse_regression_ticket(ticket)
    assert origin == 3
    assert affected == [3, "4a", "4b"]


def test_parse_regression_ticket_subphase_origin(tmp_path):
    from hepagent.agents.jfc.investigator import _parse_regression_ticket

    ticket = tmp_path / "REGRESSION_TICKET.md"
    ticket.write_text("## Origin Phase\n4a\n\n## Affected Downstream Phases\n4a, 4b, 4c\n")
    origin, affected = _parse_regression_ticket(ticket)
    assert origin == "4a"
    assert affected == ["4a", "4b", "4c"]


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
