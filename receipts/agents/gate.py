"""The Receipts gate: the agent users talk to on ASI:One.

It speaks the Agent Chat Protocol, remembers each user, and coordinates the
worker agents. Calls to different workers run in parallel.
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (ChatAcknowledgement, ChatMessage, TextContent,
                                                 chat_protocol_spec)

from .. import blind, llm, mutation, planner, tamper
from ..models import Receipt
from ..verify import blind_receipts, decide, mutation_receipts, to_markdown
from . import memory, targets
from .messages import (DifferentialRequest, DifferentialResult, FixDone, FixProgress, FixRequest, JudgeRequest,
                       JudgeResult, MutationRequest, MutationResult, PlanRequest, PlanResult, SuiteRequest,
                       SuiteResult, TamperRequest, TamperResult, WriteTestsRequest, WriteTestsResult)

TIMEOUT = 900
WATCH_SECONDS = float(os.environ.get("RECEIPTS_WATCH_SECONDS", "120"))
README = Path(__file__).with_name("AGENT_README.md")

HELP = """**Receipts** is a teammate that only ships work it can prove.

**Your day**
- `today: ship the coupon fixes, demo at 5pm, meetings 1-3pm`: I plan your day (Meta drafts, SubQ reviews)
- `plan`: show today's plan

**Your repos**
- `watch owner/repo`: I check every new PR on my own and tell you (or hold it for your digest during quiet hours)
- `inbox`: what the watcher found · `unwatch owner/repo`

**Work**
- `fix demo issue 1` or `fix issue 3 in owner/repo`: Meta writes the fix, Receipts collects evidence,
  SubQ judges each round, and it reworks until approved (max 3 rounds)
- paste a GitHub PR link, or `demo list` then `demo i1-bad-ignores-qty`, to verify one PR

