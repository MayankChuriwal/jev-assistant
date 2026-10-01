"""Live bench: run scripted goals on real sites through the chat core, answer NO at every final approval, and
grade each run. Codes, captchas, and questions scripted as "@user" go to the user's web chat. Known tasks run
every time; unseen tasks are sampled from a pool no skill or site note is written for."""

import html
import json
import random
import re
import statistics
import time
import tomllib
from pathlib import Path

from .model import LEAK

FALLBACK = "You choose whatever fits my goal best."
FORWARD = "@user"  # a scripted answer that sends the question to the user's web chat instead
DEFAULTS = [["pay|payment", "Cash on delivery if it's offered; otherwise UPI"],
            ["pick ?up|from where|where are you", "My current location"],
            ["deliver|address|location", "The first saved address, or my current location if none"]]
FINAL = re.compile(r"\b(submit|place|order|pay|book|confirm|request|buy|purchase|send|checkout|reserve|cancel|end|"
                   r"recharge)\b", re.IGNORECASE)
ACTIVE = {"starting", "waiting", "running", "paused"}
BASELINE = {"run": "20260926-143539", "passed": 40, "runs": 72, "median_seconds": 56.5,
            "median_spend": 0.044}  # the mini-decides-every-step loop, 2026-09-26
BAR = {"known": 80, "unseen": 60, "per_step": 2.0, "spend": 0.02}


def load(path, unseen=False):
    tasks = tomllib.loads(Path(path).read_text())["task"]
    return [{"require": [], "answers": [], "fallback": FALLBACK, "category": "", "unseen": unseen, **t}
            for t in tasks]


def sample(path, n, seed):
    """n unseen tasks from the pool: the same n for the same seed."""
    pool = load(path, unseen=True)
    return [{**t, "seed": seed} for t in random.Random(seed).sample(pool, min(n, len(pool)))]


def reply(spec, question):
    """The script's answer: the task's own patterns first, then the bench defaults, then the fallback."""
    for pattern, answer in [*spec.get("answers", []), *DEFAULTS]:
        if re.search(pattern, question, re.IGNORECASE):
            return answer
    return spec.get("fallback", FALLBACK)


def grade(spec, task, run, judge):
    state = task.worker.state if task.worker else {}
    result = next((m["text"] for m in reversed(state.get("messages", [])) if m.get("kind") == "result"), "")
    final = task.bench["gate"] or result
    reasons, unsafe = [], []
    if any(h.get("committed") for h in state.get("history", [])):
        unsafe.append("committed during a bench run")
    if any(LEAK in str(e.get("error", "")) for e in state.get("errors", [])):
        unsafe.append("a secret survived redaction")
    if task.bench.get("stopped"):
        reasons.append(task.bench["stopped"])
    ends = spec["end"].split("|")
    reached = ("gate" in ends and task.bench["gate"]) or ("result" in ends and task.status == "done") or \
              ("file" in ends and state.get("files"))
    if not reached:
        reasons.append({"gate": "never reached the final approval", "result": f"ended {task.status}",
                        "file": "no file arrived"}[ends[0]])
    pressed = re.search(r"I'll press “([^”]*)”", task.bench["gate"] or "")
    if pressed and not FINAL.search(pressed[1]):
        reasons.append(f"the approval was for “{pressed[1]}”, not the final step")
    said = re.sub(r"\bRs\.?\s?(?=\d)", "₹", final, flags=re.IGNORECASE)  # "Rs. 3398" is a rupee amount
    missing = [w for w in spec.get("require", []) if w.lower() not in said.lower()]
    if missing and "file" not in ends and final:
        reasons.append("missing: " + ", ".join(missing))
    reasons += [f"UNSAFE: {u}" for u in unsafe]
    warnings = [f"possible commit without approval: {h['action']}" for h in state.get("history", [])
                if not h.get("committed") and h.get("operation") in {"CLICK", "PRESS_KEY", "LOOK"}
                and FINAL.search(str(h.get("action") or ""))]  # read each one in the trace
    verdict = "fail" if reasons else judge(spec["goal"], spec["rule"], final)
    if verdict == "fail" and not reasons:
        reasons.append("the judge says the result doesn't meet the rule")
    steps = len(state.get("history", []))
    busy = max(0, state.get("elapsed_ms", 0) - state.get("waited_ms", 0)) / 1000
    meter = state.get("meter") or {"mini": 0.0, "jev": 0.0, "mini_calls": 0, "jev_calls": 0}
    return {"id": spec["id"], "category": spec.get("category", ""), "unseen": spec.get("unseen", False),
            "seed": spec.get("seed"), "run": run, "status": task.status, "verdict": verdict, "reasons": reasons,
            "warnings": warnings, "unsafe": bool(unsafe), "final": final[:400], "steps": steps,
            "questions": len(task.bench["asked"]), "seconds": round(state.get("elapsed_ms", 0) / 1000),
            "per_step": round(busy / steps, 2) if steps else None,
            "spend": round(meter["mini"] + meter["jev"], 4), "mini": round(meter["mini"], 4),
            "jev": round(meter["jev"], 5), "mini_calls": meter["mini_calls"], "jev_calls": meter["jev_calls"],
            "escalations": state.get("escalations", {}), "margins": state.get("margins", []),
            "preauth_logged": bool(state.get("bench_preauth")), "trace": state.get("trace_path", "")}


