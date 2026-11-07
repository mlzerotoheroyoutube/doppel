"""Messages between Receipts agents.

The message schemas are the independence guarantee. WriteTests carries only
the issue and the interface text, and Judge carries only the spec, one test
and its failure. Those agents cannot see code because no message ever gives
it to them.
"""

from __future__ import annotations

from uagents import Model


class SuiteRequest(Model):
    head: str


class SuiteResult(Model):
    receipts: list[dict]
    stats: dict
    green: bool


class TamperRequest(Model):
    base: str
    head: str
    spec: str = ""


class TamperResult(Model):
    receipts: list[dict]


class WriteTestsRequest(Model):
    issue: str
    interface: str


class WriteTestsResult(Model):
    test_src: str
    error: str = ""


class DifferentialRequest(Model):
    test_src: str
    base: str
    head: str


class DifferentialResult(Model):
    outcomes: list[dict]
    error: str = ""


class JudgeRequest(Model):
    spec: str
    interface: str
    test_src: str
    failure: str


class JudgeResult(Model):
    valid: bool
    reason: str


class MutationRequest(Model):
    project: str
    targets: list[list[str]]
    test_src: str = ""  # empty: run the project's own suite
    limit: int = 40


class MutationResult(Model):
    mutants: list[dict]


# ---------------------------------------------------------------- personal layer + work loop

class PlanRequest(Model):
    user: str
    targets: str
    schedule: str
    inbox: str


class PlanResult(Model):
    user: str
    result: dict
    error: str = ""


class FixRequest(Model):
    user: str
    label: str
    issue: str
    base: str
    max_rounds: int = 3
    repo_info: dict = {}  # owner/repo/number/subdir/title when the issue lives on GitHub


class FixProgress(Model):
    user: str
    text: str


class FixDone(Model):
    user: str
    label: str
    result: dict
    summary_md: str
    error: str = ""
    pr_url: str = ""
