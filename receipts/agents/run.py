"""Start every Receipts agent in one Bureau.

    python -m receipts.agents.run

The gate connects to Agentverse through a mailbox (open the Agent Inspector
link it prints and click Connect -> Mailbox once). The six workers are
reachable only from inside this process.
"""

from __future__ import annotations

import os

os.environ.setdefault("GRPC_VERBOSITY", "ERROR")
os.environ.setdefault("GRPC_ENABLE_FORK_SUPPORT", "0")

from uagents import Bureau

from .gate import make_gate
from .teammates import make_teammates
from .workers import make_workers


def main() -> None:
    workers = {**make_workers(), **make_teammates()}
    gate = make_gate(workers)
    bureau = Bureau(port=gate._port if gate._port else 8001)
    bureau.add(gate)
    for agent in workers.values():
        bureau.add(agent)
    print(f"Receipts gate address: {gate.address}")
    bureau.run()


if __name__ == "__main__":
    main()
