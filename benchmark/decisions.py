"""Cross-domain business decisions: where does Meta fail, and does a SubQ judge help?

Six templates mix two or three domains (finance, trade, tax, operations, behavioral traps).
Each instance has one exactly computable right answer, so grading is objective:
the choice must match and the number must be within 0.5% (or $1).

Configurations:
    meta       Meta Muse Spark answers once.
    selfcheck  Meta answers, then reviews its own answer once (a second look, same model).
    subq       Meta answers; SubQ Snap votes on whether it is right and names the error type;
               on a "wrong" vote Meta revises with only that error type (never the answer). Max 2 revisions.

    python -m benchmark.decisions --config subq
    python -m benchmark.decisions --report
"""

from __future__ import annotations

import argparse
import json
import os
import random
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "benchmark" / "results" / "decisions"


def money(x: float) -> str:
    return f"${x:,.2f}"


# ---------------------------------------------------------------- templates (scenario, choice, value)

def supplier(r: random.Random):
    qty = r.choice([800, 1200, 2000, 2500])
    deadline = r.choice([20, 25, 30])
    sups = []
    for name, cur, fx in (("Alpha (Germany)", "EUR", 1.08), ("Bravo (Japan)", "JPY", 0.0067), ("Cobalt (USA)", "USD", 1.0)):
        price_local = {"EUR": r.uniform(9, 14), "JPY": r.uniform(1300, 2100), "USD": r.uniform(11, 16)}[cur]
        sups.append(dict(name=name, cur=cur, fx=fx, price=round(price_local, 2),
                         ship=r.choice([600, 900, 1500, 2400]), duty=0 if cur == "USD" else r.choice([0.04, 0.08, 0.12]),
                         cap=r.choice([qty - 200, qty, qty + 500, qty * 2]), lead=r.choice([10, 18, 24, 35])))
    lines = []
    best = None
    for s in sups:
        lines.append(f"- {s['name']}: {s['price']:,} {s['cur']} per unit, can supply at most {s['cap']} units, "
                     f"delivers in {s['lead']} days, shipping {money(s['ship'])} flat, import duty "
                     f"{s['duty']:.0%} of (goods value + shipping) in USD")
        if s["cap"] >= qty and s["lead"] <= deadline:
            goods = s["price"] * s["fx"] * qty
            cost = (goods + s["ship"]) * (1 + s["duty"])
            if best is None or cost < best[1]:
                best = (s["name"].split()[0], cost)
    text = (f"We must buy {qty} units, delivered within {deadline} days, from ONE supplier. Exchange rates: "
            f"1 EUR = 1.08 USD, 1 JPY = 0.0067 USD.\n" + "\n".join(lines) +
            "\nWhich supplier gives the lowest total landed cost in USD that meets quantity and deadline? "
            "If none qualifies, choice is \"none\" and value 0.")
    return text, (best[0] if best else "none"), (round(best[1], 2) if best else 0.0), "choice = supplier's first word; value = total landed cost in USD"


def loan(r: random.Random):
    amount = r.choice([20000, 35000, 50000, 80000])
    opts, best = [], None
    for name in ("A", "B", "C"):
        apr = r.choice([0.049, 0.059, 0.069, 0.079, 0.089])
        years = r.choice([3, 4, 5, 6])
        fee = r.choice([0, 0.01, 0.02, 0.03])
        i = apr / 12
        n = years * 12
        pay = amount * i / (1 - (1 + i) ** -n)
        total = pay * n + amount * fee - amount  # interest + fee
        opts.append(f"- Loan {name}: {apr:.1%} APR, {years}-year term, monthly payments, origination fee "
                    f"{fee:.0%} of the amount paid upfront in cash")
        if best is None or total < best[1]:
            best = (name, total)
    text = (f"A small business borrows {money(amount)}, fully amortized with equal monthly payments (APR / 12 "
            "is the monthly rate).\n" + "\n".join(opts) +
            "\nWhich loan has the lowest total cost of borrowing (all interest paid plus the fee)?")
    return text, best[0], round(best[1], 2), "choice = loan letter; value = total cost of borrowing in USD"


