# Doppel: Product Spec

**One line:** your double for engineering work. A personal agent that knows your repos, your day and your
rules, works behind the scenes, double-verifies everything (Meta Muse does the work, SubQ decides what ships,
deterministic checks supply the facts), and only asks you when it must. It all lives in one chat on ASI:One.

Status: live (Nov 7, 2026). Repo: https://github.com/mlzerotoheroyoutube/doppel ·
Agent: [doppel on ASI:One](https://asi1.ai/ai/agent1q0qv0zfjg8vwzdx9dle5yxreyfwmg3mnrlfqkqeayl6hlq6vaztuk3wxcsg)
· Hackathon tracks: **Trust but Verify** (primary), **The System That Gets Better** (secondary).

---

## 1. Problem

- Coding agents open pull requests faster than people can review them, and "all tests pass" is easy to fake:
  edited or skipped tests, weakened assertions, patched `conftest.py`, code that detects the test runner,
  answers hard-coded from the issue.
- An AI reviewer that reads the diff tends to believe the diff's story, and blocks honest work too
  (our baseline wrongly blocked a third of honest PRs).
- Teams need a merge gate that is independent of the coder, explains itself with evidence, and gets better
  over time without quietly getting worse.

## 2. Users and jobs

| User | Job to be done |
|---|---|
| Engineer / tech lead (owner) | "Tell me if this AI PR is safe to merge, with proof." "Fix this issue and open a PR I can trust." |
| Team using coding agents | A gate on every PR in CI that is hard to game |
| Busy builder | "Plan my day around my meetings; tell me only when something is blocked." |

## 3. What it does (features)

| Area | Feature | Status |
|---|---|---|
| Verify | Verify a GitHub PR: BLOCK / WARN / PASS with evidence and repro commands | ✅ live |
| Verify | Official **Doppel** commit status + report comment on the PR (watched repos only) | ✅ live |
| Verify | Static-only review for repos the owner doesn't own (their code is never run) | ✅ live |
| Watch | Watch repos; check every new PR / push on its own; alert or hold for a digest in quiet hours | ✅ live |
| Fix | "Fix issue N": hidden exam, Meta writes the fix, evidence, SubQ judges, rework ≤3, opens a real PR | ✅ live |
| Fix | Start a fix from GitHub by labelling an issue `doppel-fix`; progress posted on the issue | ✅ live |
| Build | "New project <name>: <idea>": repo + Meta-written starter with passing tests + 3 starter issues (asks first) | ✅ live |
| GitHub | List repos, open PRs (with Doppel status), open issues; open issues from chat | ✅ live |
| Plan | "today: …": Meta drafts a day plan, SubQ reviews it, one revision; quiet hours drive alerts | ✅ live |
| Chat | Plain English for everything; full Meta answers with conversation memory for anything else | ✅ live |
| Memory | Per-person memory (plan, repos, strictness, muted rules, feedback) across devices | ✅ live |
| Teach | `wrong` / `right` after a verdict; a rule marked wrong twice warns instead of blocking for that person | ✅ live |
| Learn | Self-improving playbook: SubQ stops bad work, Meta writes lessons, held-out validation adopts or rolls back | ✅ built, benchmarked |
| Select | Meta proposes (Wonder + Spark), evidence + SubQ pick what ships | ✅ built, benchmarking |
| CI | GitHub Actions workflow that runs Doppel on every PR in GitHub's cloud | ✅ template (`docs/receipts-workflow.yml`) |

## 4. Interfaces

| Interface | How | Notes |
|---|---|---|
| **ASI:One** (web, iOS, Android) | Chat addressed to `doppel` (Agent Chat Protocol via Agentverse mailbox) | Primary. Messages to `@doppel` are answered by Meta; unaddressed messages go to ASI:One's own assistant |
| **HTTP API** | `POST /api/chat`, `POST /api/messages`, `GET /api/health` on the gate (port 8001) | Token-protected; any device, Shortcut or bot |
| **CLI** | `python -m receipts verify --github-pr <url> --gate` | One-off checks, CI |
| **GitHub** | Status checks, PR comments, issue comments, `doppel-fix` label, PRs opened by the agent | Writes only to the owner's repos |

### Chat commands (plain English also works)

`help` · `models` · `link <owner> <code>` · `today: …` · `plan` · `watch owner/repo` · `unwatch owner/repo` ·
`inbox` · `my repos` · `prs owner/repo` · `issues owner/repo` · `check prs in owner/repo` ·
`new issue in owner/repo: title -- details` · `new project <name>: <idea>` · `fix issue N [in repo]` ·
`fix demo issue N` · `<GitHub PR link>` · `demo list` · `demo <name>` · `history` · `me` · `strict on|off` ·
`mute T00x` · `unmute T00x` · `wrong` · `right`

## 5. Architecture

```
ASI:One (web / phone) ──mailbox──▶ doppel (uAgents, port 8001)  ◀── HTTP API (any device)
                                      │ chat protocol · memory · access control · watcher · router
        ┌──────────────┬──────────────┼──────────────┬───────────────┬──────────────┐
        ▼              ▼              ▼              ▼               ▼              ▼
     suite          tamper       blind-writer    differential      judge        mutation      (6 verifier agents)
   (pytest)      (T001–T008)   (Meta; issue    (exam before/    (SubQ; never   (planted
                                 only)          after)           sees code)     bugs)
        └──────────── evidence ──────────────▶ SubQ gate (J001): ship / don't ship
     planner (Meta drafts → SubQ reviews)      coder (Meta writes → evidence → SubQ → rework → PR)
                                      │
                                      ▼
                    GitHub (gh login): status checks · comments · branches · PRs · issues
```

- **Runtime:** one Python process. The gate runs on its own server (port 8001, Agentverse mailbox); the 8
  helper agents run in a Bureau (port 8002); messages between them stay in memory.
- **Supervisor:** `scripts/run_forever.sh` restarts on crash and keeps the Mac awake; logs in `logs/`.
- **State:** uAgents storage per person (profile, memory, outbox, chat history); `playbook/` for learned
  lessons; `.receipts_cache/` replay cache of every model call.

## 6. Models and roles

| Role | Model | Writes text? |
|---|---|---|
| Chat, plain-English routing, day planning, hidden exams, decisions | Meta `muse-spark-1.3` | yes |
| Coding loop, project scaffolding | Meta `muse-wonder-1.0` (falls back to Spark; the playbook can switch) | yes |
| Judge: ship/don't-ship gate, loop rounds, exam validity, plan review, picking between attempts | SubQ `subq-snap-1.1` via `POST /v1/systemone` | **no**, it only returns probabilities and choices |
| Ground truth | Deterministic: pytest, AST tamper rules, AST mutation, before/after differential | n/a |

Every reply a person reads is written by our code or by Meta. `models` in chat shows the live mapping.
Every call is cached by a hash of its inputs, so any verdict can be replayed exactly.

## 7. The verifier (how a PR is judged)

| Check | Catches | Model |
|---|---|---|
| Suite | The PR's own tests red | none |
| Tamper T001–T008 | Deleted/skipped/weakened tests, changed expectations, edited test config/CI, test-runner detection, answers copied from tests (spec-aware), "fixes" with no code change | none |
| Hidden exam | Meta writes tests from the issue + public interface only (it never sees code or diff); run before and after the PR: pass→fail = regression, fail→fail = not fixed | Meta |
| Exam validity | Throws out exam tests that misread the spec | SubQ |
| Planted bugs | Mutates changed functions; scores the PR's tests and the exam | none |
| **SubQ gate (J001)** | Reads issue + evidence + diff + repo; votes ship / don't ship with the main problem | SubQ |

Verdict: any block → **BLOCK**; any warning → **WARN**; else **PASS**. Per-person policy (strict mode,
muted rules, learned downgrades) is applied before the verdict.

## 8. The work loop and SubQ

1. Meta writes a hidden exam from the issue, then freezes it.
2. Meta proposes the fix (and, in selection mode, Wonder and Spark both propose; the exam scores each, SubQ picks on a tie).
3. Doppel collects evidence; **SubQ judges**. Approved and not blocked → a branch is pushed and a PR opened.
4. Rejected → **stop**: nothing ships; SubQ's diagnosis (never the exam) goes back to Meta; up to 3 rounds,
   then it hands back to the person.
5. Every rejection is queued in `playbook/inbox.jsonl` for the improvement loop.

## 9. Self-improvement (The System That Gets Better)

- **Playbook** (`playbook/playbook.json`): per domain, short lessons Meta reads before working, plus a Meta
  model choice; versioned with a full history of adopted and rejected changes.
- **Loop** (`benchmark/improve.py`): solve training tasks → SubQ judges and stops → Meta writes ≤3 general
  lessons (and a model-switch candidate) → each candidate is scored on a **held-out validation set** →
  adopted only if it beats the current version, otherwise **rolled back** → final comparison on a test set
  never used for learning or validation.
- **Safety in practice:** in the decisions domain, lessons learned from SubQ false alarms dropped validation
  from 100% to 90.5% and were rolled back automatically.

## 10. Security and trust

| Risk | Control |
|---|---|
| Public agent: strangers driving your GitHub | Owner-only commands; a chat becomes the owner's only via `link <owner> <code>` (secret in `.env`); HTTP requires the token and the owner's handle |
| Running a stranger's PR code on your machine | Never: PRs from repos the owner doesn't own get static review only (tamper + SubQ on the diff) |
| Cost abuse | Guest budget (12 requests/hour/channel) |
| PR hooking the verifier | Exam runs in a temp copy with `--noconftest`, empty pytest config, fixed hash seed, timeouts |
| Coder gaming the exam | Exam frozen before round 1, never sent to the coder; feedback has test names and code stripped |
| Secrets | Keys and seeds live only in git-ignored `.env`; nothing secret in the repo or the replay cache |
| Writes to GitHub | Only the owner's repos; new repos need an explicit `yes`; nothing is ever merged |
| Provider outages | 180 s API timeout; SubQ down → evidence-only verdict, labelled; Meta down → clear message |
| Oversized inputs | Repos > 50 MB, PRs > 60 files and non-Python repos are refused with a reason |

## 11. Benchmarks (all run Nov 7, 2026; raw results in `benchmark/results/`)

| Benchmark | Result |
|---|---|
| 18 PRs (12 bad, 6 honest) | Deterministic only 7/12 · + Meta exam 10/12 · **+ SubQ gate 11/12 caught, 0/6 honest blocked** · plain AI reviewer 12/12 but **2/6 honest blocked** |
| MBPP, 500 tasks | Meta alone 459 correct, 40 wrong shipped · Meta + Doppel + SubQ 451 correct, **36 wrong shipped**, 11 held back; shipped code correct 92.7% vs 92.0% |
| Repo issues (6 × 3), long horizon (6 dependent issues × 2), long vertical (receipts feature × 3) | Meta alone and Meta + SubQ both perfect; 0 regressions |
| Cross-domain decisions (60 × 3 configurations) | All 60/60; SubQ raised 8 false alarms (loan arithmetic), none changed a final answer |
| Selector, 200 held-out MBPP tasks | running |
| Self-improving loop, code + decisions | running (code validation 94% at start) |
| SubQ cost | ~$0.0001–0.0008 per judge call; first 88 calls ≈ $0.007 in total |

**What the numbers say:** Meta Muse is a strong coder and analyst on its own. SubQ earns its place as a gate
on evidence and as a picker between Meta's attempts; it is not used as an arithmetic checker, because there
it raises false alarms.

## 12. Limitations

- Python projects tested with pytest only; small to mid-size repos (the coder reads the repo in its prompt).
- The agent runs on the owner's laptop: closed lid = offline (free cloud option: a Hugging Face Docker Space).
- ASI:One's own UI extras (chat titles, suggestion chips) use ASI:One's model, not Meta.
- The SubQ veto can hold back correct work (~2% on MBPP); selection mode is designed to avoid that.
- No real calendar yet: the schedule comes from chat (read-only iCal or Google API integration is designed, not built).
- MBPP and our own tasks may be partly familiar to the models; both configurations face that equally.

## 13. Roadmap

1. Selection mode by default in the live fix loop, once the held-out numbers confirm it.
2. Real calendar (private iCal URL, then Google Calendar API, read-only) and Gmail digests.
3. Always-on hosting (Hugging Face Space / small VM) with a persistent volume.
4. More languages (TypeScript/Jest, Go) and repo retrieval instead of whole-repo prompts.
5. Scope-creep rule (a PR that changes more than its issue asks) and a per-repo learned playbook.
6. Payments protocol for paid verification of other people's repos.

## 14. Credits

Built during the hackathon (git history). Not ours: pytest, uAgents, OpenAI and Anthropic Python SDKs, the
Meta Model API, SubQ API, Fetch.ai Agentverse and ASI:One, and MBPP (Google Research, CC-BY 4.0).
