"""The Receipts gate: the agent users talk to on ASI:One.

It speaks the Agent Chat Protocol, remembers each user, and coordinates the
worker agents. Calls to different workers run in parallel.
"""

from __future__ import annotations

import asyncio
import hmac
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from uagents import Agent, Context, Model, Protocol
from uagents_core.contrib.protocols.chat import (ChatAcknowledgement, ChatMessage, TextContent,
                                                 chat_protocol_spec)

from .. import blind, github_ops, llm, mutation, planner, publish, tamper
from ..models import Receipt
from ..verify import blind_receipts, decide, mutation_receipts, subq_gate, to_markdown
from . import access, memory, targets, understand
from .messages import (DifferentialRequest, DifferentialResult, FixDone, FixProgress, FixRequest, JudgeRequest,
                       JudgeResult, MutationRequest, MutationResult, PlanRequest, PlanResult, SuiteRequest,
                       SuiteResult, TamperRequest, TamperResult, WriteTestsRequest, WriteTestsResult)

TIMEOUT = 600
WATCH_SECONDS = float(os.environ.get("RECEIPTS_WATCH_SECONDS", "120"))
README = Path(__file__).with_name("AGENT_README.md")

HELP = """**Receipts** is a teammate that only ships work it can prove.

**Your day**
- `today: ship the coupon fixes, demo at 5pm, meetings 1-3pm`: I plan your day (Meta drafts, SubQ reviews)
- `plan`: show today's plan

**Your repos**
- `watch owner/repo`: I check every new PR on my own and tell you (or hold it for your digest during quiet hours)
- `inbox`: what the watcher found · `unwatch owner/repo`

**GitHub**
- `my repos` · `prs owner/repo` · `issues owner/repo` · `check prs in owner/repo`
- `new issue in owner/repo: title -- details`
- `new project <name>: <idea>`: Meta scaffolds a tested starter project, I create the repo and its first issues (asks you first)

**Work**
- `fix demo issue 1` or `fix issue 3 in owner/repo`: Meta writes the fix, Receipts collects evidence,
  SubQ judges each round, and it reworks until approved (max 3 rounds)
- paste a GitHub PR link, or `demo list` then `demo i1-bad-ignores-qty`, to verify one PR

**Any device**
- `link <name>` here, and use the same name from the HTTP API on any other device: one memory, everywhere

**Teach me**
- `strict on` / `strict off`: treat warnings as blocks
- `mute T004` / `unmute T004`: turn a rule down to a note for you
- `wrong` or `right` after a verdict: teach Receipts what you think
- `history`: your past verdicts
- `me`: what Receipts remembers about you"""


class WorkerError(RuntimeError):
    pass


# ---------------------------------------------------------------- HTTP API (any device)

class ApiChat(Model):
    token: str
    user: str
    text: str


class ApiPoll(Model):
    token: str
    user: str


class ApiReply(Model):
    ok: bool
    messages: list[str] = []
    error: str = ""


class ApiHealth(Model):
    ok: bool
    agent: str
    roles: dict


