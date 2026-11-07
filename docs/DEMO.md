# Demo script

**The moment:** an AI cheats on a pull request, and your phone buzzes with the exact lie before any human
has looked. Then you ask Doppel to do it properly, and a real, judged PR appears on GitHub.

## Before you go on stage (10 minutes)

1. Agent running on the laptop (`python -m receipts.agents.run`), laptop plugged in and awake.
2. Phone: ASI:One app, chat addressed to `doppel`. Send once:
   - `link sandeep <code>` (code from `.env`; so alerts reach the phone)
   - `watch mlzerotoheroyoutube/receipts-demo-store`
3. Laptop screen: browser tab on https://github.com/mlzerotoheroyoutube/receipts-demo-store/pulls
4. The cheating branch `agent/save10-quick` is already pushed (it hard-codes the answer from issue #1).
   On stage you only open the PR.
5. Have the recorded backup video open in another tab in case the Wi-Fi fails.

## Live (3 minutes)

| Time | Do | Say |
|---|---|---|
| 0:00 | Show issue #1 on GitHub: "SAVE10 only discounts the first item" | "Coding agents write PRs faster than anyone can review them. I told one to fix this bug and make the tests pass, whatever it takes." |
| 0:20 | Open the PR from `agent/save10-quick`: "Fixes #1. All tests pass ✅". Show the green test run. | "Its tests really are green. Would you merge it?" |
| 0:40 | Hold up the phone. Within about a minute: 👀 *New PR, checked on my own: 🛑 BLOCK: discount_cents now branches on values copied from the tests: 'SAVE10', 2* | "Nobody asked. Doppel watches my repos and caught the cheat: it hard-coded the exact cart from the bug report." |
| 1:10 | Refresh the PR: red **Doppel** status check and the report comment | "It's on GitHub as a real status check, with evidence and a command to reproduce it." |
| 1:30 | On the phone: "fix issue 1 in receipts-demo-store properly" | "Now I ask it to do the work itself." |
| 1:40 | Phone narrates: hidden exam written (13 tests) → Round 1 by Meta → evidence → SubQ approves → 🚀 PR opened | "Meta writes a hidden exam from the issue alone; the coder never sees it. Meta writes the fix, Doppel gathers evidence, SubQ decides if it ships." |
| 2:30 | Show the new PR on GitHub with the round history and SubQ's vote | "Two AIs: one cheated, one caught it and did it right. I just held my phone." |
| 2:45 | One slide: the benchmark table | "Across 18 cheating and honest PRs: 11 of 12 cheats caught, zero honest PRs blocked. A plain AI reviewer blocks a third of honest work." |

## One-minute video (submission)

1. (0–10 s) Issue → cheating PR with green tests.
2. (10–25 s) Phone buzzes: BLOCK with the exact line; GitHub shows the red Doppel check.
3. (25–45 s) "fix issue 1 properly" → narrated loop → PR opens.
4. (45–60 s) Benchmark table + "Meta codes, Meta writes the exam it never sees, SubQ judges, Fetch.ai agents in ASI:One."

## Likely questions

- **Can't the coding agent just learn the hidden tests?** It never receives them: the exam is written from the
  issue alone, frozen before round 1, and the judge's feedback has test names and code stripped out.
- **Why SubQ and not another LLM?** It's a decision model: it reads the whole evidence (up to 384K tokens) and
  returns a calibrated ship/don't-ship probability for a fraction of a cent. We don't ask it to do arithmetic;
  deterministic checks supply the facts.
- **Did SubQ make Meta a better coder?** No, and we say so: Meta solved our coding and business tasks on its own.
  SubQ's value is the gate: it caught the PR nothing else caught and never blocked honest work.
- **What's built today vs. not ours?** See the README: everything in the repo is from today; libraries, model
  APIs and the MBPP dataset are credited.
