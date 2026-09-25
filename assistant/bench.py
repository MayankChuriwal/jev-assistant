"""Live bench: run scripted goals on real sites through the chat core, answer NO at every final approval, and
grade each run. OTP and captcha questions still go to the user's web chat."""

import html
import json
import re
import time
import tomllib
from pathlib import Path

FALLBACK = "Choose what best fits my goal."
ACTIVE = {"starting", "waiting", "running", "paused"}


def load(path):
    tasks = tomllib.loads(Path(path).read_text())["task"]
    return [{"require": [], "answers": [], "fallback": FALLBACK, **t} for t in tasks]


def reply(spec, question):
    for pattern, answer in spec.get("answers", []):
        if re.search(pattern, question, re.IGNORECASE):
            return answer
    return spec.get("fallback", FALLBACK)


def grade(spec, task, run, judge):
    state = task.agent.state if task.agent else {}
    result = next((m["text"] for m in reversed(state.get("messages", [])) if m.get("kind") == "result"), "")
    final = task.bench["gate"] or result
    reasons = []
    if any(h.get("committed") for h in state.get("history", [])):
        reasons.append("committed during a bench run")
    if spec["end"] == "gate" and not task.bench["gate"]:
        reasons.append("never reached the final approval")
    if spec["end"] == "result" and task.status != "done":
        reasons.append(f"ended {task.status}")
    if spec["end"] == "file" and not state.get("files"):
        reasons.append("no file arrived")
    missing = [w for w in spec.get("require", []) if w.lower() not in final.lower()]
    if missing and spec["end"] != "file" and final:
        reasons.append("missing: " + ", ".join(missing))
    verdict = "fail" if reasons else judge(spec["goal"], spec["rule"], final)
    if verdict == "fail" and not reasons:
        reasons.append("the judge says the result doesn't meet the rule")
    return {"id": spec["id"], "run": run, "status": task.status, "verdict": verdict, "reasons": reasons,
            "final": final[:400], "steps": len(state.get("history", [])),
            "questions": len(task.bench["asked"]), "seconds": round(state.get("elapsed_ms", 0) / 1000),
            "spend": round(state.get("spend", 0.0), 3), "trace": state.get("trace_path", "")}


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
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False))
    passed = sum(r["verdict"] == "pass" for r in results)
    share = round(100 * passed / len(results)) if results else 0
    rows = "".join(
        f"<tr class='{r['verdict']}'><td>{html.escape(r['id'])}</td><td>{r['run']}</td>"
        f"<td>{r['verdict']}{' — REGRESSED' if r['regressed'] else ''}</td>"
        f"<td>{html.escape(r['status'])}</td><td>{html.escape('; '.join(r['reasons']))}</td>"
        f"<td>{html.escape(r['final'])}</td><td>{r['steps']}</td><td>{r['questions']}</td><td>{r['seconds']}</td>"
        f"<td>${r['spend']}</td><td>{html.escape(r['trace'])}</td></tr>" for r in results)
    page = (f"<!doctype html><meta charset='utf-8'><title>Jev bench</title><style>body{{font:14px system-ui;"
            f"margin:16px}}td,th{{border:1px solid #ccc;padding:4px}}.pass{{background:#e8f5e9}}.fail{{background:"
            f"#ffebee}}</style><h1>{passed} of {len(results)} passed ({share}%)</h1><table><tr><th>task</th>"
            f"<th>run</th><th>verdict</th><th>status</th><th>reasons</th><th>final</th><th>steps</th>"
            f"<th>questions</th><th>s</th><th>spend</th><th>trace</th></tr>{rows}</table>")
    path = out / "report.html"
    path.write_text(page)
    return path


def run(core, tasks, repeat, out_dir, timeout=1200, judge=None):
    from . import jev

    judge = judge or (lambda goal, rule, final: core.hidden(jev.judge, goal, rule, final))
    results = []
    for n in range(1, repeat + 1):
        for spec in tasks:
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
    """Keep one decision of a trace (the page and context mini saw) with what it should have done."""
    trace = json.loads(Path(trace_path).read_text())
    payload = trace["decisions"][int(step) - 1]["seen"]
    out = Path(cases_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{Path(trace_path).stem}-{step}.json"
    path.write_text(json.dumps({"payload": payload, "expect": expectation}, indent=2, ensure_ascii=False))
    return path


def words(text):
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def expected(expect, action):
    """Whether an action meets comma-separated expectations: op=ASK, target~where to, not target~pickup."""
    for part in (p.strip() for p in expect.split(",")):
        if part.startswith("op=") and action["op"] != part[3:].split()[0]:
            return False
        if "target~" in part:
            hit = words(part.split("target~", 1)[1]) <= words(action["target"])
            if part.startswith("not ") == hit:
                return False
    return True


def replay(cases_dir, decide=None):
    """Run mini on every saved case, without a browser."""
    from . import mind

    decide = decide or mind.decide
    results = []
    for path in sorted(Path(cases_dir).glob("*.json")):
        case = json.loads(path.read_text())
        step, _ = decide(case["payload"])
        results.append({"case": path.name, "ok": expected(case["expect"], step["action"]), "got": step["action"]})
    return results
