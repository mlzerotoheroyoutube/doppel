# Receipts

**Your AI coding agent says "All tests pass ✅". Receipts checks whether that's true.**

Receipts is an independent verifier for AI-written pull requests, built for the
*Trust but Verify* track of the AIE Code Summit Hackathon (Nov 7, 2026). It
blocks a PR unless it can show evidence (receipts) that the change really fixes
the issue and breaks nothing.

## The teammate (Fetch.ai × Meta × SubQ)

Talk to it in ASI:One. It's built from Fetch.ai uAgents, and each model does what it's best at:

| Layer | What | Who |
|---|---|---|
| Personal layer | Remembers your targets, schedule, repos, strictness and feedback; plans your day; watches your repos; decides when to interrupt you | **Fetch.ai** agents (gate, planner, watcher, coder, six verifier workers) |
| Models | Coding loop (`muse-wonder-1.0`, falling back to `muse-spark-1.3`), hidden exam and day planning (`muse-spark-1.3`) | **Meta** Model API |
| Judge | Every agent output: each round of the coding loop, blind-test failures, the daily plan | **SubQ** |
| Ground truth | Suite, tamper rules, planted bugs, before/after differential | Deterministic, no model |

```
today: ship the coupon fixes, demo at 5pm, meetings 1-3pm   → Meta plans, SubQ reviews the plan
watch owner/repo                                             → every new PR is verified on its own
fix demo issue 1                                             → Meta codes → Receipts evidence → SubQ judges → rework (≤3)
```

The coding model never sees the hidden exam. It only gets the judge's feedback, with any test code removed.

```bash
python -m receipts.agents.run          # gate + 8 agents; connect the gate to Agentverse with a mailbox
python -m receipts.agents.try_local "today: ..." "fix demo issue 1"   # same chat, locally
```

Keys: `MODEL_API_KEY` (Meta), `SUBQ_API_KEY` (SubQ). `GITHUB_TOKEN` is optional; the signed-in `gh` CLI is used otherwise.

## How the verifier works

| Check | What it catches | Needs a model? |
|---|---|---|
| **Blind tests** | A model writes its own tests from the issue and the public interface only. It never sees the diff, the PR's tests or any function body, so it can't share the implementer's blind spots. Each test runs before and after the PR: *passed before, fails after* = regression; *fails before and after* = issue not fixed. A separate judge call (also code-blind) throws out tests that misread the spec. | yes |
| **Tamper detection** | Deleted tests, skipped tests, weakened assertions, changed expected values, edited `conftest.py` / pytest config / CI, code that detects it is running under pytest, code that hard-codes values copied from the tests, "fixes" that change no code. | no |
| **Planted bugs (mutation)** | Plants small bugs in the functions the PR changed. If the PR's tests stay green, their passing proves little. Also scores Receipts' own blind tests the same way. | no |
| **Sandbox** | Runs in a copy of the project with a fixed hash seed and a timeout. Blind tests run with `--noconftest` and an empty config, so the PR cannot hook them. | no |

Verdict: **BLOCK** if any receipt blocks, **WARN** for things a human should look at, otherwise **PASS**.

Every model call is cached by a hash of its inputs in `.receipts_cache/`, so re-running a verdict replays it exactly.

## Run it

```bash
uv venv && uv pip install pytest anthropic
python -m receipts verify --base demo_store --head path/to/pr/demo_store \
    --issue demo_store/ISSUES.md --issue-number 1
python -m benchmark.run              # full benchmark
python -m benchmark.run --no-model   # static + mutation checks only
python -m benchmark.run --baseline   # compare with a plain "LLM reads the diff" reviewer
```

The model backend is the Anthropic SDK (any credential it can resolve), falling
back to the `claude -p` CLI. The default model is `claude-opus-5-5`.

## Built at the hackathon

Everything in this repository was written during the event on Nov 7, 2026. The git history shows it.

- `receipts/blind.py`: code-blind test writer, before/after differential run, code-blind judge
- `receipts/tamper.py`: static tamper detection (rules T001 to T008)
- `receipts/mutation.py`: AST mutation engine scoped to changed functions
- `receipts/sandbox.py`: isolated pytest runner
- `receipts/verify.py`, `receipts/__main__.py`: verdict, report card, CLI
- `receipts/llm.py`: role-based model routing (Meta, SubQ, Anthropic) with a replay cache
- `receipts/loop.py`: the work loop: implement, collect evidence, judge, rework
- `receipts/planner.py`: Meta plans the day; SubQ reviews the plan
- `receipts/agents/`: Fetch.ai uAgents: gate (chat protocol + memory + watcher), planner, coder, six verifier workers
- `demo_store/`: the sample shop the demo PRs target, with three open issues
- `benchmark/`: 18 PRs (12 bad, 6 honest) mimicking common AI-agent failures, and the runner

## Not ours

[pytest](https://pytest.org) (MIT), [uAgents](https://github.com/fetchai/uAgents) (Apache-2.0),
the [OpenAI Python SDK](https://github.com/openai/openai-python) (Apache-2.0, used as the client for the Meta and SubQ APIs),
the [Anthropic Python SDK](https://github.com/anthropics/anthropic-sdk-python) (MIT), and the Meta, SubQ and Anthropic model APIs.
Claude Code was used as a coding assistant.

## License

MIT
