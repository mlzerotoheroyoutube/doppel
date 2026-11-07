from __future__ import annotations

from dataclasses import asdict, dataclass, field

SEVERITY_ORDER = {"block": 0, "warn": 1, "info": 2}


@dataclass
class Receipt:
    """One piece of evidence behind a verdict."""

    check: str          # which checker produced it: tamper | blind | mutation | suite
    rule: str           # stable id, e.g. T001
    severity: str       # block | warn | info
    title: str          # one plain-English sentence for the report card
    detail: str = ""    # longer explanation
    evidence: str = ""  # code, diff excerpt or failing output
    file: str = ""
    line: int = 0
    repro: str = ""     # command that reproduces it

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Verdict:
    decision: str                      # BLOCK | WARN | PASS
    receipts: list[Receipt] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"decision": self.decision, "stats": self.stats,
                "receipts": [r.to_dict() for r in self.receipts]}
