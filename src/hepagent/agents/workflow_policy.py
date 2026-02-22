"""Workflow policy hooks for coarse workflow-level progression control.

Core bash guardrails should stay generic; workflow-specific rules here should
be broad and resilient, not a strict command-by-command script.
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from typing import Protocol


READ_COMMANDS = {"ls", "cat", "sed", "head", "tail", "find", "rg", "tree", "stat", "wc", "grep"}


def _split_command_segments(cmd: str) -> list[str]:
    parts = re.split(r"\s*(?:&&|\|\||;|\|)\s*", cmd.strip())
    return [p for p in parts if p]


def _normalize_path_token(token: str) -> str:
    while token.startswith("./"):
        token = token[2:]
    return token


def _extract_path_args(cmd_name: str, tokens: list[str]) -> list[str]:
    if len(tokens) <= 1:
        return []
    args = tokens[1:]
    if cmd_name == "sed":
        script_consumed = False
        out: list[str] = []
        for t in args:
            if not script_consumed:
                if t.startswith("-"):
                    continue
                script_consumed = True
                continue
            if t.startswith("-"):
                continue
            out.append(t)
        return out
    return [t for t in args if not t.startswith("-")]


def _is_read_only_command(cmd: str) -> bool:
    segments = _split_command_segments(cmd)
    if not segments:
        return False
    for segment in segments:
        try:
            tokens = shlex.split(segment)
        except ValueError:
            return False
        if not tokens:
            continue
        if tokens[0] not in READ_COMMANDS:
            return False
    return True


def _cmd_reads_path(cmd: str, target_path: str) -> bool:
    target_norm = _normalize_path_token(target_path)
    target_name = Path(target_norm).name
    segments = _split_command_segments(cmd)
    for segment in segments:
        try:
            tokens = shlex.split(segment)
        except ValueError:
            continue
        if not tokens:
            continue
        if tokens[0] not in READ_COMMANDS:
            continue
        for p in _extract_path_args(tokens[0], tokens):
            pn = _normalize_path_token(p)
            if pn == target_norm or Path(pn).name == target_name:
                return True
    return False


def _is_orchestrator_preflight_command(cmd: str) -> bool:
    segments = _split_command_segments(cmd)
    for segment in segments:
        try:
            tokens = shlex.split(segment)
        except ValueError:
            continue
        if len(tokens) < 3:
            continue
        if tokens[0] not in {"python", "python3"}:
            continue
        if _normalize_path_token(tokens[1]) != "orchestrator/run.py":
            continue
        if "--config" in tokens:
            return True
    return False


def _extract_report_path_from_output(output: str) -> str | None:
    out = output or ""
    patterns = [
        r"See report:\s*([^\s]+)",
        r"\[INFO\]\s+JSON report:\s*([^\s]+)",
        r"output_report already exists:\s*([^\s]+)",
    ]
    for p in patterns:
        m = re.search(p, out)
        if m:
            return m.group(1).strip()
    return None


def _is_preflight_report_read_command(cmd: str) -> bool:
    segments = _split_command_segments(cmd)
    for segment in segments:
        try:
            tokens = shlex.split(segment)
        except ValueError:
            continue
        if not tokens or tokens[0] not in READ_COMMANDS:
            continue
        for p in _extract_path_args(tokens[0], tokens):
            pn = _normalize_path_token(p)
            if pn.endswith(".json") and ("preflight" in pn or "orchestrator_preflight" in pn):
                return True
    return False


def _is_json_report_read_command(cmd: str) -> bool:
    segments = _split_command_segments(cmd)
    for segment in segments:
        try:
            tokens = shlex.split(segment)
        except ValueError:
            continue
        if not tokens or tokens[0] not in READ_COMMANDS:
            continue
        for p in _extract_path_args(tokens[0], tokens):
            pn = _normalize_path_token(p)
            if pn.endswith(".json") and ("preflight" in pn or "orchestrator" in pn):
                return True
    return False


class WorkflowPolicy(Protocol):
    def evaluate(self, cmd: str, thought: str) -> str | None:
        ...

    def record_result(self, cmd: str, returncode: int, output: str = "") -> None:
        ...


class NoopWorkflowPolicy:
    def evaluate(self, cmd: str, thought: str) -> str | None:
        return None

    def record_result(self, cmd: str, returncode: int, output: str = "") -> None:
        return


class PreflightChainWorkflowPolicy:
    """Lightweight controller for preflight-plan tasks.

    This policy intentionally avoids strict sequencing. It enforces only
    high-level liveness:
    1) after a preflight run, read the produced JSON report before drifting,
    2) after reading a valid report, stop tool-calling and summarize.
    """

    def __init__(self) -> None:
        self.enabled = False
        self.report_path: str | None = None
        self.pending_finalize = False
        self.read_steps_since_enable = 0
        self.preflight_attempted = False
        self.manifest_ready = False

    def evaluate(self, cmd: str, thought: str) -> str | None:
        if not self.enabled:
            return None

        if _is_orchestrator_preflight_command(cmd):
            segments = _split_command_segments(cmd)
            for segment in segments:
                try:
                    tokens = shlex.split(segment)
                except ValueError:
                    continue
                if not tokens:
                    continue
                if "--no-open-report" in tokens or "--no-serve-report" in tokens:
                    return (
                        "Progress guard: for preflight workflows, do not disable report UI by default. "
                        "Run the preflight command without `--no-open-report/--no-serve-report` unless the user asked."
                    )
            return None

        if self.manifest_ready and not self.preflight_attempted:
            return (
                "Progress guard: preflight manifest is ready. "
                "Run `python3 orchestrator/run.py --config <manifest>` now."
            )

        if self.read_steps_since_enable >= 12 and not self.preflight_attempted:
            if not _is_orchestrator_preflight_command(cmd):
                return (
                    "Progress guard: enough context has been gathered for preflight workflow. "
                    "Run `python3 orchestrator/run.py --config <manifest>` next."
                )

        if self.pending_finalize:
            return (
                "Progress guard: preflight report has been read. "
                "Stop tool-calling and provide final summary now."
            )

        if self.report_path and not self.pending_finalize:
            if _is_orchestrator_preflight_command(cmd):
                # Allow reruns with a corrected manifest/path.
                return None
            if not (
                _cmd_reads_path(cmd, self.report_path)
                or _is_preflight_report_read_command(cmd)
                or _is_json_report_read_command(cmd)
            ):
                return (
                    "Progress guard: preflight execution is done. "
                    "Read the preflight JSON report next, then summarize."
                )
            return None

        return None

    def record_result(self, cmd: str, returncode: int, output: str = "") -> None:
        if _is_read_only_command(cmd):
            out = output or ""
            if (
                "Prepare a production preflight plan for the canonical chain" in out
                and "Run only orchestrator preflight" in out
            ):
                self.enabled = True
            if (
                "name: class_to_cosmicic" in out
                and "name: cosmicic_to_nyx" in out
                and "output_report:" in out
            ):
                self.manifest_ready = True

            if not self.enabled:
                return

            self.read_steps_since_enable += 1

            if self.report_path and _cmd_reads_path(cmd, self.report_path):
                if "\"handoff_results\"" in out and "\"qa\"" in out:
                    self.pending_finalize = True
            elif _is_preflight_report_read_command(cmd):
                if "\"handoff_results\"" in out and "\"qa\"" in out:
                    self.pending_finalize = True

        if not self.enabled:
            return

        if _is_orchestrator_preflight_command(cmd):
            self.preflight_attempted = True
            rp = _extract_report_path_from_output(output or "")
            if rp:
                self.report_path = rp


def build_workflow_policy() -> WorkflowPolicy:
    mode = os.getenv("HEPAGENT_WORKFLOW_POLICY", "auto").strip().lower()
    if mode in {"none", "off", "disabled"}:
        return NoopWorkflowPolicy()
    if mode in {"preflight_chain", "preflight"}:
        return PreflightChainWorkflowPolicy()
    # auto: preflight policy self-activates only on matching task text.
    return PreflightChainWorkflowPolicy()