def pricing(r: random.Random):
    fixed = r.choice([5000, 8000, 12000])
    var = r.choice([6, 9, 12])
    cap = r.choice([900, 1300, 2000])
    rows, best = [], None
    for price in (19, 24, 29, 34, 39):
        demand = int(r.uniform(400, 2600) * (40 - price) / 20)
        sold = min(demand, cap)
        profit = sold * (price - var) - fixed
        rows.append(f"- ${price}: demand {demand} units")
        if best is None or profit > best[1]:
            best = (str(price), profit)
    text = (f"A product costs ${var} per unit to make, fixed costs are {money(fixed)} per month, and the factory can "
            f"make at most {cap} units per month (unmet demand is lost).\nMonthly demand by price:\n" + "\n".join(rows) +
            "\nWhich price maximizes monthly profit?")
    return text, best[0], float(best[1]), "choice = the price as a number without $; value = monthly profit in USD"


def project(r: random.Random):
    spent = r.choice([400_000, 900_000, 1_500_000])
    remaining = r.choice([200_000, 350_000, 500_000])
    p = r.choice([0.3, 0.5, 0.7])
    win = r.choice([500_000, 800_000, 1_200_000])
    lose = r.choice([0, 50_000, 100_000])
    salvage = r.choice([0, 60_000, 150_000])
    ev_continue = p * win + (1 - p) * lose - remaining
    ev_stop = salvage
    choice = "continue" if ev_continue > ev_stop else "stop"
    text = (f"We have already spent {money(spent)} on a project; that money is gone either way. Finishing needs "
            f"{money(remaining)} more. If finished, it succeeds with probability {p:.0%} and then returns "
            f"{money(win)}; otherwise it returns {money(lose)}. If we stop now we can sell the work for "
            f"{money(salvage)}. The board wants the decision that maximizes expected value from today.")
    return text, choice, round(max(ev_continue, ev_stop), 2), "choice = continue or stop; value = expected value from today of the better option in USD"


def offer(r: random.Random):
    tax = r.choice([0.25, 0.3, 0.35])
    best, lines = None, []
    for name in ("North", "South"):
        salary = r.choice([140_000, 155_000, 170_000, 185_000])
        bonus = r.choice([0.0, 0.1, 0.15])
        match = r.choice([0.03, 0.05, 0.06])
        match_cap = r.choice([6_000, 9_000, 20_000])
        equity = r.choice([0, 80_000, 160_000])
        cliff = r.choice([False, True])
        months_until_cliff = 12 if cliff else 0
        # first-year value: salary + bonus taxed; retirement match pre-tax and capped; equity vests 1/4 per year,
        # with a one-year cliff the first-year quarter still vests at month 12 (inside year one)
        first_year = (salary + salary * bonus) * (1 - tax) + min(salary * match, match_cap) + equity / 4
        lines.append(f"- {name}: salary {money(salary)}, bonus {bonus:.0%} of salary (taxed like salary), "
                     f"retirement match {match:.0%} of salary capped at {money(match_cap)} per year (not taxed now), "
                     f"equity grant {money(equity)} vesting evenly over 4 years"
                     + (" with a 1-year cliff (the first quarter vests on the 12-month anniversary)" if cliff else " monthly"))
        if best is None or first_year > best[1]:
            best = (name, first_year)
    text = (f"Compare two job offers by FIRST-YEAR value: after-tax cash at a flat {tax:.0%} tax rate, plus the "
            "retirement match, plus equity that has vested by the end of month 12 (valued at grant value).\n" +
            "\n".join(lines) + "\nWhich offer has the higher first-year value?")
    return text, best[0], round(best[1], 2), "choice = North or South; value = its first-year value in USD"


