# Doppel: hackathon submission

**Team:** Doppel (Sandeep Vijayarao)
**Repository:** https://github.com/mlzerotoheroyoutube/doppel
**Problem statement:** AI agent with a verifier ("Trust but Verify")
**Partner products:** Meta (Model API, Muse), Fetch.ai (Agentverse, ASI:One), SubQ
**Demo video:** [docs/video/doppel-demo.mp4](video/doppel-demo.mp4) (2:40)
**Fetch.ai agent:** `agent1q0qv0zfjg8vwzdx9dle5yxreyfwmg3mnrlfqkqeayl6hlq6vaztuk3wxcsg`

## Project description

Doppel is an AI agent framework you can trust: an agent that does real work, plus a verifier that checks every step before anything ships.

Meta's Muse models are the brain (muse-wonder writes code; muse-spark plans, routes, writes hidden exams and chats). Fetch.ai is the interface: Doppel is a uAgent on Agentverse that you can talk to from ASI:One on web or phone, or from our own web app. It connects to GitHub, your calendar and your email, and it remembers who you are (role, focus, standards) so it reviews PRs the way you would.

SubQ is the judge at every step of the agent loop. Meta writes the work, deterministic checks gather evidence (a pytest sandbox, tamper rules, mutation testing, a hidden exam written from the issue alone), and SubQ decides whether it ships. If SubQ rejects it, the agent reworks it using SubQ's diagnosis, never the hidden tests.

Measured, not assumed:
- Meta alone was right about 92% of the time on 500 MBPP coding tasks. The other 8% is wrong code that would ship, and that costs far more than SubQ's fraction of a cent per check.
- As a gate on 18 pull requests, SubQ caught 11 of 12 bad PRs (92%) and blocked 0 of 6 honest ones. A plain AI reviewer blocked 33% of the honest work.
- Confirmed failures feed a self-improving playbook. A lesson is kept only if it does better on held-out work; otherwise it's rolled back.

Owner-only actions, nothing merges without you, and every decision comes with receipts.

## Feedback for partner products

SubQ's systemone gate is a great evidence judge. Meta Muse is fast and capable. Agentverse needs Bureau support in the Inspector.

## Feedback for Cerebral Valley

Well-run event with clear tracks and great partner APIs. More API credits and earlier key access would help.
