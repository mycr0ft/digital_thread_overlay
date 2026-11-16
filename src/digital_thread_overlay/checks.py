"""Checks: the thing that turns a promotion from a typed claim into evidence.

`ops.promote` refuses anything but objective evidence that passed, but until now the
verdict came from the operator: `--result pass` recorded a person's word. A check runs and
reports its own verdict, and the verdict is what the record carries.

Two shapes, because the paper's demonstrations need both:

    NameOccursCheck   a fact occurs in a file - the structure check the ladder demo uses
    CommandCheck      a program runs and its exit status is the verdict

A check that produced no verdict is not a pass. A command that could not be run reports
`passed=None`, which the gate treats as unsupported - a missing tool must not read as a
verified relationship.
"""
from __future__ import annotations

import hashlib
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class Verdict:
    """What a check concluded, and how it was obtained."""

    passed: bool | None
    method: str
    tool: str
    version: str = ""
    output: str = ""
    seconds: float = 0.0

    @property
    def output_digest(self) -> str:
        return hashlib.sha256(self.output.encode()).hexdigest()[:16] if self.output else ""


class Check(Protocol):
    name: str

    def run(self, subject: str, obj: str) -> Verdict: ...


class NameOccursCheck:
    """Does the subject's name occur in the artifact? The weakest objective check there is.

    Worth having precisely because it is weak: the paper's argument is that a gate which
    admits a name match is indistinguishable from one that admits everything, so the
    demonstration needs a check that passes trivially on a constructed pair and fails on
    unrelated artifacts.
    """

    name = "name-occurs"

    def __init__(self, artifact, needles: list[str] | None = None):
        self.artifact = Path(artifact)
        self.needles = needles                 # extra strings to require, beyond the subject

    def run(self, subject: str, obj: str) -> Verdict:
        try:
            text = self.artifact.read_text(errors="replace")
        except OSError as e:
            return Verdict(False, f"{self.name}: unreadable artifact", self.name,
                           output=str(e))
        wanted = [subject] + (self.needles or [])
        # match on the last path-like segment: a URN or qualified name is the useful needle
        found = []
        for w in wanted:
            needle = w.rsplit(":", 1)[-1].rsplit("/", 1)[-1]
            found.append((needle, needle in text))
        passed = all(ok for _, ok in found)
        listing = ", ".join(f"{n}={'found' if ok else 'absent'}" for n, ok in found)
        return Verdict(passed, f"{self.name} over {self.artifact.name}", self.name,
                       output=listing)


class CommandCheck:
    """Run a program; its exit status is the verdict.

    The output digest is recorded so a reader can tell two runs apart without keeping both
    logs, and a non-zero exit is a fail rather than an error: a check that ran and said no
    is evidence, and the gate needs to see it as such.
    """

    name = "command"

    def __init__(self, command: str | list[str], cwd=None, timeout: float = 120.0):
        self.command = shlex.split(command) if isinstance(command, str) else list(command)
        self.cwd = cwd
        self.timeout = timeout

    def run(self, subject: str, obj: str) -> Verdict:
        try:
            proc = subprocess.run(self.command, cwd=self.cwd, capture_output=True,
                                  text=True, timeout=self.timeout)
        except FileNotFoundError as e:
            return Verdict(None, f"command not runnable: {self.command[0]}", self.name,
                           output=str(e))
        except subprocess.TimeoutExpired:
            return Verdict(None, f"command timed out after {self.timeout}s", self.name)
        output = (proc.stdout or "") + (proc.stderr or "")
        return Verdict(proc.returncode == 0, " ".join(self.command), self.name,
                       output=output[-4000:])


def parse_check(spec: str, default_artifact: str | None = None) -> Check:
    """`name-occurs:<artifact>` or `command:<shell command>`.

    A tiny grammar rather than a config file: the point of a check is that a reviewer can
    read the command in the record and re-run it.
    """
    if spec.startswith("name-occurs:"):
        artifact = spec.split(":", 1)[1] or default_artifact
        if not artifact:
            raise ValueError("name-occurs needs an artifact path")
        return NameOccursCheck(artifact)
    if spec.startswith("command:"):
        return CommandCheck(spec.split(":", 1)[1])
    raise ValueError(f"unknown check spec {spec!r}; use name-occurs:<path> or command:<cmd>")
