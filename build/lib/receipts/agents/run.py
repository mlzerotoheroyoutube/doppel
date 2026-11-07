"""Start every Receipts agent in one process.

    python -m receipts.agents.run

The gate runs on its own server (port 8001) with an Agentverse mailbox, because
the Agentverse Inspector does not support Bureaus. The eight helper agents run
in a Bureau on port 8002. All agents live in one process, so messages between
them are delivered in memory and never leave the machine.
"""

from __future__ import annotations

import asyncio
import os

os.environ.setdefault("GRPC_VERBOSITY", "ERROR")
os.environ.setdefault("GRPC_ENABLE_FORK_SUPPORT", "0")

from uagents import Bureau  # noqa: E402

from .gate import make_gate  # noqa: E402
from .teammates import make_teammates  # noqa: E402
from .workers import make_workers  # noqa: E402


def main() -> None:
    workers = {**make_workers(), **make_teammates()}
    gate = make_gate(workers)
    helpers = Bureau(port=int(os.environ.get("RECEIPTS_HELPER_PORT", "8002")))
    for agent in workers.values():
        helpers.add(agent)
    print(f"Receipts gate address: {gate.address}")
    print("Inspector: https://agentverse.ai/inspect/?uri=http%3A//127.0.0.1%3A"
          f"{gate._port}&address={gate.address}", flush=True)

    async def both():
        await asyncio.gather(gate.run_async(), helpers.run_async())

    asyncio.get_event_loop().run_until_complete(both())


if __name__ == "__main__":
    main()