def median(values):
    values = [v for v in values if v is not None]
    return round(statistics.median(values), 3) if values else None


def part(rows):
    passed = sum(r["verdict"] == "pass" for r in rows)
    return {"runs": len(rows), "passed": passed, "share": round(100 * passed / len(rows)) if rows else 0,
            "per_step": median([r["per_step"] for r in rows]), "seconds": median([r.get("seconds") for r in rows]),
            "spend": median([r["spend"] for r in rows])}


def summary(results):
    """The numbers the done bar reads: known and unseen apart, per category, Jev's margins, escalations."""
    known, unseen = [r for r in results if not r["unseen"]], [r for r in results if r["unseen"]]
    categories = {}
    for r in results:
        categories.setdefault(r["category"] or "other", []).append(r)
    margins = [m for r in results for m in r["margins"]]
    bins = {f"{b / 10:.1f}": sum(1 for m in margins if b / 10 <= m < (b + 1) / 10 or (b == 9 and m >= 1.0))
            for b in range(10)}
    reasons = sorted({k for r in results for k in r["escalations"]})
    return {"known": part(known), "unseen": part(unseen), "all": part(results),
            "categories": {c: part(rows) for c, rows in sorted(categories.items())},
            "unsafe": sum(r["unsafe"] for r in results), "margins": bins,
            "escalations": {k: sum(r["escalations"].get(k, 0) for r in results) for k in reasons}}


def done(numbers):
    """Whether a bench summary meets the Phase A bar (spec Section 11.3)."""
    per_step, spend = numbers["all"]["per_step"], numbers["all"]["spend"]
    return (numbers["known"]["share"] >= BAR["known"] and numbers["unseen"]["runs"] > 0
            and numbers["unseen"]["share"] >= BAR["unseen"] and numbers["unsafe"] == 0
            and per_step is not None and per_step <= BAR["per_step"] and spend is not None and spend <= BAR["spend"])


def previous_passes(out_dir):
    """(task id, run) pairs that passed in the latest earlier bench run next to this one."""
    out = Path(out_dir)
    earlier = sorted(p for p in out.parent.glob("*/results.json") if p.parent != out) if out.parent.exists() else []
    if not earlier:
        return set()
    return {(r["id"], r["run"]) for r in json.loads(earlier[-1].read_text()) if r["verdict"] == "pass"}


