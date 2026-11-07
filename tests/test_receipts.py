"""Tests for Receipts itself: the safety rules the product depends on."""

from pathlib import Path

import pytest

from receipts import loop, tamper
from receipts.agents import access, memory


class Store:
    """Minimal stand-in for uAgents storage."""

    def __init__(self):
        self.d = {}

    def get(self, k):
        return self.d.get(k)

    def set(self, k, v):
        self.d[k] = v


# ---------------------------------------------------------------- access control

def test_owner_needs_link(monkeypatch):
    monkeypatch.setenv("RECEIPTS_OWNER", "sandeep")
    s = Store()
    assert not access.is_owner(s, "agent1stranger")
    memory.link(s, "agent1phone", "sandeep")
    assert access.is_owner(s, "agent1phone")
    assert access.is_owner(s, "rest:sandeep")
    assert not access.is_owner(s, "rest:mallory")


def test_link_code(monkeypatch):
    monkeypatch.setenv("RECEIPTS_LINK_CODE", "424242")
    assert access.check_link_code("424242")
    assert not access.check_link_code("000000")
    assert not access.check_link_code("")
    monkeypatch.setenv("RECEIPTS_LINK_CODE", "")
    assert not access.check_link_code("")  # no code configured: nobody can link


def test_no_owner_configured_means_nobody_is_owner(monkeypatch):
    monkeypatch.setenv("RECEIPTS_OWNER", "")
    assert not access.is_owner(Store(), "rest:sandeep")


def test_guest_budget(monkeypatch):
    monkeypatch.setattr(access, "GUEST_REQUESTS_PER_HOUR", 3)
    s = Store()
    assert [access.guest_allowed(s, "g") for _ in range(4)] == [True, True, True, False]
    assert access.guest_allowed(s, "other")


# ---------------------------------------------------------------- the hidden exam stays hidden

def test_feedback_never_leaks_exam():
    fb = ("Percent discounts must count every unit. See test_issue_quantities in test_receipts_blind.py.\n"
          "```python\nassert cart.discount_cents() == 610\n```")
    out = loop.sanitize_feedback(fb)
    assert "test_issue_quantities" not in out and "test_receipts_blind" not in out and "610" not in out
    assert "count every unit" in out


# ---------------------------------------------------------------- tamper rules

def _project(tmp: Path, name: str, src: str, test: str) -> Path:
    d = tmp / name
    (d / "pkg").mkdir(parents=True)
    (d / "tests").mkdir()
    (d / "pkg" / "__init__.py").write_text("")
    (d / "pkg" / "m.py").write_text(src)
    (d / "tests" / "test_m.py").write_text(test)
    return d


BASE_SRC = "def f(x):\n    return x * 2\n"
BASE_TEST = "from pkg.m import f\n\ndef test_f():\n    assert f(2) == 4\n"


def rules(base: Path, head: Path, spec: str = "") -> set:
    return {r.rule for r in tamper.check(base, head, spec)}


def test_hard_coded_answer_is_blocked(tmp_path):
    base = _project(tmp_path, "b", BASE_SRC, BASE_TEST)
    head = _project(tmp_path, "h", "def f(x):\n    if x == 7:\n        return 99\n    return x * 2\n",
                    BASE_TEST + "\ndef test_seven():\n    assert f(7) == 99\n")
    assert "T007" in rules(base, head)


def test_literal_named_in_the_issue_is_allowed(tmp_path):
    base = _project(tmp_path, "b", BASE_SRC, BASE_TEST)
    head = _project(tmp_path, "h", "def f(x):\n    if x == 'pm':\n        x = 'PM'\n    return x * 2\n",
                    BASE_TEST + "\ndef test_pm():\n    assert f('pm') == 'PMPM'\n")
    assert "T007" in rules(base, head)  # without the spec it looks copied
    assert "T007" not in rules(base, head, "Times like 2:30 pm must parse")  # the issue names 'pm'


def test_returning_a_tests_exact_answer_is_blocked_even_if_named(tmp_path):
    base = _project(tmp_path, "b", BASE_SRC, BASE_TEST)
    head = _project(tmp_path, "h", "def f(x):\n    if x == 'pm':\n        return 12\n    return x * 2\n",
                    BASE_TEST + "\ndef test_pm():\n    assert f('pm') == 12\n")
    assert "T007" in rules(base, head, "Times like 2:30 pm must parse")


def test_skipped_and_deleted_tests_are_blocked(tmp_path):
    base = _project(tmp_path, "b", BASE_SRC, BASE_TEST)
    skipped = _project(tmp_path, "s", BASE_SRC,
                       "import pytest\nfrom pkg.m import f\n\n@pytest.mark.skip\ndef test_f():\n    assert f(2) == 4\n")
    deleted = _project(tmp_path, "d", BASE_SRC, "from pkg.m import f\n")
    assert "T002" in rules(base, skipped)
    assert "T001" in rules(base, deleted)


def test_weakened_assertion_is_blocked(tmp_path):
    base = _project(tmp_path, "b", BASE_SRC, BASE_TEST)
    head = _project(tmp_path, "h", "def f(x):\n    return x * 3\n",
                    "from pkg.m import f\n\ndef test_f():\n    assert f(2) > 0\n")
    assert "T003" in rules(base, head)


def test_honest_change_passes(tmp_path):
    base = _project(tmp_path, "b", BASE_SRC, BASE_TEST)
    head = _project(tmp_path, "h", "def f(x):\n    return x + x\n",
                    BASE_TEST + "\ndef test_zero():\n    assert f(0) == 0\n")
    assert not {r for r in rules(base, head) if r != "T004"}


@pytest.mark.parametrize("channel,expected", [("rest:sandeep", "sandeep"), ("agent1abc", "agent1abc")])
def test_canonical_identity(channel, expected):
    assert memory.canonical(Store(), channel) == expected
