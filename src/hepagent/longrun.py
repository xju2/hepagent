"""Long-run harness for `hepagent run --time-budget`.

A chat loop pays a model call for every look at the clock, keeps its memory in a
conversation that has to be trimmed, and loses results that background processes
find. This module gives the agent:

- tracked background jobs and a `wait` tool that sleeps without model calls until a
  job exits, a sub-agent reports, a log matches, or a kept output improves;
- a best-result keeper: registered output files are scored periodically, the best
  version is snapshotted, and a worse or broken file is restored from it;
- parallel sub-agents, each with its own brief, time limit and conversation;
- a per-call status (jobs, kept scores, phase checkpoints, STATE.md) for the model.
"""

from __future__ import annotations

import asyncio
import os
import re
import shlex
import shutil
import signal
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic

from agents import Agent, RunConfig, Runner, function_tool

STATE_FILE = "STATE.md"
STATE_MAX_CHARS = 3000
KEEP_EVERY_S = 60
SCORE_TIMEOUT_S = 120
DEADLINE_MARGIN_S = 300
MAX_JOBS_PER_CPU = 2
PROTECT_JOB_AFTER_S = 1800

SUBAGENT_PROMPT = """You are sub-agent "{name}", working for an orchestrator agent.

Overall task (for context; do only your assignment):
{task}

Your assignment:
{brief}

Work in your own directory {workdir} unless the assignment says otherwise, and never
overwrite the final deliverables unless told to. You have {minutes:.0f} minutes. Verify
what you claim. Finish with a concise report: what you did, results with numbers,
file paths, and what remains open."""


def tool_error(ctx, error: Exception) -> str:
    """Tell the model why a tool call failed; newer SDK defaults hide the reason."""
    return f"The tool call failed: {type(error).__name__}: {error}. Fix the call and try again."


harness_tool = function_tool(failure_error_function=tool_error)


def available_cpus() -> int:
    """CPUs this process may really use: `nproc` ignores a container's cgroup quota."""
    cpus = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count()
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()
        if quota != "max":
            cpus = min(cpus, max(1, round(int(quota) / int(period))))
    except (OSError, ValueError):
        pass
    return cpus or 1