def invoice(r: random.Random):
    eur = r.choice([12_000, 48_000, 95_000])
    mid = r.choice([1.07, 1.09, 1.11])
    bank_spread = r.choice([0.005, 0.01, 0.015])
    wire_eur = r.choice([15, 25, 40])
    fx_platform_spread = r.choice([0.002, 0.004])
    platform_fee = r.choice([0.0035, 0.006])
    discount_usd = r.choice([0.0, 0.01, 0.02])
    # Option 1: pay EUR via bank: buy EUR at mid*(1+spread), plus wire fee in EUR converted the same way
    opt1 = (eur + wire_eur) * mid * (1 + bank_spread)
    # Option 2: pay EUR via platform: mid*(1+platform spread) plus percentage fee on USD amount
    opt2 = eur * mid * (1 + fx_platform_spread) * (1 + platform_fee)
    # Option 3: supplier accepts USD at their rate mid*1.02, minus early-payment discount
    opt3 = eur * mid * 1.02 * (1 - discount_usd)
    best = min((("bank", opt1), ("platform", opt2), ("usd", opt3)), key=lambda t: t[1])
    text = (f"We owe a supplier {eur:,} EUR. The mid-market rate is 1 EUR = {mid} USD. Options:\n"
            f"- bank: our bank sells EUR at mid-market + {bank_spread:.1%}; it also charges a {wire_eur} EUR wire fee "
            "(bought at the same rate)\n"
            f"- platform: a payments platform sells EUR at mid-market + {fx_platform_spread:.1%}, then adds a fee of "
            f"{platform_fee:.2%} of the USD amount\n"
            f"- usd: the supplier accepts USD at their own rate of mid-market + 2%, and gives an early-payment "
            f"discount of {discount_usd:.0%} off that USD amount\nWhich option costs the fewest USD?")
    return text, best[0], round(best[1], 2), "choice = bank, platform or usd; value = total USD paid"


TEMPLATES = {"supplier": supplier, "loan": loan, "pricing": pricing, "project": project, "offer": offer,
             "invoice": invoice}


def tasks(per: int = 10) -> list[dict]:
    out = []
    for name, fn in TEMPLATES.items():
        for i in range(per):
            text, choice, value, fmt = fn(random.Random(f"{name}-{i}"))
            out.append({"id": f"{name}-{i}", "domain": name, "text": text, "choice": choice, "value": value,
                        "format": fmt})
    return out


# ---------------------------------------------------------------- models

ANALYST = """You are a careful business analyst. Work it out step by step, then give the final answer. \
End your reply with one JSON object on its own line: {"choice": "...", "value": <number>}"""

SELF_CHECK = """Here is your earlier answer. Re-check every number, unit, constraint and formula. If anything \
is wrong, fix it. End your reply with one JSON object on its own line: {"choice": "...", "value": <number>}"""

ERRORS = {"none": "No error; the decision and the number are right",
          "arithmetic": "An arithmetic slip somewhere in the calculation",
          "ignored_constraint": "A stated constraint (capacity, deadline, cap, cliff) was ignored",
          "sunk_cost": "Money already spent was treated as if it still mattered",
          "units_or_currency": "Units, percentages or currencies were mixed up or converted wrongly",
          "wrong_formula": "The wrong formula or method was used",
          "misread": "The scenario was misread (wrong option, wrong number taken)"}


def parse(reply: str) -> dict:
    for line in reversed(reply.strip().splitlines()):
        line = line.strip().strip("`")
        if line.startswith("{") and line.endswith("}"):
            try:
                d = json.loads(line)
                return {"choice": str(d.get("choice", "")).strip(), "value": float(d.get("value", 0) or 0)}
            except (ValueError, TypeError):
                continue
    try:
        d = json.loads(reply[reply.rindex("{"): reply.rindex("}") + 1])
        return {"choice": str(d.get("choice", "")).strip(), "value": float(d.get("value", 0) or 0)}
    except (ValueError, TypeError):
        return {"choice": "", "value": 0.0}


def correct(task: dict, ans: dict) -> bool:
    ok_choice = ans["choice"].lower().strip(" $").split()[0:1] == [task["choice"].lower()] if ans["choice"] else False
    tol = max(1.0, abs(task["value"]) * 0.005)
    return ok_choice and abs(ans["value"] - task["value"]) <= tol


def ask(task: dict, history: list[str]) -> tuple[str, dict]:
    from receipts import llm
    prompt = f"{task['text']}\n\nAnswer format: {task['format']}."
    if history:
        prompt += "\n\n" + "\n\n".join(history)
    reply = llm.complete(ANALYST, prompt, effort="medium", role="analyst")
    return reply, parse(reply)


