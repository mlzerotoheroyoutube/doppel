"""Run pytest on a copy of a project, isolated from anything the PR controls.

Isolation rules:
* The project is copied to a fresh temp dir; the original is never touched.
* Receipts' own tests live outside the project and run with --noconftest and
  an empty config file, so a PR cannot hook them through conftest.py,
  pytest.ini or pyproject settings.
* Fixed PYTHONHASHSEED, no bytecode, no pytest cache, hard timeout.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

IGNORE = shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", "*.pyc", ".venv")


@dataclass
class TestResult:
    nodeid: str
    outcome: str  # passed | failed | error | skipped
    message: str = ""


@dataclass
class RunResult:
    returncode: int
    results: list[TestResult] = field(default_factory=list)
    output: str = ""
    timed_out: bool = False

    @property
    def failed(self) -> list[TestResult]:
        return [r for r in self.results if r.outcome in ("failed", "error")]

    @property
    def passed(self) -> list[TestResult]:
        return [r for r in self.results if r.outcome == "passed"]

    @property
    def green(self) -> bool:
        return not self.timed_out and self.returncode == 0 and bool(self.passed)


def copy_project(src: Path) -> Path:
    dst = Path(tempfile.mkdtemp(prefix="receipts-")) / "project"
    shutil.copytree(src, dst, ignore=IGNORE)
    return dst


def _parse_junit(path: Path) -> list[TestResult]:
    if not path.exists():
        return []
    out = []
    for case in ET.parse(path).getroot().iter("testcase"):
        name = f"{case.get('classname', '')}::{case.get('name', '')}"
        outcome, message = "passed", ""
        for tag in ("failure", "error", "skipped"):
            el = case.find(tag)
            if el is not None:
                outcome = {"failure": "failed", "error": "error", "skipped": "skipped"}[tag]
                message = (el.get("message") or "") + "\n" + (el.text or "")
                break
        out.append(TestResult(name, outcome, message.strip()[:2000]))
    return out


def run_pytest(project: Path, tests: Path | None = None, *, isolated: bool = False,
               timeout: int = 60, extra_args: list[str] | None = None) -> RunResult:
    """Run pytest against `project`.

    tests=None runs the project's own suite with its own config (that is what
    the PR author claims passes). With `tests` given and isolated=True, runs
    that external test dir with the project importable but none of its pytest
    configuration loaded.
    """
    junit = Path(tempfile.mkdtemp(prefix="receipts-junit-")) / "junit.xml"
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "PYTHONPATH": str(project),
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
           f"--junitxml={junit}", "-o", "junit_family=xunit1"]
    if isolated:
        empty_ini = junit.parent / "pytest.ini"
        empty_ini.write_text("[pytest]\n")
        cmd += ["--noconftest", "-c", str(empty_ini), "--rootdir", str(tests)]
    cmd += extra_args or []
    cmd.append(str(tests) if tests else ".")
    try:
        proc = subprocess.run(cmd, cwd=tests if isolated else project, env=env,
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        return RunResult(-1, [], (e.stdout or "") if isinstance(e.stdout, str) else "", timed_out=True)
    return RunResult(proc.returncode, _parse_junit(junit), (proc.stdout + proc.stderr)[-6000:])
