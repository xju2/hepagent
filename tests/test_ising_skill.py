"""The bundled Ising solver finds the brute-force optimum of a small random instance."""

import itertools
import random
import shutil
import subprocess
from pathlib import Path

import pytest

SOURCE = Path(__file__).parents[1] / ".agents/skills/ising-ground-state/ising_pt.c"


@pytest.mark.skipif(shutil.which("gcc") is None, reason="needs gcc")
def test_ising_pt_matches_brute_force(tmp_path):
    rng = random.Random(3)
    n = 14
    edges = [
        (i, j, rng.choice([-3, -2, -1, 1, 2, 3]))
        for i in range(n)
        for j in range(i + 1, n)
        if rng.random() < 0.4
    ]

    def energy(s):
        return -sum(J * s[i] * s[j] for i, j, J in edges)

    (tmp_path / "inst.txt").write_text(
        f"{n} {len(edges)}\n" + "".join(f"{i} {j} {J}\n" for i, j, J in edges)
    )
    exe = tmp_path / "ising_pt"
    subprocess.run(["gcc", "-O2", "-o", exe, SOURCE, "-lm"], check=True)
    exact = min(energy(s) for s in itertools.product([1, -1], repeat=n))
    subprocess.run(
        [
            exe,
            tmp_path / "inst.txt",
            tmp_path / "out.txt",
            "5",
            "1",
            "8",
            "0.4",
            "3",
            "0.5",
            str(exact),
        ],
        check=True,
        capture_output=True,
    )
    spins = [int(t) for t in (tmp_path / "out.txt").read_text().split()]
    assert len(spins) == n and energy(spins) == exact


@pytest.mark.skipif(shutil.which("gcc") is None, reason="needs gcc")
def test_ising_pt_never_overwrites_a_better_existing_output(tmp_path):
    # Ferromagnetic chain: all-up is optimal; a 0-second run must keep it.
    n = 50
    (tmp_path / "inst.txt").write_text(
        f"{n} {n - 1}\n" + "".join(f"{i} {i + 1} 1\n" for i in range(n - 1))
    )
    out = tmp_path / "out.txt"
    out.write_text("+1\n" * n)
    exe = tmp_path / "ising_pt"
    subprocess.run(["gcc", "-O2", "-o", exe, SOURCE, "-lm"], check=True)
    res = subprocess.run(
        [exe, tmp_path / "inst.txt", out, "0"], check=True, capture_output=True, text=True
    )
    assert f"E={-(n - 1)}" in res.stdout
    assert out.read_text().split() == ["+1"] * n
