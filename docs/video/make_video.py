"""Build the Doppel demo video (about 2:50): real app screens + architecture slides + voice-over.

    python docs/video/make_video.py
Needs: macOS `say`, ffmpeg, Google Chrome. App screens are snapshots in docs/video/frames/*.html.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
FR = HERE / "frames"
BUILD = HERE / "build"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
VOICE, RATE = "Samantha", 158

HIDE = {  # which demo cards to hide so the important one sits at the top of the phone
    "demo-1": "#s2,#s3,#s4{display:none}",
    "demo-2": "#s0,#s1{display:none}",
    "demo-3": "#s0,#s1,#s2{display:none}",
}

SCENES = [
    ("title", None, "We're demoing the product, Doppel."),
    ("app", ("chat", "An AI agent framework",
             "Meta Muse models are the brain. Fetch.ai is the interface. Use it anywhere."),
     "Doppel is an A I agent framework. Meta's Muse models are its brain. Fetch dot A I is the user interface, "
     "so you can use it anywhere: in A S I One on the web or on your phone, or in this app. "
     "It works quietly behind the scenes, and it remembers who you are."),
    ("app", ("connections", "Connected to your work",
             "GitHub, your calendar and your email. Read-only, and it stays on your machine."),
     "It connects to the places your work lives. Your GitHub, your calendar, and your email."),
    ("loop", None,
     "For the agent loop, we use SubQ as the judge, at every step. Meta writes the work. Deterministic checks gather the facts. "
     "SubQ decides whether it ships. If it rejects, the agent reworks, guided by SubQ's diagnosis, never by the hidden tests."),
    ("app", ("demo-1", "An AI agent opens a pull request",
             "“Fixes #1. All tests pass.” And the tests really are green."),
     "Here it is live. A coding agent opens a pull request. It says: fixes issue one, all tests pass. And the tests really are green."),
    ("app", ("demo-2", "SubQ blocks it",
             "A hidden exam written from the issue alone. Planted bugs test the tests. SubQ judges the evidence: don't ship."),
     "Doppel checks it. Meta writes a hidden exam from the issue alone, without ever seeing the code. "
     "Then SubQ reads all of the evidence, and votes: do not ship. The agent had simply hard coded the answer."),
    ("app", ("demo-3", "Then it does the work itself",
             "Meta writes the fix. SubQ judges every round and approves. A real pull request opens."),
     "Then Doppel does the work itself. Meta writes the fix, SubQ judges the round and approves, "
     "and a real pull request opens on GitHub, with the evidence attached. Nothing merges without me."),
    ("stat-meta", None,
     "Our numbers are measured, not assumed. We tested on a public dataset of five hundred coding tasks. "
     "Without SubQ, Meta alone was right about ninety two percent of the time. "
     "But the other eight percent is shipped code that is wrong. And that can cost you far more than a dollar a day."),
    ("stat-subq", None,
     "So we tested SubQ as the gate. On pull requests written to cheat or break things, "
     "SubQ caught eleven out of twelve. That's ninety two percent. And it blocked zero honest changes. "
     "A plain A I reviewer, by comparison, blocked a third of the honest work."),
    ("learn", None,
     "So we turned it into a feedback loop. Every failure that the evidence confirms becomes a lesson for the Meta models. "
     "And a lesson is only kept if it proves itself, on work the agent has never seen. Otherwise, it's rolled back. "
     "So the system can improve over time, without quietly getting worse."),
    ("results", None,
     "Here are the benchmarks. Eleven of twelve bad pull requests caught, with zero honest ones blocked. "
     "Fewer wrong answers shipped across five hundred tasks. And long, multi step builds, all correct, with zero regressions. "
     "What we're building toward is simple: A I systems you can actually trust."),
    ("title-end", None,
     "Doppel. Meta does the work. SubQ checks it, at every step. And it only asks you when it must. "
     "There's a lot more we can show you: the self improving playbook, role specific reviews, and the full benchmarks. Thank you."),
]

CSS = """
*{box-sizing:border-box} body{margin:0;width:1920px;height:1080px;overflow:hidden;background:#000;color:#f5f5f7;
font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display","Helvetica Neue",sans-serif;-webkit-font-smoothing:antialiased}
.glow{position:absolute;inset:0;background:radial-gradient(1200px 700px at 75% 20%,rgba(94,92,230,.35),transparent 60%),
radial-gradient(900px 600px at 10% 90%,rgba(10,132,255,.25),transparent 60%)}
.mark{width:120px;height:120px;border-radius:30px;background:linear-gradient(145deg,#5e5ce6,#0a84ff 55%,#30d158 120%);
display:grid;place-items:center;font-weight:700;font-size:62px;color:#fff;box-shadow:0 20px 60px rgba(10,132,255,.45)}
h1{font-size:96px;letter-spacing:-.04em;margin:28px 0 10px;font-weight:700} h2{font-size:64px;letter-spacing:-.035em;margin:0 0 22px;font-weight:700}
.sub{font-size:34px;color:#a1a1a6;letter-spacing:-.01em;line-height:1.35}
.eyebrow{font-size:24px;color:#0a84ff;font-weight:600;letter-spacing:.02em;text-transform:uppercase;margin-bottom:16px}
.center{position:absolute;inset:0;display:grid;place-items:center;text-align:center}
.split{position:absolute;inset:0;display:grid;grid-template-columns:720px 1fr;align-items:center;gap:40px;padding:0 120px 0 150px}
.phone{width:470px;height:960px;border-radius:72px;background:#1c1c1e;padding:18px;box-shadow:0 40px 120px rgba(0,0,0,.6),inset 0 0 0 2px #3a3a3c}
.phone img{width:434px;height:924px;border-radius:56px;display:block;object-fit:cover;object-position:top}
.box{background:rgba(28,28,30,.85);border:1px solid #2c2c2e;border-radius:24px;padding:22px 26px;font-size:26px;line-height:1.3}
.box b{display:block;font-size:30px;color:#fff;margin-bottom:4px} .box span{color:#a1a1a6}
.grid{display:grid;gap:18px} .row{display:flex;gap:18px;align-items:stretch}
.pill{display:inline-block;padding:8px 18px;border-radius:999px;font-size:24px;font-weight:600;margin:4px}
.arrow{font-size:40px;color:#636366;align-self:center}
.big{font-size:120px;font-weight:700;letter-spacing:-.05em;line-height:1}
"""


def slide(kind: str, payload) -> str:
    if kind in ("title", "title-end"):
        tag = "Your double for engineering work." if kind == "title" else "Does the work. Checks it twice. Asks only when it must."
        return f"""<div class="glow"></div><div class="center"><div><div class="mark" style="margin:0 auto">D</div>
<h1>Doppel</h1><div class="sub">{tag}</div><div class="sub" style="margin-top:40px;font-size:26px">
Meta Muse · the brain &nbsp;·&nbsp; SubQ · the judge &nbsp;·&nbsp; Fetch.ai · everywhere</div></div></div>"""
    if kind == "app":
        name, title, sub = payload
        return f"""<div class="glow"></div><div class="split"><div class="phone"><img src="{BUILD / (name + '.png')}"></div>
<div><div class="eyebrow">Live product</div><h2>{title}</h2><div class="sub">{sub}</div></div></div>"""
    if kind == "arch":
        agents = ["Test runner", "Tamper detector", "Hidden-exam writer · Meta", "Before / after runner",
                  "Bug planter", "Planner · Meta", "Coder · Meta Muse Wonder"]
        return f"""<div class="glow"></div><div style="position:absolute;inset:0;padding:90px 120px">
<div class="eyebrow">Architecture</div><h2>One gate, many specialists</h2>
<div class="row" style="margin-top:30px">
 <div class="grid" style="width:430px">
  <div class="box"><b>You, anywhere</b><span>ASI:One web & phone · web app · HTTP API</span></div>
  <div class="box"><b>Fetch.ai Agentverse</b><span>Mailbox · Agent Chat Protocol</span></div></div>
 <div class="arrow">→</div>
 <div class="box" style="width:380px;display:flex;flex-direction:column;justify-content:center;border-color:#0a84ff">
  <b style="font-size:40px">Doppel gate</b><span>Memory · access control · repo watcher · plain-English router (Meta)</span></div>
 <div class="arrow">→</div>
 <div class="grid" style="flex:1;grid-template-columns:1fr 1fr">{''.join(f'<div class="box" style="padding:16px 20px"><b style="font-size:24px;margin:0">{a}</b></div>' for a in agents)}
  <div class="box" style="padding:16px 20px;border-color:#30d158"><b style="font-size:24px;margin:0;color:#30d158">Judge · SubQ</b></div></div>
</div>
<div class="row" style="margin-top:34px;justify-content:center">
 <span class="pill" style="background:#1d3b5f;color:#64d2ff">Meta Muse writes</span>
 <span class="pill" style="background:#173d24;color:#30d158">SubQ decides</span>
 <span class="pill" style="background:#3a2a10;color:#ff9f0a">Deterministic checks supply the facts</span>
 <span class="pill" style="background:#2c2c2e;color:#f5f5f7">GitHub · Calendar · Gmail</span></div></div>"""
    if kind == "loop":
        steps = [("Meta writes", "the fix", "#0a84ff"), ("Evidence", "tests · tamper · exam · planted bugs", "#ff9f0a"),
                 ("SubQ judges", "every step", "#30d158"), ("Ship or rework", "feedback, never the exam", "#bf5af2")]
        cells = '<div class="arrow">→</div>'.join(
            f'<div class="box" style="flex:1;text-align:center;border-color:{c}"><b style="color:{c};font-size:38px">{a}</b><span>{b}</span></div>'
            for a, b, c in steps)
        return f"""<div class="glow"></div><div style="position:absolute;inset:0;padding:120px">
<div class="eyebrow">The agent loop</div><h2>SubQ judges at every step</h2>
<div class="row" style="margin-top:60px">{cells}</div>
<div class="box" style="margin-top:50px;font-size:30px"><b>It gets better, safely</b>
<span>Every confirmed failure becomes a lesson for Meta. A lesson is adopted only if it beats the current playbook on held-out work. Otherwise it is rolled back.</span></div></div>"""
    if kind == "stat-meta":
        return """<div class="glow"></div><div style="position:absolute;inset:0;padding:130px 140px">
<div class="eyebrow">Measured, not assumed · 500 tasks (MBPP)</div><h2>Meta alone is right most of the time.</h2>
<div class="row" style="margin-top:60px">
 <div class="box" style="flex:1"><div class="big" style="color:#30d158">92%</div><span style="font-size:32px">right without SubQ</span></div>
 <div class="box" style="flex:1;border-color:#ff453a"><div class="big" style="color:#ff453a">8%</div><span style="font-size:32px">wrong code that would ship</span></div></div>
<div class="sub" style="margin-top:46px">That 8% can cost far more than a dollar a day.</div></div>"""
    if kind == "stat-subq":
        return """<div class="glow"></div><div style="position:absolute;inset:0;padding:130px 140px">
<div class="eyebrow">SubQ as the gate · 18 pull requests</div><h2>SubQ caught the problem.</h2>
<div class="row" style="margin-top:60px">
 <div class="box" style="flex:1;border-color:#30d158"><div class="big" style="color:#30d158">92%</div><span style="font-size:32px">of bad PRs caught (11 of 12)</span></div>
 <div class="box" style="flex:1"><div class="big">0%</div><span style="font-size:32px">of honest PRs blocked (0 of 6)</span></div>
 <div class="box" style="flex:1"><div class="big" style="color:#ff453a">33%</div><span style="font-size:32px">honest PRs blocked by a plain AI reviewer</span></div></div></div>"""
    if kind == "learn":
        steps = [("Confirmed failure", "the evidence proves it", "#ff453a"), ("Lesson for Meta", "a general rule", "#0a84ff"),
                 ("Held-out test", "work it never saw", "#ff9f0a"), ("Keep or roll back", "only if it's better", "#30d158")]
        cells = '<div class="arrow">→</div>'.join(
            f'<div class="box" style="flex:1;text-align:center;border-color:{c}"><b style="color:{c};font-size:34px">{a}</b><span>{b}</span></div>'
            for a, b, c in steps)
        return f"""<div class="glow"></div><div style="position:absolute;inset:0;padding:130px 120px">
<div class="eyebrow">Feedback loop</div><h2>It improves over time, safely</h2>
<div class="row" style="margin-top:70px">{cells}</div>
<div class="sub" style="margin-top:56px">A change is adopted only when it beats the current playbook on held-out work.</div></div>"""
    if kind == "results":
        return """<div class="glow"></div><div style="position:absolute;inset:0;padding:110px 120px">
<div class="eyebrow">Results · measured today</div><h2>Catches cheats. Doesn't block honest work.</h2>
<div class="row" style="margin-top:50px">
 <div class="box" style="flex:1"><div class="big" style="color:#30d158">11/12</div><span style="font-size:30px">bad PRs caught with SubQ as the gate</span></div>
 <div class="box" style="flex:1"><div class="big">0/6</div><span style="font-size:30px">honest PRs blocked</span></div>
 <div class="box" style="flex:1"><div class="big" style="color:#ff453a">2/6</div><span style="font-size:30px">honest PRs blocked by a plain AI reviewer</span></div></div>
<div class="row" style="margin-top:22px">
 <div class="box" style="flex:1"><b>500 coding tasks</b><span>Wrong answers shipped: 40 → 36 with SubQ in the loop</span></div>
 <div class="box" style="flex:1"><b>Long-horizon & vertical builds</b><span>All correct, zero regressions</span></div></div></div>"""
    raise ValueError(kind)


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, capture_output=True)


def shoot(html: Path, png: Path, w: int, h: int, scale: int = 1) -> None:
    run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--allow-file-access-from-files",
         f"--force-device-scale-factor={scale}", f"--window-size={w},{h}", f"--screenshot={png}", html.as_uri()])


def main() -> None:
    BUILD.mkdir(exist_ok=True)
    # 1. phone screens from the app snapshots
    for name in ("chat", "memory", "connections", "demo-1", "demo-2", "demo-3"):
        src = FR / (name.split("-")[0] + ("" if "-" not in name else "") + ".html")
        src = FR / ("demo.html" if name.startswith("demo") else f"{name}.html")
        html = src.read_text()
        extra = "<style>.view.on{animation:none!important}.composer{display:none!important}" + HIDE.get(name, "") + "</style>"
        page = BUILD / f"{name}.html"
        page.write_text(html.replace("</head>", extra + "</head>", 1))
        shoot(page, BUILD / f"{name}.png", 434, 924, 2)
    # 2. slides + voice + segments
    segs = []
    for i, (kind, payload, text) in enumerate(SCENES):
        sl = BUILD / f"s{i:02d}.html"
        sl.write_text(f"<!doctype html><meta charset=utf-8><style>{CSS}</style>{slide(kind, payload)}")
        shoot(sl, BUILD / f"s{i:02d}.png", 1920, 1080)
        run(["say", "-v", VOICE, "-r", str(RATE), "-o", str(BUILD / f"s{i:02d}.aiff"), text])
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                    str(BUILD / f"s{i:02d}.aiff")], capture_output=True, text=True).stdout) + 0.9
        seg = BUILD / f"s{i:02d}.mp4"
        run(["ffmpeg", "-y", "-loop", "1", "-i", str(BUILD / f"s{i:02d}.png"), "-i", str(BUILD / f"s{i:02d}.aiff"),
             "-filter_complex", f"[0:v]scale=1920:1080,zoompan=z='min(zoom+0.00025,1.04)':d={int(dur * 30)}:s=1920x1080:fps=30,"
             f"fade=t=in:st=0:d=0.4,fade=t=out:st={dur - 0.4:.2f}:d=0.4[v];[1:a]apad[a]",
             "-map", "[v]", "-map", "[a]", "-t", f"{dur:.2f}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
             "-c:a", "aac", "-b:a", "160k", str(seg)])
        segs.append(seg)
        print(f"scene {i:02d} {kind:10} {dur:5.1f}s", flush=True)
    (BUILD / "list.txt").write_text("".join(f"file '{s}'\n" for s in segs))
    out = HERE / "doppel-demo.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(BUILD / "list.txt"), "-c", "copy", str(out)])
    total = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
                           capture_output=True, text=True).stdout.strip()
    print(f"video: {out}  ({float(total):.1f}s)")


if __name__ == "__main__":
    main()
