---
name: ising-ground-state
description: Find ground states (lowest-energy spin configurations) of Ising spin glasses, QUBO or MaxCut problems on sparse graphs with a tested parallel-tempering + cluster-move solver in C. Use for any "minimise E = -sum J_ij s_i s_j" task.
---

# Ising ground-state search

`ising_pt.c` (in this skill's directory, see SKILL FILES below) is a tested solver:
parallel tempering with two replicas per temperature, Houdayer isoenergetic cluster
moves at the colder half of the ladder, and a zero-temperature polish of each record.
It works on any sparse graph with integer couplings (multiply real couplings by a
common factor and round). MaxCut maps directly: `J_ij = -w_ij`. A general QUBO maps by
`x = (1 + s) / 2`, which also creates linear terms `h_i s_i` (from diagonal `Q_ii` and
from each off-diagonal pair) that this pairwise-only solver cannot take. Add an
auxiliary reference spin `s_N` (so `N+1` sites) and encode each energy term `+h_i s_i`
as the coupling `J_{i,N} = -h_i`; decode the answer as `s_i * s_N`, since flipping every
spin leaves the energy unchanged. Dropping the linear terms changes the optimum.

Do not write your own annealer first. Use this one, and spend your effort on running
it well and on analysing the instance.

## Steps

1. Convert each instance to the solver's text format: first line `N M`, then `M`
   lines `i j J` (0-based sites, integer `J`, energy convention `E = -sum J s_i s_j`;
   flip the sign of `J` if the task defines `E = +sum J s_i s_j`).
2. Build: `gcc -O3 -march=native -o ising_pt ising_pt.c -lm`
3. Run: `./ising_pt INST OUT [seconds] [seed] [nT] [Tlo] [Thi] [icm_frac] [target]`
   - `OUT` receives `N` tokens `+1`/`-1`, one per line, rewritten atomically every
     time the best energy improves, so it is always a valid answer.
   - Defaults: 600 s, seed 1, 32 temperatures from `Tlo = 0.4` to `Thi = 3.0` in units
     of the rms coupling, cluster moves on the colder half.
   - With `target`, it stops as soon as the energy is at or below it.
   - It prints `t=<s> round=<n> best=<E>` every 30 s.
4. Write an independent scorer (a few lines of Python that recompute the energy from
   the original instance file) and check the solver's reported energy with it once.
5. First get a valid answer for every instance (a 60 s run each), then start
   production.

## How to run production

- The solver is single-threaded. Start exactly one long-running process per instance,
  all at once, each writing straight to that instance's final output file and each
  for the whole remaining time minus a ten-minute margin. Do this even when there are
  somewhat more instances than CPUs (up to twice as many); the processes share them.
- Then leave the runs alone. Do not restart, duplicate or "refresh" them: a restart
  throws away the equilibration that hard instances need, and extra processes on the
  same CPUs only slow every run down. Wait in long steps (30-60 minutes).
- On hard frustrated instances the best energy sits on a plateau for a long time and
  then drops by a large step when the search finds the right basin. A plateau is
  therefore not evidence of the optimum, and it is not a reason to change anything.
  Measured on 3D lattices with about 10^4 spins: the last drop to the ground state
  came after 1 to 3.5 CPU-hours per instance, and one hour before it the run was
  still 0.5-1% above the optimum and gaining only a few units per half hour. With
  shared CPUs the wall time is longer in proportion. Stopping a run at that stage
  is the usual way to fail.
- Do side experiments next to production with `nice -n 19`, never by stopping
  production runs to free CPUs.
  Keep every instance running until the deadline unless you can certify its optimum
  (a known target, or a lower bound that the energy meets).
- Only if there are more CPUs than instances, add runs of the same instance with
  another seed, each writing to its own file, and keep the lowest-energy file.
- If an output file already holds a valid configuration, a new run starts from it and
  never replaces it with a higher energy, so runs can be chained safely.
- Tuning is rarely needed. If you do tune, compare settings in separate 10-20 minute
  pilots before production, not by disturbing production: `Tlo` between 0.3 and 0.6,
  `Thi` between 2.5 and 4. More temperatures cost time per round in proportion.

## While it runs

Analyse the instance for structure that makes it easier: dangling or weakly coupled
spins that can be eliminated, a planted or constructed solution (look at how the
couplings could have been generated), unfrustrated sub-blocks, or an exact method
that fits the size (planar graphs, small tree-width, branch and cut). A structural shortcut beats more sweeps; test each
idea on a small case with a brute-force answer before trusting it.