HANDLE = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")


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
        """Deliver to the channel the person used: chat protocol, or the HTTP outbox."""
        if to.startswith("rest:"):
            key = f"outbox:{to[5:]}"
            box = ctx.storage.get(key) or []
            ctx.storage.set(key, (box + [{"at": time.time(), "text": text}])[-200:])
            return
        await ctx.send(to, ChatMessage(content=[TextContent(text=text)],
                                       msg_id=uuid4(), timestamp=datetime.now(timezone.utc)))

    async def broadcast(ctx: Context, uid: str, text: str) -> None:
        """Proactive messages (watcher, digest) go to every device this person has used."""
        profile = memory.load(ctx.storage, uid)
        for channel in profile.get("channels") or [uid]:
            await reply(ctx, channel, text)

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

        # SubQ votes ship / don't ship on the whole evidence (rule J001). If SubQ is down,
        # the verdict stands on the evidence alone and says so.
        try:
            j = await asyncio.to_thread(subq_gate, target.base, target.head, target.issue,
                                        decide(list(receipts), dict(stats)))
            if j is not None:
                receipts.append(j)
        except Exception as e:  # noqa: BLE001
            ctx.logger.warning(f"SubQ gate unavailable: {e}")
            receipts.append(Receipt("judge", "J000", "info", "SubQ was unavailable; this verdict uses the evidence only."))
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
        top = next((r.title for r in verdict.receipts if r.severity in ("block", "warn")), "every claim checked out")
        if target.gh and f"{target.gh['owner']}/{target.gh['repo']}".lower() in {
                r.lower() for r in memory.load(ctx.storage, user)["repos"]}:
            # Only repos this person asked us to watch get a status and a comment.
            try:
                await asyncio.to_thread(publish.report_pr, target.gh["owner"], target.gh["repo"],
                                        target.gh["number"], target.gh["sha"], verdict.decision, top, md)
                md += "\n\n📌 Posted to GitHub: a **Receipts** status check and this report as a PR comment."
            except Exception as e:  # noqa: BLE001
                ctx.logger.warning(f"couldn't post to GitHub: {e}")
                md += f"\n\n(Couldn't post to GitHub: {e})"
        return md

    async def static_review(ctx: Context, user: str, target: targets.Target) -> str:
        """For repos the owner doesn't own: never execute their code. Tamper rules + SubQ on the diff."""
        receipts = await asyncio.to_thread(tamper.check, target.base, target.head, target.issue)
        try:
            j = await asyncio.to_thread(subq_gate, target.base, target.head, target.issue, decide(list(receipts), {}))
            if j is not None:
                receipts.append(j)
        except Exception as e:  # noqa: BLE001
            receipts.append(Receipt("judge", "J000", "info", f"SubQ was unavailable ({str(e)[:80]})."))
        verdict = decide(receipts, {"mode": "static"})
        run_pipeline.last_decision = verdict.decision
        return (to_markdown(verdict, target.label) +
                "\n*Static review only: this repository isn't yours, so its code was never run. Spec used: "
                f"{target.spec_source}.*")

    async def meta_chat(ctx: Context, sender: str, text: str, profile: dict) -> str:
        key = f"chatlog:{sender}"
        history = ctx.storage.get(key) or []
        try:
            answer = await asyncio.to_thread(understand.chat, text, profile, history)
        except Exception as e:  # noqa: BLE001
            ctx.logger.warning(f"meta chat failed: {e}")
            return "I couldn't reach Meta just now. Try again in a moment, or say `help`."
        ctx.storage.set(key, (history + [{"role": "user", "text": text}, {"role": "assistant", "text": answer}])[-16:])
        return answer

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

    OPEN = re.compile(r"^(help|\?|commands|demo( list|s)?|(check\s+)?demo\s+[\w-]+|me|profile|settings|policy|"
                      r"history|models|which models?|strict (on|off)|(un)?mute [a-z]\d{3}|wrong|right|correct|good catch|"
                      r"that's wrong|false block|bad verdict|(link|i am|i'm)\s+.+)$")

    async def handle(ctx: Context, sender: str, text: str, depth: int = 0) -> None:
        t = text.strip()[:8000]
        low = t.lower()
        profile = memory.load(ctx.storage, sender)
        owner = access.is_owner(ctx.storage, sender)
        if not owner and depth == 0 and not access.guest_allowed(ctx.storage, sender):
            await reply(ctx, sender, "You've reached the guest limit for this hour. Try again later.")
            return
        if not owner and depth > 0 and not targets.PR_URL.search(t) and not OPEN.match(low):
            await reply(ctx, sender, "That needs my owner.\n\n" + access.GUEST_HELP)
            return

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
            mine = bool(target.gh) and owner and \
                target.gh["owner"].lower() == (await asyncio.to_thread(github_ops.me)).lower()
            if target.gh and not mine:
                await reply(ctx, sender, f"Reviewing **{target.label}** statically (tamper rules + SubQ). "
                                         "I don't run code from repositories that aren't yours.")
                try:
                    await reply(ctx, sender, await static_review(ctx, sender, target))
                except Exception as e:  # noqa: BLE001
                    ctx.logger.exception("static review failed")
                    await reply(ctx, sender, f"Something failed while reviewing: {e}")
                return
            await reply(ctx, sender, f"Checking **{target.label}**.\n\n"
                                     "Six agents are on it: test suite · tamper detector · blind-test writer · "
                                     "before/after runner · bug planter, then SubQ decides. About a minute.")
            try:
                await reply(ctx, sender, await run_pipeline(ctx, sender, target))
            except Exception as e:  # noqa: BLE001 - always answer the user
                ctx.logger.exception("pipeline failed")
                await reply(ctx, sender, f"Something failed while checking: {e}\n\nNo verdict was recorded. Try again.")
            return

        if (m := re.fullmatch(r"(?:link|i am|i'm)\s+([a-z0-9_.-]{1,40})(?:\s+(\S+))?", low)):
            if sender.startswith("rest:"):
                await reply(ctx, sender, "You're already using your name on this device.")
                return
            if m.group(1) != access.owner() or not access.check_link_code(m.group(2) or ""):
                await reply(ctx, sender, "Linking needs the owner's name and the one-time code from the agent's "
                                         "`.env`: `link <name> <code>`.")
                return
            old = memory.load(ctx.storage, sender)
            memory.link(ctx.storage, sender, m.group(1))
            merged = memory.load(ctx.storage, sender)
            if not merged.get("channels") and old.get("history"):
                merged = old  # first link: bring this device's memory along
            memory.save(ctx.storage, sender, merged)
            await reply(ctx, sender, f"Linked. This chat and every device that uses the name **{m.group(1)}** "
                                     "now share one memory: plan, watched repos, settings and history.")
            return
        if not owner and not OPEN.match(low):
            if depth == 0:  # plain English from a guest: answer it, but never run owner commands
                try:
                    routed = await asyncio.to_thread(understand.route, t, {})
                except Exception:  # noqa: BLE001
                    routed = {"command": "", "answer": ""}
                cmd = routed["command"]
                if cmd and (OPEN.match(cmd.lower()) or targets.PR_URL.search(cmd)):
                    await handle(ctx, sender, cmd, depth + 1)
                    return
                await reply(ctx, sender, await meta_chat(ctx, sender, t, {}))
                return
            await reply(ctx, sender, "That needs my owner.\n\n" + access.GUEST_HELP)
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
        if low in ("my repos", "repos", "list repos", "my projects"):
            repos = await asyncio.to_thread(github_ops.list_repos)
            await reply(ctx, sender, "**Your repos**\n" + "\n".join(
                f"- [{r['nameWithOwner']}](https://github.com/{r['nameWithOwner']})"
                + (f" · {r['description']}" if r.get("description") else "") for r in repos))
            return
        if (m := re.match(r"(prs|pull requests|issues)\s+(?:in\s+|for\s+)?([\w.-]+/[\w.-]+)$", low)):
            full = m.group(2)
            if m.group(1) == "issues":
                items = await asyncio.to_thread(github_ops.list_issues, full)
                rows = [f"- [#{i['number']}]({i['url']}) {i['title']}"
                        + (f" · `{', '.join(l['name'] for l in i['labels'])}`" if i["labels"] else "") for i in items]
                tail = f"\n\nSay `fix issue <n> in {full}`, or add the label `receipts-fix` on GitHub."
            else:
                items = await asyncio.to_thread(github_ops.list_prs, full)
                def mark(pr):
                    for c in pr.get("statusCheckRollup") or []:
                        if c.get("context") == "Receipts":
                            return {"FAILURE": " · 🛑 Receipts BLOCK", "SUCCESS": " · ✅ Receipts"}.get(c.get("state"), "")
                    return " · not checked yet"
                rows = [f"- [#{p['number']}]({p['url']}) {p['title']}{mark(p)}" for p in items]
                tail = f"\n\nSay `check prs in {full}` to verify the unchecked ones."
            await reply(ctx, sender, f"**Open {m.group(1)} in {full}**\n" + ("\n".join(rows) or "none") + tail)
            return
        if (m := re.match(r"check (?:all )?(?:the )?(?:prs|pull requests) (?:in|for|on) ([\w.-]+/[\w.-]+)$", low)):
            full = m.group(1)
            prs = await asyncio.to_thread(github_ops.list_prs, full)
            todo = [p for p in prs if not any(c.get("context") == "Receipts" for c in p.get("statusCheckRollup") or [])][:3]
            if not todo:
                await reply(ctx, sender, f"Every open PR in {full} already has a Receipts check.")
                return
            await reply(ctx, sender, f"Checking {len(todo)} PR(s) in {full}…")
            for p in todo:
                await handle(ctx, sender, p["url"], depth + 1)
            return
        if (m := re.match(r"(?:new|open|create) (?:an )?issue (?:in|on) ([\w.-]+/[\w.-]+)\s*:\s*(.+)", t, re.I | re.S)):
            title, _, body = m.group(2).partition("--")
            try:
                url = await asyncio.to_thread(github_ops.new_issue, m.group(1), title.strip(), body.strip())
                await reply(ctx, sender, f"Opened {url}\n\nSay `fix issue {url.rsplit('/', 1)[-1]} in {m.group(1)}` "
                                         "and I'll work on it.")
            except Exception as e:  # noqa: BLE001
                await reply(ctx, sender, f"Couldn't open the issue: {e}")
            return
        if (m := re.match(r"(?:new|start|create) (?:a )?(?:project|repo)\s+([\w.-]+)\s*:\s*(.+)", t, re.I | re.S)):
            profile["pending"] = {"action": "new_project", "name": m.group(1), "idea": m.group(2).strip()}
            memory.save(ctx.storage, sender, profile)
            await reply(ctx, sender, f"I'll create the **public** repo `{m.group(1)}` on your GitHub account, have "
                                     f"{llm.who('implementer')} write a tested starter project for:\n> {m.group(2).strip()}\n\n"
                                     "and file its first 3 issues. Reply `yes` to go ahead, or `no`.")
            return
        if low in ("yes", "go ahead", "confirm", "no", "cancel") and profile.get("pending"):
            pending = profile.pop("pending")
            memory.save(ctx.storage, sender, profile)
            if low in ("no", "cancel"):
                await reply(ctx, sender, "Cancelled; nothing was created.")
                return
            await reply(ctx, sender, f"Creating `{pending['name']}`: Meta is writing the starter project and its tests…")
            try:
                res = await asyncio.to_thread(github_ops.create_project, pending["name"], pending["idea"])
            except Exception as e:  # noqa: BLE001
                await reply(ctx, sender, f"Couldn't create the project: {e}")
                return
            profile = memory.load(ctx.storage, sender)
            full = res["url"].split("github.com/")[1]
            profile["repos"][full] = {"seen": {}}
            memory.save(ctx.storage, sender, profile)
            await reply(ctx, sender, f"🎉 **{res['url']}**\n\n{res['description']}\n\n"
                                     f"- Starter code with **{res['tests_passed']} passing tests**\n"
                                     + "\n".join(f"- Issue: {u}" for u in res["issues"])
                                     + f"\n\nI'm watching it now. Say `fix issue 1 in {full}` to have me build the first feature.")
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
        elif low in ("models", "which models", "which model", "what models do you use"):
            roles = {"understanding your messages": "router", "planning your day": "planner",
                     "writing hidden exams": "exam", "writing and fixing code": "implementer",
                     "ship / don't-ship votes (no text)": "judge"}
            await reply(ctx, sender, "**Models I use**\n" + "\n".join(
                f"- {what}: {llm.who(role)}" for what, role in roles.items())
                + "\n\nEvery reply you read is written by my own code or by Meta. SubQ only votes.")
        elif low in ("me", "profile", "settings", "policy", "what do you know about me"):
            await reply(ctx, sender, describe(profile))
        elif low in ("help", "?", "commands") or depth > 0:
            await reply(ctx, sender, HELP)
        else:
            # Plain English: Meta Muse Spark maps it to a command or answers from memory.
            try:
                routed = await asyncio.to_thread(understand.route, t, profile)
            except Exception as e:  # noqa: BLE001 - never leave the user without an answer
                ctx.logger.warning(f"router failed: {e}")
                await reply(ctx, sender, HELP)
                return
            cmd = routed["command"]
            if cmd and cmd.lower() not in ("help",) and cmd.lower() != low:
                if routed["answer"]:
                    await reply(ctx, sender, routed["answer"])
                await handle(ctx, sender, cmd, depth + 1)
            else:
                await reply(ctx, sender, await meta_chat(ctx, sender, t, profile))

    @chat.on_message(ChatMessage)
    async def on_chat(ctx: Context, sender: str, msg: ChatMessage):
        await ctx.send(sender, ChatAcknowledgement(timestamp=datetime.now(timezone.utc), acknowledged_msg_id=msg.msg_id))
        # ASI:One includes the @mention in the text ("@agent1q… link …"); drop leading mentions.
        text = re.sub(r"^(\s*@[\w.:-]+[\s,:]*)+", "", msg.text()).strip()
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
        info = (msg.result or {}).get("repo_info") or {}
        if info.get("owner"):
            note = (f"🚀 Receipts opened {msg.pr_url} for this issue." if msg.pr_url else
                    "✋ Receipts couldn't get a fix approved within 3 rounds; a human should take this one.")
            try:
                await asyncio.to_thread(publish.comment_issue, info["owner"], info["repo"], info["number"],
                                        note + "\n\n" + msg.summary_md)
            except Exception as e:  # noqa: BLE001
                ctx.logger.warning(f"couldn't comment on the issue: {e}")
        if msg.pr_url:
            text = f"🚀 **Pull request opened:** {msg.pr_url}\n\n" + text
        await reply(ctx, msg.user, text)

    watching = {"busy": False}

    @gate.on_interval(period=WATCH_SECONDS)
    async def watch(ctx: Context):
        if watching["busy"]:  # a slow tick is still running; skip instead of piling up
            return
        watching["busy"] = True
        try:
            await watch_once(ctx)
        finally:
            watching["busy"] = False

    async def watch_once(ctx: Context):
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
                await broadcast(ctx, user, f"**Digest** (held while you were busy)\n{rows}")
            for repo, state in list(profile["repos"].items()):
                owner, name = repo.split("/")
                try:  # issues labelled receipts-fix: work started from GitHub
                    for iss in await asyncio.to_thread(publish.labeled_issues, owner, name):
                        started = state.setdefault("fixing", {})
                        if str(iss["number"]) in started:
                            continue
                        started[str(iss["number"])] = int(time.time())
                        memory.save(ctx.storage, user, profile)
                        label, base, issue, info = await asyncio.to_thread(
                            targets.issue_target, f"fix issue {iss['number']} in {repo}")
                        await asyncio.to_thread(publish.comment_issue, owner, name, iss["number"],
                                                "👋 Receipts picked this up: Meta writes a hidden exam and the fix, "
                                                "Receipts collects evidence, SubQ judges. I'll link the PR here.")
                        await broadcast(ctx, user, f"🏷️ **{repo}#{iss['number']}** was labelled `receipts-fix`. "
                                                   f"Working on it: {iss['title']}")
                        channel = (profile.get("channels") or [user])[0]
                        await ctx.send(addr["coder"], FixRequest(user=channel, label=label, issue=issue,
                                                                 base=str(base), repo_info=info))
                except Exception as e:  # noqa: BLE001
                    ctx.logger.warning(f"label watch {repo}: {e}")
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
                        await broadcast(ctx, user, f"👀 New PR in **{repo}**, checked on my own:\n\n{md}")
                    state = profile["repos"].get(repo, state)

    api_token = os.environ.get("RECEIPTS_API_TOKEN", "")

    def authorized(token: str) -> bool:
        return bool(api_token) and hmac.compare_digest(token.encode(), api_token.encode())

    def drain(ctx: Context, handle: str) -> list[str]:
        box = ctx.storage.get(f"outbox:{handle}") or []
        ctx.storage.set(f"outbox:{handle}", [])
        return [m["text"] for m in box]

    @gate.on_rest_get("/api/health", ApiHealth)
    async def api_health(ctx: Context) -> ApiHealth:
        return ApiHealth(ok=True, agent=gate.address, roles={r: llm.who(r) for r in llm.DEFAULT_ROLES})

    @gate.on_rest_post("/api/chat", ApiChat, ApiReply)
    async def api_chat(ctx: Context, req: ApiChat) -> ApiReply:
        """Send a message from any device. Quick answers come back at once; long work
        (verifying, fixing, planning) arrives later via /api/messages."""
        if not authorized(req.token):
            return ApiReply(ok=False, error="bad or missing token")
        if not HANDLE.match(req.user) or req.user.lower() != access.owner():
            return ApiReply(ok=False, error="user must be the owner's handle")
        channel = f"rest:{req.user}"
        task = asyncio.create_task(handle(ctx, channel, req.text))
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=4)
        except asyncio.TimeoutError:
            pass
        except Exception as e:  # noqa: BLE001
            return ApiReply(ok=False, messages=drain(ctx, req.user), error=str(e)[:300])
        return ApiReply(ok=True, messages=drain(ctx, req.user))

    @gate.on_rest_post("/api/messages", ApiPoll, ApiReply)
    async def api_messages(ctx: Context, req: ApiPoll) -> ApiReply:
        """Collect everything the agent has said to this person on the HTTP channel since last time."""
        if not authorized(req.token):
            return ApiReply(ok=False, error="bad or missing token")
        if not HANDLE.match(req.user):
            return ApiReply(ok=False, error="bad user")
        return ApiReply(ok=True, messages=drain(ctx, req.user))

    gate.include(chat, publish_manifest=True)
    return gate
