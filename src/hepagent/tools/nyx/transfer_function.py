from pathlib import Path

from pydantic import BaseModel, Field

from agents import function_tool


class TransferFunctionInput(BaseModel):
    h: float = Field(description="Dimensionless Hubble parameter")
    Omega_b: float = Field(description="Baryon density parameter")
    Omega_c: float = Field(description="Cold dark matter density parameter")
    n_s: float = Field(description="Primordial spectral index")
    z_ic: float = Field(description="Redshift for initial conditions")
    output_path: str = Field(description="Path to save the generated transfer function file")


@function_tool
def create_transfer_function(params: TransferFunctionInput) -> Path:
    """
    Create a cosmic transfer function using CAMB.

    Args:
        params (TransferFunctionInput): Cosmological parameters for the transfer function.

    Returns:
        Path: Path to the generated transfer function file.
    """
    import camb
    import numpy as np

    # cosmological parameters
    h = params.h
    Omega_b = params.Omega_b
    Omega_c = params.Omega_c
    n_s = params.n_s
    z_ic = params.z_ic
    out_path = Path(params.output_path)

    # --------------------------------------------------
    # 2. Set CAMB parameters
    # --------------------------------------------------
    pars = camb.CAMBparams()

    pars.set_cosmology(
        H0=100 * h,
        ombh2=Omega_b * h * h,
        omch2=Omega_c * h * h,
    )

    # Primordial spectrum (σ8 is derived from As)
    pars.InitPower.set_params(ns=n_s)

    # ! what is kmax, need to be configurabled?
    pars.set_matter_power(
        redshifts=[z_ic],
        kmax=300.0,  # physical Mpc^-1
    )

    pars.WantTransfer = True
    pars.DoLensing = False

    pars.Transfer.k_per_logint = 300  # default is ~10

    # --------------------------------------------------
    # 3. Run CAMB
    # --------------------------------------------------
    results = camb.get_results(pars)
    transfers = results.get_matter_transfer_data()

    # --------------------------------------------------
    # 4. Extract k and δ_i(k,z)
    # --------------------------------------------------
    k = transfers.q  # physical Mpc^-1 units

    delta_cdm = transfers.transfer_z("delta_cdm", z_index=0)
    delta_b = transfers.transfer_z("delta_baryon", z_index=0)

    # CAMB already has δ(k→0)=1

    # --------------------------------------------------
    # 5. Generate CosmicIC 7-column format
    # --------------------------------------------------
    zeros = np.zeros_like(k)
    cmb_tf = np.column_stack([k, delta_cdm, delta_b, zeros, zeros, zeros, zeros])

    np.savetxt(out_path, cmb_tf, fmt="%.10e")
    print(f"Saved transfer function to {out_path}")
    return out_path