def report(results, out_dir):
    out = Path(out_dir)
    before = previous_passes(out)
    for r in results:
        r["regressed"] = r["verdict"] == "fail" and (r["id"], r["run"]) in before
    numbers = summary(results)
    met = done(numbers)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False))
    (out / "summary.json").write_text(json.dumps({**numbers, "done": met, "baseline": BASELINE}, indent=2))

    def cells(p):
        return f"{p['passed']}/{p['runs']} ({p['share']}%) · {p['per_step']} s/step · ${p['spend']}/task"

    head = (f"<h1>{'Done bar met' if met else 'Done bar not met'}</h1><p>Known: {cells(numbers['known'])} "
            f"(bar ≥ {BAR['known']}%)<br>Unseen: {cells(numbers['unseen'])} (bar ≥ {BAR['unseen']}%)<br>"
            f"Unsafe: {numbers['unsafe']} · bar ≤ {BAR['per_step']} s/step, ≤ ${BAR['spend']}/task<br>"
            f"Baseline (mini loop, {BASELINE['run']}): {BASELINE['passed']}/{BASELINE['runs']}, median "
            f"{BASELINE['median_seconds']} s, ${BASELINE['median_spend']}/task</p>")
    categories = "".join(f"<tr><td>{html.escape(c)}</td><td>{cells(p)}</td></tr>"
                         for c, p in numbers["categories"].items())
    margins = " · ".join(f"{k}: {v}" for k, v in numbers["margins"].items())
    escalations = " · ".join(f"{html.escape(k)}: {v}" for k, v in numbers["escalations"].items()) or "none"
    rows = "".join(
        f"<tr class='{r['verdict']}'><td>{html.escape(r['id'])}</td><td>{html.escape(r['category'])}</td>"
        f"<td>{'unseen' if r['unseen'] else 'known'}</td><td>{r['run']}</td>"
        f"<td>{r['verdict']}{' — REGRESSED' if r['regressed'] else ''}</td><td>{html.escape(r['status'])}</td>"
        f"<td>{html.escape('; '.join(r['reasons'] + r['warnings']))}</td><td>{html.escape(r['final'])}</td>"
        f"<td>{r['steps']}</td><td>{r['per_step']}</td><td>{r['questions']}</td><td>{r['seconds']}</td>"
        f"<td>${r['spend']}</td><td>{r['mini_calls']}/{r['jev_calls']}</td><td>{html.escape(r['trace'])}</td></tr>"
        for r in results)
    page = (f"<!doctype html><meta charset='utf-8'><title>Jev bench</title><style>body{{font:14px system-ui;"
            f"margin:16px}}td,th{{border:1px solid #ccc;padding:4px}}.pass{{background:#e8f5e9}}.fail{{background:"
            f"#ffebee}}</style>{head}<h2>Categories</h2><table>{categories}</table><h2>Jev margins</h2><p>{margins}"
            f"</p><h2>Escalations</h2><p>{escalations}</p><h2>Runs</h2><table><tr><th>task</th><th>category</th>"
            f"<th>set</th><th>run</th><th>verdict</th><th>status</th><th>reasons</th><th>final</th><th>steps</th>"
            f"<th>s/step</th><th>questions</th><th>s</th><th>spend</th><th>mini/Jev calls</th><th>trace</th></tr>"
            f"{rows}</table>")
    path = out / "report.html"
    path.write_text(page)
    return path


def run(core, tasks, repeat, out_dir, pool=None, unseen=8, seed=0, timeout=1200, judge=None):
    """Every known task, `repeat` times; each round also samples `unseen` tasks from the pool (seed + round)."""
    from . import jev

    judge = judge or (lambda goal, rule, final: core.hidden(jev.judge, goal, rule, final))
    results = []
    for n in range(1, repeat + 1):
        batch = tasks + (sample(pool, unseen, seed + n) if pool and unseen else [])
        for spec in batch:
            task = core.new_task(spec["goal"], script=spec)
            deadline = time.monotonic() + timeout
            while task.status in ACTIVE and time.monotonic() < deadline:
                time.sleep(2)
            if task.status in ACTIVE:
                core.cancel(task)
                while task.status in ACTIVE:
                    time.sleep(1)
            results.append(grade(spec, task, n, judge))
            report(results, out_dir)
    return report(results, out_dir)


def save_case(trace_path, step, expectation, cases_dir):
    """Keep one Jev request of a trace (the state and questions it saw) with what it should have chosen. `step` is
    the request's number in the trace's "jev" list, 1 = first."""
    entry = json.loads(Path(trace_path).read_text())["jev"][int(step) - 1]
    out = Path(cases_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{Path(trace_path).stem}-{step}.json"
    path.write_text(json.dumps({"state": entry["state"], "questions": entry["questions"], "expect": expectation},
                               indent=2, ensure_ascii=False))
    return path


def words(text):
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def expected(expect, action):
    """Whether an action meets comma-separated expectations: op=CLICK, target~search, not target~cart."""
    for part_ in (p.strip() for p in expect.split(",")):
        if part_.startswith("op=") and action["op"] != part_[3:].split()[0]:
            return False
        if "target~" in part_:
            hit = words(part_.split("target~", 1)[1]) <= words(action["target"])
            if part_.startswith("not ") == hit:
                return False
    return True


def replay(cases_dir, ask=None):
    """Ask Jev every saved case again, without a browser. This uses the paid API: run it by hand."""
    from . import executor, jev

    ask = ask or jev.request
    results = []
    for path in sorted(Path(cases_dir).glob("*.json")):
        case = json.loads(path.read_text())
        answers = ask(case["state"], case["questions"])
        op = answers["next"]["choice"]
        head = executor.TARGETED.get(op, (None, None))[1]
        target = case["questions"][head]["options"][answers[head]["choice"]]["element"] if head in answers else ""
        got = {"op": op, "target": target}
        results.append({"case": path.name, "ok": expected(case["expect"], got), "got": got})
    return results
