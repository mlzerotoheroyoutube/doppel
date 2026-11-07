"""Worker agents. Each owns one check and knows nothing about the others."""

from __future__ import annotations

import asyncio
import os
from dataclasses import asdict
from pathlib import Path

from uagents import Agent, Context

from .. import blind, mutation, tamper
from ..verify import suite_step
from .messages import (DifferentialRequest, DifferentialResult, JudgeRequest, JudgeResult, MutationRequest,
                       MutationResult, SuiteRequest, SuiteResult, TamperRequest, TamperResult, WriteTestsRequest,
                       WriteTestsResult)


def _seed(name: str) -> str:
    return os.environ.get("RECEIPTS_SEED", "receipts-local-dev") + "/" + name


def ensure_loop() -> None:
    """uAgents expects a current event loop at construction (Python 3.14 no longer makes one)."""
    try:
        asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


def make_workers() -> dict[str, Agent]:
    ensure_loop()
    suite = Agent(name="receipts-suite", seed=_seed("suite"), handle_messages_concurrently=True,
                  description="Runs the PR's own test suite in a sandbox.")
    tamper_a = Agent(name="receipts-tamper", seed=_seed("tamper"), handle_messages_concurrently=True,
                     description="Static detection of test tampering and hard-coded answers.")
    writer = Agent(name="receipts-blind-writer", seed=_seed("writer"), handle_messages_concurrently=True,
                   description="Writes tests from the issue and public interface only. Never sees code.")
    runner = Agent(name="receipts-differential", seed=_seed("runner"), handle_messages_concurrently=True,
                   description="Runs blind tests on the code before and after the PR.")
    judge = Agent(name="receipts-judge", seed=_seed("judge"), handle_messages_concurrently=True,
                  description="Checks failing blind tests against the spec. Never sees code.")
    mutant = Agent(name="receipts-mutation", seed=_seed("mutation"), handle_messages_concurrently=True,
                   description="Plants bugs in changed code to test the tests.")

    @suite.on_message(SuiteRequest, replies=SuiteResult)
    async def on_suite(ctx: Context, sender: str, msg: SuiteRequest):
        receipts, stats, green = await asyncio.to_thread(suite_step, Path(msg.head))
        await ctx.send(sender, SuiteResult(receipts=[r.to_dict() for r in receipts], stats=stats, green=green))

    @tamper_a.on_message(TamperRequest, replies=TamperResult)
    async def on_tamper(ctx: Context, sender: str, msg: TamperRequest):
        receipts = await asyncio.to_thread(tamper.check, Path(msg.base), Path(msg.head))
        await ctx.send(sender, TamperResult(receipts=[r.to_dict() for r in receipts]))

    @writer.on_message(WriteTestsRequest, replies=WriteTestsResult)
    async def on_write(ctx: Context, sender: str, msg: WriteTestsRequest):
        try:
            src = await asyncio.to_thread(blind.write_tests_from, msg.issue, msg.interface)
            await ctx.send(sender, WriteTestsResult(test_src=src))
        except Exception as e:  # noqa: BLE001 - report, don't crash the agent
            ctx.logger.error(f"writer failed: {e}")
            await ctx.send(sender, WriteTestsResult(test_src="", error=str(e)[:500]))

    @runner.on_message(DifferentialRequest, replies=DifferentialResult)
    async def on_diff(ctx: Context, sender: str, msg: DifferentialRequest):
        outcomes, err = await asyncio.to_thread(blind.differential, msg.test_src, Path(msg.base), Path(msg.head))
        await ctx.send(sender, DifferentialResult(outcomes=[asdict(o) for o in outcomes], error=err))

    @judge.on_message(JudgeRequest, replies=JudgeResult)
    async def on_judge(ctx: Context, sender: str, msg: JudgeRequest):
        try:
            valid, reason = await asyncio.to_thread(blind.judge_one, msg.spec, msg.interface, msg.test_src, msg.failure)
        except Exception as e:  # noqa: BLE001 - keep the test if the judge is down
            valid, reason = True, f"judge unavailable ({str(e)[:120]}); kept the test"
        await ctx.send(sender, JudgeResult(valid=valid, reason=reason))

    @mutant.on_message(MutationRequest, replies=MutationResult)
    async def on_mutation(ctx: Context, sender: str, msg: MutationRequest):
        tests = blind.materialize(msg.test_src) if msg.test_src else None
        targets = [(f, n) for f, n in msg.targets]
        mutants = await asyncio.to_thread(mutation.run, Path(msg.project), targets, tests, msg.limit)
        await ctx.send(sender, MutationResult(mutants=[asdict(m) for m in mutants]))

    return {"suite": suite, "tamper": tamper_a, "writer": writer, "runner": runner,
            "judge": judge, "mutation": mutant}
