"""Tests for hepagent.tools.nyx.transfer_function."""

from pathlib import Path


def test_transfer_function_input_model():
    """TransferFunctionInput validates fields correctly."""
    from hepagent.tools.nyx.transfer_function import TransferFunctionInput

    params = TransferFunctionInput(
        h=0.7,
        Omega_b=0.05,
        Omega_c=0.3,
        n_s=0.96,
        z_ic=50.0,
        output_path="/tmp/test.dat",
    )
    assert params.h == 0.7
    assert params.Omega_b == 0.05
    assert params.n_s == 0.96
    assert params.output_path == "/tmp/test.dat"
