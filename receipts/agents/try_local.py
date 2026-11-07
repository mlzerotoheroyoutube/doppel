"""Talk to the gate locally, without Agentverse, the way ASI:One would.

    python -m receipts.agents.try_local "demo i1-bad-hardcoded" "wrong" "me"
"""

from __future__ import annotations

import os

os.environ.setdefault("GRPC_VERBOSITY", "ERROR")
os.environ.setdefault("GRPC_ENABLE_FORK_SUPPORT", "0")

import sys
from datetime import datetime, timezone
from uuid import uuid4

os.environ["RECEIPTS_MAILBOX"] = "0"

if fake := os.environ.get("RECEIPTS_FAKE_MODULE"):  # wiring tests only: stand-in model replies
    import importlib.util
    spec = importlib.util.spec_from_file_location("fake_models", fake)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from receipts import llm as _llm
    _llm.install_fake(mod.fake)

from uagents import Agent, Bureau, Context  # noqa: E402
from uagents_core.contrib.protocols.chat import (ChatAcknowledgement, ChatMessage, TextContent,  # noqa: E402
                                                 chat_protocol_spec)
from uagents import Protocol  # noqa: E402

from .gate import make_gate  # noqa: E402
from .teammates import make_teammates  # noqa: E402
from .workers import make_workers  # noqa: E402

PROMPTS = sys.argv[1:] or ["help", "demo i1-bad-hardcoded", "history"]
VERDICT_MARKERS = ("New PR in", "Digest", "Receipts:", "Today's plan", "Shipped", "Stopped after", "loop failed", "Planning failed",
                   "Watching", "Nothing new", "Watcher inbox", "Noted", "Strict", "muted", "back to normal", "What I remember",
                   "Your last", "No verdicts", "Demo PRs", "couldn't", "Something failed", "**Receipts** checks")


def main() -> None:
    workers = {**make_workers(), **make_teammates()}
    gate = make_gate(workers)
    user = Agent(name="local-user", seed="receipts-local-user")
    proto = Protocol(spec=chat_protocol_spec)
    queue = list(PROMPTS)

    async def send_next(ctx: Context):
        if not queue:
            print("\n=== done ===", flush=True)
            os._exit(0)
        text = queue.pop(0)
        if text == "@wait":  # send nothing; wait for the agent to speak on its own
            print("\n... waiting for the agent", flush=True)
            return
        print(f"\n>>> {text}", flush=True)
        await ctx.send(gate.address, ChatMessage(content=[TextContent(text=text)], msg_id=uuid4(),
                                                 timestamp=datetime.now(timezone.utc)))

    @user.on_event("startup")
    async def start(ctx: Context):
        await send_next(ctx)

    @proto.on_message(ChatMessage)
    async def got(ctx: Context, sender: str, msg: ChatMessage):
        await ctx.send(sender, ChatAcknowledgement(acknowledged_msg_id=msg.msg_id, timestamp=datetime.now(timezone.utc)))
        text = msg.text()
        print(f"\n<<< {text}", flush=True)
        if any(mark in text for mark in VERDICT_MARKERS):
            await send_next(ctx)

    @proto.on_message(ChatAcknowledgement)
    async def acked(ctx: Context, sender: str, msg: ChatAcknowledgement):
        pass

    user.include(proto)
    bureau = Bureau(port=8099)
    for a in [gate, user, *workers.values()]:
        bureau.add(a)
    bureau.run()


if __name__ == "__main__":
    main()