def judge(task: dict, reply: str) -> tuple[bool, str, float]:
    from receipts import llm
    state = f"# Scenario\n{task['text']}\n\n# Proposed answer and working\n{reply[-6000:]}"
    a = llm.decide(state, {
        "right": {"type": "noul", "instructions": "Are both the chosen option and the final number correct, using "
                  "every number and constraint in the scenario?",
                  "criteria": {"true": "Correct choice and correct number", "false": "Wrong choice or wrong number"}},
        "error": {"type": "choice", "instructions": "What is the main error in the working, if any?",
                  "criteria": ERRORS}}, role="judge")
    p, err = float(a["right"]["noul"]), a["error"]["choice"]
    return (p >= 0.5 and err == "none"), err, p


def run_task(config: str, task: dict) -> dict:
    row = {"id": task["id"], "domain": task["domain"], "config": config}
    try:
        reply, ans = ask(task, [])
        row["first_correct"] = correct(task, ans)
        if config == "selfcheck":
            reply2, ans = ask(task, [f"Your earlier answer:\n{reply[-4000:]}", SELF_CHECK])
            row["revisions"] = 1
        elif config == "subq":
            row["votes"], revisions = [], 0
            while True:
                ok, err, p = judge(task, reply)
                row["votes"].append([err, round(p, 3)])
                if ok or revisions == 2:
                    break
                revisions += 1
                hint = (f"An independent reviewer believes your answer is wrong. Likely problem: {ERRORS[err]}. "
                        "Recompute carefully from the scenario.") if err != "none" else \
                    "An independent reviewer is not confident your answer is right. Recompute carefully."
                reply, ans = ask(task, [f"Your earlier answer:\n{reply[-4000:]}", hint])
            row["revisions"] = revisions
        row.update(answer=ans, expected={"choice": task["choice"], "value": task["value"]},
                   correct=correct(task, ans))
    except Exception as e:  # noqa: BLE001
        row.update(correct=False, error=str(e)[:200])
    return row


def report() -> None:
    rows = [json.loads(l) for f in sorted(OUT.glob("*.jsonl")) for l in f.read_text().splitlines()]
    names = {"meta": "Meta alone", "selfcheck": "Meta + self-check", "subq": "Meta + SubQ judge"}
    doms = list(TEMPLATES)
    print("| Configuration | Overall | " + " | ".join(doms) + " |")
    print("|---|---|" + "---|" * len(doms))
    for c in ("meta", "selfcheck", "subq"):
        rs = [r for r in rows if r["config"] == c and "error" not in r]
        if not rs:
            continue
        cells = []
        for d in doms:
            ds = [r for r in rs if r["domain"] == d]
            cells.append(f"{sum(r['correct'] for r in ds)}/{len(ds)}")
        print(f"| {names[c]} | **{sum(r['correct'] for r in rs)}/{len(rs)}** | " + " | ".join(cells) + " |")
    sub = [r for r in rows if r["config"] == "subq" and "error" not in r]
    if sub:
        flagged_wrong = sum(1 for r in sub if not r["first_correct"] and r["votes"][0][0] != "none" or
                            (not r["first_correct"] and r["votes"][0][1] < 0.5))
        wrong_first = sum(not r["first_correct"] for r in sub)
        false_alarm = sum(1 for r in sub if r["first_correct"] and (r["votes"][0][0] != "none" or r["votes"][0][1] < 0.5))
        fixed = sum(1 for r in sub if not r["first_correct"] and r["correct"])
        broke = sum(1 for r in sub if r["first_correct"] and not r["correct"])
        print(f"\nSubQ judge on Meta's first answers: {wrong_first} were wrong; SubQ flagged {flagged_wrong} of them. "
              f"False alarms on right answers: {false_alarm}. After revision: {fixed} fixed, {broke} broken.")
    errs = sum("error" in r for r in rows)
    if errs:
        print(f"\n({errs} rows hit infrastructure errors and are excluded.)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", choices=["meta", "selfcheck", "subq"])
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    if a.report:
        report()
        return
    os.environ["RECEIPTS_TRIAL"] = "decisions"
    OUT.mkdir(parents=True, exist_ok=True)
    ts = tasks()
    with ThreadPoolExecutor(a.workers) as pool:
        rows = list(pool.map(lambda t: run_task(a.config, t), ts))
    (OUT / f"{a.config}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    print(f"{a.config}: {sum(r.get('correct', False) for r in rows)}/{len(rows)} correct")


if __name__ == "__main__":
    main()