**Teach me**
- `strict on` / `strict off`: treat warnings as blocks
- `mute T004` / `unmute T004`: turn a rule down to a note for you
- `wrong` or `right` after a verdict: teach Receipts what you think
- `history`: your past verdicts
- `me`: what Receipts remembers about you"""


class WorkerError(RuntimeError):
    pass


def make_gate(workers: dict[str, Agent]) -> Agent:
    kwargs = dict(
        name="receipts-gate",
        seed=os.environ.get("RECEIPTS_GATE_SEED", "receipts-local-dev/gate"),
        handle_messages_concurrently=True,
        description="Independent verifier for AI-written pull requests. Paste a PR link; "
                    "get a BLOCK / WARN / PASS verdict with evidence.",
        readme_path=str(README) if README.exists() else None,
        publish_agent_details=True,
    )
    if os.environ.get("RECEIPTS_MAILBOX", "1") == "1":
        kwargs.update(mailbox=True, port=int(os.environ.get("RECEIPTS_PORT", "8001")))
    gate = Agent(**kwargs)
    addr = {k: a.address for k, a in workers.items()}
    chat = Protocol(spec=chat_protocol_spec)

    async def reply(ctx: Context, to: str, text: str) -> None:
        await ctx.send(to, ChatMessage(content=[TextContent(text=text)],
                                       msg_id=uuid4(), timestamp=datetime.now(timezone.utc)))

    async def ask(ctx: Context, worker: str, msg, result_type):
        res, status = await ctx.send_and_receive(addr[worker], msg, result_type, timeout=TIMEOUT)
        if res is None:
            raise WorkerError(f"the {worker} agent didn't answer ({getattr(status, 'detail', status)})")
        return res

    async def run_pipeline(ctx: Context, user: str, target: targets.Target) -> str:
        base, head = str(target.base), str(target.head)
        iface = await asyncio.to_thread(blind.interface, target.base)

        # Round 1: three independent agents in parallel.
        suite_r, tamper_r, writer_r = await asyncio.gather(
            ask(ctx, "suite", SuiteRequest(head=head), SuiteResult),
            ask(ctx, "tamper", TamperRequest(base=base, head=head, spec=target.issue), TamperResult),
            ask(ctx, "writer", WriteTestsRequest(issue=target.issue, interface=iface), WriteTestsResult),
        )
        receipts = [Receipt(**r) for r in suite_r.receipts] + [Receipt(**r) for r in tamper_r.receipts]
        stats = dict(suite_r.stats)
        changed = await asyncio.to_thread(
            tamper.changed_functions, tamper.py_files(target.base), tamper.py_files(target.head))
        stats["changed_functions"] = [f"{f}:{n}" for f, n in changed]
        test_src = writer_r.test_src

        # Round 2: run the blind tests and plant bugs, in parallel.
        jobs = {}
        if test_src:
            jobs["diff"] = ask(ctx, "runner", DifferentialRequest(test_src=test_src, base=base, head=head),
                               DifferentialResult)
        if changed and suite_r.green:
            jobs["pr_mut"] = ask(ctx, "mutation", MutationRequest(project=head, targets=[list(t) for t in changed]),
                                 MutationResult)
        done = dict(zip(jobs, await asyncio.gather(*jobs.values())))

        outcomes, err = [], writer_r.error and f"blind-test writer failed: {writer_r.error}"
        if "diff" in done:
            outcomes = [blind.BlindOutcome(**o) for o in done["diff"].outcomes]
            err = err or done["diff"].error

        # Round 3: the judge reviews each failing blind test (still code-blind),
        # while the mutation agent scores Receipts' own tests.
        async def judge_all():
            for o in outcomes:
                if blind.needs_judging(o):
                    j = await ask(ctx, "judge", JudgeRequest(spec=target.issue, interface=iface,
                                                              test_src=o.source, failure=o.message[:1500]),
                                  JudgeResult)
                    o.judged_valid, o.judge_reason = j.valid, j.reason

        async def blind_mutation():
            if test_src and not err and changed and suite_r.green:
                return await ask(ctx, "mutation", MutationRequest(project=head, targets=[list(t) for t in changed],
                                                                  test_src=test_src), MutationResult)

        # Different destinations (judge, mutation), so these can overlap safely.
        _, blind_mut = await asyncio.gather(judge_all(), blind_mutation())

        if test_src or err:
            r, s = blind_receipts(outcomes, err or "")
            receipts += r
            stats.update(s)
        pr_m = [mutation.Mutant(**m) for m in done["pr_mut"].mutants] if "pr_mut" in done else None
        b_m = [mutation.Mutant(**m) for m in blind_mut.mutants] if blind_mut else None
        r, s = mutation_receipts(pr_m, b_m)
        receipts += r
        stats.update(s)

        profile = memory.load(ctx.storage, user)
        receipts, notes = memory.apply_policy(profile, receipts)
        verdict = decide(receipts, stats)
        deciding = {"BLOCK": "block", "WARN": "warn"}.get(verdict.decision)
        memory.record(profile, target.label, verdict.decision,
                      sorted({r.rule for r in verdict.receipts if r.severity == deciding}))
        memory.save(ctx.storage, user, profile)

        md = to_markdown(verdict, target.label)
        md += f"\n*Spec used: {target.spec_source}. Six agents ran; the test writer and judge never saw the code.*\n"
        if notes:
            md += "\n**Your settings:** " + " ".join(notes) + "\n"
        md += "\nReply `wrong` or `right` to teach me, or `history` to see past verdicts."
        run_pipeline.last_decision = verdict.decision
        return md

    def describe(profile: dict) -> str:
        p = profile["policy"]
        lines = [f"- Today's targets: {profile['targets'] or 'not told yet'}",
                 f"- Watching: {', '.join(profile['repos']) or 'no repos'}",
                 f"- Plan: {'yes, ' + str(len(profile['plan'].get('plan', {}).get('blocks', []))) + ' blocks' if profile['plan'] else 'none yet'}",
                 f"- Strict mode: **{'on' if p['strict'] else 'off'}**",
                 f"- Muted rules: {', '.join(p['muted']) or 'none'}",
                 f"- Learned from your feedback: {', '.join(sorted(memory.learned_downgrades(profile))) or 'nothing yet'}",
                 f"- Verdicts given you: {len(profile['history'])}, feedback given: {len(profile['feedback'])}"]
        return "**What I remember about you**\n" + "\n".join(lines)

    async def handle(ctx: Context, sender: str, text: str) -> None:
        t = text.strip()
        low = t.lower()
        profile = memory.load(ctx.storage, sender)

        if targets.PR_URL.search(t) or (m := re.fullmatch(r"(?:check\s+)?demo\s+([\w-]+)", low)) and m.group(1) != "list":
            try:
                if targets.PR_URL.search(t):
                    await reply(ctx, sender, "Fetching the pull request…")
                    target = await asyncio.to_thread(targets.from_github, t)
                else:
                    target = await asyncio.to_thread(targets.from_demo, m.group(1))
            except targets.TargetError as e:
                await reply(ctx, sender, f"I couldn't load that: {e}")
                return
            await reply(ctx, sender, f"Checking **{target.label}**.\n\n"
                                     "Six agents are on it: test suite · tamper detector · blind-test writer · "
                                     "before/after runner · judge · bug planter. This takes about a minute.")
            try:
                await reply(ctx, sender, await run_pipeline(ctx, sender, target))
            except Exception as e:  # noqa: BLE001 - always answer the user
                ctx.logger.exception("pipeline failed")
                await reply(ctx, sender, f"Something failed while checking: {e}\n\nNo verdict was recorded. Try again.")
            return

        if low.startswith(("today:", "today ", "my day:", "plan my day")) or low.startswith(("targets:", "schedule:")):
            body = t.split(":", 1)[1].strip() if ":" in t else ""
            if low.startswith("schedule:"):
                profile["schedule"] = body
            elif low.startswith("targets:"):
                profile["targets"] = body
            elif body:
                profile["targets"] = profile["schedule"] = body
            memory.save(ctx.storage, sender, profile)
            inbox = "\n".join(f"- {i['decision']} · {i['target']}" for i in profile["inbox"][-10:])
            await reply(ctx, sender, f"Planning your day with {llm.who('planner')}; {llm.who('judge')} will review it…")
            await ctx.send(addr["planner"], PlanRequest(user=sender, targets=profile["targets"],
                                                        schedule=profile["schedule"], inbox=inbox))
            return
        if low in ("plan", "my plan", "show plan"):
            await reply(ctx, sender, planner.to_markdown(profile["plan"]) if profile["plan"]
                        else "No plan yet. Tell me about your day: `today: <targets, meetings, deadlines>`")
            return
        if (m := re.match(r"(un)?watch\s+(?:https?://github\.com/)?([\w.-]+)/([\w.-]+)", low)):
            repo = f"{m.group(2)}/{m.group(3)}"
            if m.group(1):
                profile["repos"].pop(repo, None)
                memory.save(ctx.storage, sender, profile)
                await reply(ctx, sender, f"Stopped watching **{repo}**.")
                return
            try:
                prs = await asyncio.to_thread(targets.open_prs, *repo.split("/"))
            except Exception as e:  # noqa: BLE001
                await reply(ctx, sender, f"I can't read {repo}: {e}")
                return
            # Existing PRs count as seen; only NEW PRs or new commits get checked.
            profile["repos"][repo] = {"seen": {str(pr["number"]): pr["sha"] for pr in prs}}
            memory.save(ctx.storage, sender, profile)
            await reply(ctx, sender, f"Watching **{repo}** ({len(prs)} open PRs already there). From now on I check "
                                     f"every new PR or new push within {int(WATCH_SECONDS // 60) or 1} min.")
            return
        if low in ("inbox", "status", "what's new", "whats new"):
            items = profile["inbox"][-10:]
            icon = {"BLOCK": "🛑", "WARN": "⚠️", "PASS": "✅"}
            await reply(ctx, sender, ("**Watcher inbox**\n" + "\n".join(
                f"- {icon.get(i['decision'], '•')} {i['decision']} · {i['target']}" for i in reversed(items)))
                if items else "Nothing new yet." + ("" if profile["repos"] else " Try `watch owner/repo`."))
            return
        if low.startswith("fix "):
            try:
                label, base, issue, repo_info = await asyncio.to_thread(targets.issue_target, t)
            except targets.TargetError as e:
                await reply(ctx, sender, str(e))
                return
            await reply(ctx, sender, f"Working on **{label}**.\n\n1. {llm.who('exam')} writes a hidden exam from the "
                                     f"issue\n2. {llm.who('implementer')} writes the fix\n3. Receipts collects evidence\n"
                                     f"4. {llm.who('judge')} judges, and I rework until it approves (max 3 rounds)")
            await ctx.send(addr["coder"], FixRequest(user=sender, label=label, issue=issue, base=str(base),
                                                     repo_info=repo_info))
            return

        if "demo list" in low or low in ("demo", "demos"):
            rows = "\n".join(f"- `{cid}`: {title}" for cid, title in targets.demo_ids())
            await reply(ctx, sender, "**Demo PRs** against a small online store (some honest, some cheating):\n"
                                     f"{rows}\n\nSay `demo <name>` to check one.")
        elif re.search(r"\bstrict\s+(on|off)\b", low):
            profile["policy"]["strict"] = "on" in low.split("strict", 1)[1][:5]
            memory.save(ctx.storage, sender, profile)
            await reply(ctx, sender, f"Strict mode is now **{'on' if profile['policy']['strict'] else 'off'}**.")
        elif (m := re.search(r"\b(un)?mute\s+([a-z]\d{3})\b", low)):
            rule = m.group(2).upper()
            muted = set(profile["policy"]["muted"])
            if m.group(1):
                muted.discard(rule)
                for fb in profile["feedback"]:
                    fb["rules"] = [r for r in fb["rules"] if r != rule]
            else:
                muted.add(rule)
            profile["policy"]["muted"] = sorted(muted)
            memory.save(ctx.storage, sender, profile)
            await reply(ctx, sender, f"`{rule}` is now {'back to normal' if m.group(1) else 'muted (a note only)'} for you.")
        elif low in ("wrong", "that's wrong", "false block", "bad verdict", "right", "correct", "good catch"):
            msg = memory.give_feedback(profile, wrong=low in ("wrong", "that's wrong", "false block", "bad verdict"))
            memory.save(ctx.storage, sender, profile)
            await reply(ctx, sender, msg)
        elif "history" in low:
            hist = profile["history"][-10:]
            if not hist:
                await reply(ctx, sender, "No verdicts yet. Paste a PR link or try `demo list`.")
            else:
                icon = {"BLOCK": "🛑", "WARN": "⚠️", "PASS": "✅"}
                rows = "\n".join(f"- {icon[h['decision']]} {h['decision']} · {h['target']}"
                                 + (f" · {', '.join(h['rules'])}" if h["rules"] else "") for h in reversed(hist))
                await reply(ctx, sender, f"**Your last {len(hist)} verdicts**\n{rows}")
        elif low in ("me", "profile", "settings", "policy", "what do you know about me"):
            await reply(ctx, sender, describe(profile))
        else:
            await reply(ctx, sender, HELP)

    @chat.on_message(ChatMessage)
    async def on_chat(ctx: Context, sender: str, msg: ChatMessage):
        await ctx.send(sender, ChatAcknowledgement(timestamp=datetime.now(timezone.utc), acknowledged_msg_id=msg.msg_id))
        text = msg.text()
        if text.strip():
            await handle(ctx, sender, text)

    @chat.on_message(ChatAcknowledgement)
    async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
        pass

    @gate.on_message(PlanResult)
    async def on_plan(ctx: Context, sender: str, msg: PlanResult):
        if msg.error:
            await reply(ctx, msg.user, f"Planning failed: {msg.error}")
            return
        profile = memory.load(ctx.storage, msg.user)
        profile["plan"] = msg.result
        memory.save(ctx.storage, msg.user, profile)
        await reply(ctx, msg.user, planner.to_markdown(msg.result))

    @gate.on_message(FixProgress)
    async def on_progress(ctx: Context, sender: str, msg: FixProgress):
        await reply(ctx, msg.user, msg.text)

    @gate.on_message(FixDone)
    async def on_done(ctx: Context, sender: str, msg: FixDone):
        if msg.error:
            await reply(ctx, msg.user, f"The work loop failed: {msg.error}")
            return
        profile = memory.load(ctx.storage, msg.user)
        status = msg.result.get("status", "failed")
        memory.record(profile, msg.label, {"shipped": "PASS"}.get(status, "BLOCK"), [])
        memory.save(ctx.storage, msg.user, profile)
        text = msg.summary_md
        if msg.pr_url:
            text = f"🚀 **Pull request opened:** {msg.pr_url}\n\n" + text
        await reply(ctx, msg.user, text)

    @gate.on_interval(period=WATCH_SECONDS)
    async def watch(ctx: Context):
        """Check watched repos for new PRs or new pushes; verify them; notify or hold for the digest."""
        icon = {"BLOCK": "🛑", "WARN": "⚠️", "PASS": "✅"}
        for user in memory.all_users(ctx.storage):
            profile = memory.load(ctx.storage, user)
            quiet = bool(profile["plan"]) and planner.in_quiet_hours(profile["plan"].get("plan", {}))
            if profile["digest"] and not quiet:
                rows = "\n".join(f"- {icon.get(d['decision'], '•')} {d['decision']} · {d['target']}"
                                  for d in profile["digest"])
                profile["digest"] = []
                memory.save(ctx.storage, user, profile)
                await reply(ctx, user, f"**Digest** (held while you were busy)\n{rows}")
            for repo, state in list(profile["repos"].items()):
                try:
                    prs = await asyncio.to_thread(targets.open_prs, *repo.split("/"))
                except Exception as e:  # noqa: BLE001 - try again next tick
                    ctx.logger.warning(f"watch {repo}: {e}")
                    continue
                for pr in prs:
                    if state["seen"].get(str(pr["number"])) == pr["sha"]:
                        continue
                    state["seen"][str(pr["number"])] = pr["sha"]
                    memory.save(ctx.storage, user, profile)
                    try:
                        target = await asyncio.to_thread(targets.from_github, pr["url"])
                        md = await run_pipeline(ctx, user, target)
                        decision = getattr(run_pipeline, "last_decision", "WARN")
                    except Exception as e:  # noqa: BLE001
                        ctx.logger.exception(f"auto-verify {pr['url']} failed")
                        md, decision = f"I couldn't check {pr['url']}: {e}", "WARN"
                    profile = memory.load(ctx.storage, user)
                    entry = {"target": f"{repo}#{pr['number']} {pr['title']}", "decision": decision, "url": pr["url"]}
                    profile["inbox"] = (profile["inbox"] + [entry])[-50:]
                    if quiet and decision != "BLOCK":
                        profile["digest"].append(entry)
                        memory.save(ctx.storage, user, profile)
                    else:
                        memory.save(ctx.storage, user, profile)
                        await reply(ctx, user, f"👀 New PR in **{repo}**, checked on my own:\n\n{md}")
                    state = profile["repos"].get(repo, state)

    gate.include(chat, publish_manifest=True)
    return gate
