"""The planner and coder agents. Both think with Meta models and answer to SubQ.

* receipts-planner: drafts the day with Meta Muse Spark from what the personal
  layer remembers; SubQ judges the plan; one revision if rejected.
* receipts-coder: runs the work loop (Meta Muse Wonder writes, Receipts gathers
  evidence, SubQ judges, rework up to 3 rounds) and streams progress back.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from uagents import Agent, Context

from .. import loop, planner
from .messages import FixDone, FixProgress, FixRequest, PlanRequest, PlanResult
from .workers import _seed, ensure_loop


def make_teammates() -> dict[str, Agent]:
    ensure_loop()
    plan_a = Agent(name="receipts-planner", seed=_seed("planner"), handle_messages_concurrently=True,
                   description="Plans the user's day with Meta Muse Spark; SubQ reviews every plan.")
    coder = Agent(name="receipts-coder", seed=_seed("coder"), handle_messages_concurrently=True,
                  description="Fixes issues in a loop: Meta writes, Receipts checks, SubQ judges.")

    @plan_a.on_message(PlanRequest, replies=PlanResult)
    async def on_plan(ctx: Context, sender: str, msg: PlanRequest):
        try:
            result = await asyncio.to_thread(planner.make_plan, msg.targets, msg.schedule, msg.inbox)
            await ctx.send(sender, PlanResult(user=msg.user, result=result))
        except Exception as e:  # noqa: BLE001
            ctx.logger.error(f"planning failed: {e}")
            await ctx.send(sender, PlanResult(user=msg.user, result={}, error=str(e)[:400]))

    @coder.on_message(FixRequest, replies={FixProgress, FixDone})
    async def on_fix(ctx: Context, sender: str, msg: FixRequest):
        main_loop = asyncio.get_running_loop()

        def event(text: str) -> None:
            asyncio.run_coroutine_threadsafe(ctx.send(sender, FixProgress(user=msg.user, text=text)), main_loop)

        try:
            res = await asyncio.to_thread(loop.run, msg.issue, Path(msg.base), msg.max_rounds, event)
            await ctx.send(sender, FixDone(user=msg.user, label=msg.label, result=res.to_dict(),
                                           summary_md=loop.to_markdown_summary(res, msg.label)))
        except Exception as e:  # noqa: BLE001
            ctx.logger.error(f"fix loop failed: {e}")
            await ctx.send(sender, FixDone(user=msg.user, label=msg.label, result={}, summary_md="",
                                           error=str(e)[:400]))

    return {"planner": plan_a, "coder": coder}