def _fmt(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m{seconds % 60:02d}s"


def _tail(path: Path, lines: int = 3, max_chars: int = 300) -> str:
    try:
        with open(path, "rb") as fh:
            fh.seek(max(0, os.path.getsize(path) - 4096))
            text = fh.read().decode("utf-8", "replace")
    except OSError:
        return ""
    return " | ".join(text.strip().splitlines()[-lines:])[-max_chars:]


@dataclass
class Job:
    proc: subprocess.Popen
    cmd: str
    log: Path
    started: float


@dataclass
class Output:
    path: Path
    score_cmd: str
    higher_is_better: bool
    snapshot: Path
    best: float | None = None
    current: float | None = None

    def better(self, a: float, b: float) -> bool:
        return a > b if self.higher_is_better else a < b


@dataclass
class SubAgent:
    task: asyncio.Task
    started: float
    minutes: float
    result: str | None = None
    seen: bool = field(default=False)


class LongRun:
    def __init__(self, deadline: float | None, task_prompt: str, workdir: str | Path = "."):
        self.deadline = deadline
        self.task_prompt = task_prompt
        self.workdir = Path(workdir).resolve()
        self.dir = self.workdir / ".hepagent"
        (self.dir / "jobs").mkdir(parents=True, exist_ok=True)
        (self.dir / "best").mkdir(exist_ok=True)
        self.jobs: dict[str, Job] = {}
        self.outputs: dict[str, Output] = {}
        self.subagents: dict[str, SubAgent] = {}
        self.improvements = 0
        self.cpus = available_cpus()
        self._lock = threading.Lock()
        # Set by `tools()`: how to approve commands and how to run a sub-agent.
        self.approve = None
        self.run_subagent = None

    # ----- jobs -----
    def start_job(self, name: str, cmd: str, cwd: str = "") -> str:
        job = self.jobs.get(name)
        if job and job.proc.poll() is None:
            return f"Job {name!r} is still running; stop it or pick another name."
        running = sum(j.proc.poll() is None for j in self.jobs.values())
        if running >= MAX_JOBS_PER_CPU * self.cpus:
            return (
                f"Refused: {running} jobs already run on {self.cpus} CPUs, so each gets "
                f"{self.cpus / running:.2f} of a core. Stop a job before starting another."
            )
        log = self.dir / "jobs" / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', name)}.log"
        with open(log, "ab") as fh:
            proc = subprocess.Popen(
                ["bash", "-c", cmd],
                cwd=cwd or None,
                stdin=subprocess.DEVNULL,
                stdout=fh,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        self.jobs[name] = Job(proc, cmd, log, monotonic())
        return (
            f"Started job {name!r} (pid {proc.pid}); output goes to {log}. "
            f"{running + 1} jobs now run on {self.cpus} CPUs."
        )

    def stop(self, name: str, force: bool = False) -> str:
        if name in self.subagents:
            self.subagents[name].task.cancel()
            return f"Cancelled sub-agent {name!r}."
        job = self.jobs.get(name)
        if job is None:
            return f"No job or sub-agent named {name!r}."
        ran = monotonic() - job.started
        if job.proc.poll() is None and ran > PROTECT_JOB_AFTER_S and not force:
            # Models kill hours-old searches on a plateau to free a CPU; make that a decision.
            return (
                f"NOT stopped: job {name!r} has run {_fmt(ran)} and its in-memory state "
                "would be lost; a restart begins again from scratch. A search that has "
                "stopped improving for a while is normal, not a reason to stop it. For a "
                "side experiment, run it alongside with `nice` instead. If the job is "
                "truly useless (wrong, or its result is certified), call job_stop again "
                "with force=true."
            )
        if job.proc.poll() is None:
            try:
                os.killpg(job.proc.pid, signal.SIGTERM)
            except OSError:
                pass
        return f"Stopped job {name!r}."

    # ----- kept outputs -----
    def keep(self, path: str, score_cmd: str, higher_is_better: bool = False) -> str:
        p = Path(path).resolve()
        snap = self.dir / "best" / re.sub(r"[^A-Za-z0-9_.-]", "_", str(p))
        with self._lock:  # check_outputs iterates the registry in another thread
            old = self.outputs.get(str(p))
            if old and old.higher_is_better == higher_is_better:
                old.score_cmd = score_cmd  # re-registering must not forget the best so far
            else:
                self.outputs[str(p)] = Output(p, score_cmd, higher_is_better, snap)
        return "\n".join(self.check_outputs()) or f"Registered {p}; not scorable yet."

    def _score(self, out: Output, path: Path) -> float | None:
        cmd = out.score_cmd
        q = shlex.quote(str(path))
        cmd = cmd.replace("{path}", q) if "{path}" in cmd else f"{cmd} {q}"
        try:
            res = subprocess.run(
                ["bash", "-c", cmd], capture_output=True, text=True, timeout=SCORE_TIMEOUT_S
            )
            return float(res.stdout.strip().splitlines()[-1]) if res.returncode == 0 else None
        except (subprocess.TimeoutExpired, ValueError, IndexError):
            return None

    def check_outputs(self) -> list[str]:
        """Snapshot improvements; restore the best snapshot over a worse or broken file."""
        notes = []
        with self._lock:
            for out in self.outputs.values():
                # Score a copy (same file name), so a job replacing the file mid-check
                # can't leave an unscored version in the snapshot.
                probe = self.dir / "check" / out.path.name
                probe.parent.mkdir(exist_ok=True)
                try:
                    shutil.copyfile(out.path, probe)
                    score = self._score(out, probe)
                except OSError:
                    score = None
                out.current = score
                if score is not None and (out.best is None or out.better(score, out.best)):
                    os.replace(probe, out.snapshot)
                    notes.append(f"{out.path}: new best {score:g} (was {out.best}).")
                    out.best = score
                    self.improvements += 1
                elif score == out.best:
                    pass  # unchanged
                elif out.best is not None and out.snapshot.exists():
                    tmp = out.path.with_name(f".{out.path.name}.restore")
                    shutil.copyfile(out.snapshot, tmp)
                    os.replace(tmp, out.path)
                    out.current = out.best
                    notes.append(
                        f"{out.path}: score {score} is worse or invalid; "
                        f"restored best {out.best:g}."
                    )
        return notes

    async def keeper_loop(self) -> None:
        while True:
            await asyncio.to_thread(self.check_outputs)
            await asyncio.sleep(KEEP_EVERY_S)

    # ----- sub-agents -----
    def delegate(self, name: str, brief: str, minutes: float) -> str:
        if name in self.subagents and self.subagents[name].result is None:
            return f"Sub-agent {name!r} is still running."
        if self.deadline:
            minutes = min(minutes, (self.deadline - DEADLINE_MARGIN_S - monotonic()) / 60)
        if minutes < 1:
            return "Not enough time left to delegate."
        workdir = self.workdir / "sub" / name
        workdir.mkdir(parents=True, exist_ok=True)
        prompt = SUBAGENT_PROMPT.format(
            name=name, task=self.task_prompt, brief=brief, workdir=workdir, minutes=minutes
        )
        sub = SubAgent(task=None, started=monotonic(), minutes=minutes)  # type: ignore[arg-type]

        async def run() -> None:
            try:
                sub.result = await asyncio.wait_for(
                    self.run_subagent(name, prompt, monotonic() + minutes * 60), minutes * 60
                )
            except TimeoutError:
                sub.result = f"Timed out after {minutes:.0f} min; see {workdir}."
            except asyncio.CancelledError:
                sub.result = "Cancelled."
            except Exception as exc:  # report, don't crash the orchestrator
                sub.result = f"Failed: {type(exc).__name__}: {exc}"

        sub.task = asyncio.create_task(run())
        self.subagents[name] = sub
        return f"Sub-agent {name!r} started with {minutes:.0f} min in {workdir}."

    # ----- reporting -----
    def status_text(self, prefix: str = "") -> str:
        now = monotonic()
        running = sum(j.proc.poll() is None for j in self.jobs.values())
        lines = [f"CPUs available: {self.cpus} (trust this, not nproc); jobs running: {running}."]
        for name, job in self.jobs.items():
            if not name.startswith(prefix):
                continue
            rc = job.proc.poll()
            state = f"running {_fmt(now - job.started)}" if rc is None else f"exited {rc}"
            lines.append(f"job {name}: {state} | {_tail(job.log)}")
        for name, sub in self.subagents.items():
            if prefix:
                continue
            if sub.result is None:
                lines.append(f"sub-agent {name}: running {_fmt(now - sub.started)}")
            else:
                lines.append(f"sub-agent {name}: done. Report:\n{sub.result[:3000]}")
        for out in self.outputs.values():
            lines.append(f"kept {out.path}: best {out.best}, current {out.current}")
        return "\n".join(lines)

    def checkpoints(self, time_frac: float) -> list[str]:
        """Phase checkpoints the harness enforces by reminding every call."""
        notes = []
        if time_frac >= 0.15 and (
            not self.outputs or any(o.best is None for o in self.outputs.values())
        ):
            notes.append(
                "CHECKPOINT: 15% of the time is gone. Valid deliverables must exist by now, "
                "each registered with keep_best so the best version is never lost."
            )
        running = any(j.proc.poll() is None for j in self.jobs.values()) or any(
            s.result is None for s in self.subagents.values()
        )
        if time_frac >= 0.30 and not running:
            notes.append(
                "CHECKPOINT: 30% of the time is gone and nothing is running. Keep a "
                "production job (or sub-agent) improving the deliverables at all times."
            )
        return notes

    def state_text(self) -> str:
        try:
            text = (self.workdir / STATE_FILE).read_text()
        except OSError:
            return f"{STATE_FILE} does not exist yet; create it (plan, best results, open leads)."
        if len(text) > STATE_MAX_CHARS:
            text = text[:STATE_MAX_CHARS] + "\n[STATE.md truncated; keep it short]"
        return f"Your {STATE_FILE}:\n{text}"

    def status(self, time_frac: float | None) -> str:
        parts = ["# RUN STATE", self.status_text()]
        if time_frac is not None:
            parts += self.checkpoints(time_frac)
        parts.append(self.state_text())
        return "\n".join(parts)

    # ----- wait -----
    async def wait(self, minutes: float, job: str = "", log_pattern: str = "") -> str:
        start = monotonic()
        end = start + minutes * 60
        if self.deadline:
            end = min(end, self.deadline - DEADLINE_MARGIN_S)
        exited = {n for n, j in self.jobs.items() if j.proc.poll() is not None}
        offsets = {n: j.log.stat().st_size for n, j in self.jobs.items() if j.log.exists()}
        pattern = re.compile(log_pattern) if log_pattern else None
        reason = "time is up"
        while True:
            done = [
                n
                for n, j in self.jobs.items()
                if j.proc.poll() is not None and n not in exited and (not job or n == job)
            ]
            subs = [n for n, s in self.subagents.items() if s.result is not None and not s.seen]
            if done or subs:
                for n in subs:
                    self.subagents[n].seen = True
                reason = f"finished: {', '.join(done + subs)}"
                break
            if pattern and (hit := self._log_match(pattern, offsets, job)):
                reason = f"log match in {hit}"
                break
            if monotonic() >= end:
                break
            await asyncio.sleep(min(10, max(0.0, end - monotonic())))
        return f"Woke after {_fmt(monotonic() - start)} ({reason}).\n{self.status_text()}"

    def _log_match(self, pattern: re.Pattern, offsets: dict, job: str) -> str | None:
        for name, j in self.jobs.items():
            if job and name != job:
                continue
            try:
                with open(j.log, "rb") as fh:
                    fh.seek(offsets.get(name, 0))
                    new = fh.read().decode("utf-8", "replace")
            except OSError:
                continue
            offsets[name] = offsets.get(name, 0) + len(new.encode())
            if pattern.search(new):
                return name
        return None

    # ----- tools -----
    def tools(self, *, orchestrator: bool = True, prefix: str = "") -> list:
        """Function tools; a sub-agent gets them without `delegate` and with job names
        prefixed by its own name."""
        lr = self

        @harness_tool
        async def job_start(name: str, cmd: str, cwd: str = "", thought: str = "") -> str:
            """Start a long-running shell command as a tracked background job.

            Output goes to a log file; use job_status or wait to follow it. Make the job
            write every improved result atomically (temp file, then mv) into its outputs.
            """
            if lr.approve and not await lr.approve(cmd=cmd, cwd=cwd, thought=thought):
                return "Rejected by the user."
            return lr.start_job(prefix + name, cmd, cwd)

        @harness_tool
        def job_status() -> str:
            """Show every job and sub-agent (state, run time, last log lines) and kept outputs."""
            return lr.status_text(prefix)

        @harness_tool
        def job_stop(name: str, force: bool = False) -> str:
            """Stop a background job (its whole process group) or cancel a sub-agent.

            A job that has run for more than 30 minutes is only stopped with force=true.
            """
            return lr.stop(name if name in lr.subagents else prefix + name, force)

        @harness_tool
        async def wait(minutes: float, job: str = "", log_pattern: str = "") -> str:
            """Sleep without using the model until something happens, then report status.

            Wakes early when a job (or only `job`, if given) exits, a sub-agent finishes,
            or a new log line matches the regex `log_pattern`. Improvements of kept
            outputs do not wake it; they are in the status you get back.
            Waiting is free; prefer long waits over polling with sleep commands.
            """
            return await lr.wait(minutes, prefix + job if job else "", log_pattern)

        @harness_tool
        async def keep_best(path: str, score_cmd: str, higher_is_better: bool = False) -> str:
            """Guard a deliverable file: keep its best version and restore it if overwritten.

            Every minute `score_cmd` is run on a same-named copy of the file (the path
            replaces `{path}`, or is appended). It must print the score as its last line
            and exit 0 only if the file is valid. Lower is better unless higher_is_better.
            """
            return await asyncio.to_thread(lr.keep, path, score_cmd, higher_is_better)

        tools = [job_start, job_status, job_stop, wait, keep_best]
        if orchestrator:

            @harness_tool
            async def delegate(name: str, brief: str, minutes: float) -> str:
                """Start a sub-agent on a self-contained, verifiable sub-task; returns at once.

                The sub-agent has the same tools (except delegate), its own conversation
                and directory, and `minutes` of time. Several can run in parallel. Its final
                report appears in job_status, and wait wakes when it finishes.
                """
                return lr.delegate(name, brief, minutes)

            tools.append(delegate)
        return tools


def make_subagent_runner(
    base_agent: Agent, lr: LongRun, *, context, max_turns: int, hooks, input_filter_factory
):
    """Return `run(name, prompt, deadline) -> report` for `LongRun.delegate`."""

    async def run(name: str, prompt: str, deadline: float) -> str:
        agent = base_agent.clone(
            name=f"sub:{name}",
            tools=[*base_agent.tools, *lr.tools(orchestrator=False, prefix=f"{name}/")],
        )
        result = await Runner.run(
            agent,
            prompt,
            context=context,
            max_turns=max_turns,
            hooks=hooks,
            run_config=RunConfig(call_model_input_filter=input_filter_factory(deadline, name)),
        )
        return "" if result.final_output is None else str(result.final_output)

    return run
