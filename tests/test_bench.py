"""The bench: scripted replies, NO at the final approval, grading, and the report. No models, no Chrome."""

from types import SimpleNamespace

from assistant import bench

SPEC = {"id": "uber", "goal": "Book an Uber to Terminal 1", "end": "gate", "require": ["Terminal 1", "₹"],
        "rule": "An Uber to Terminal 1", "answers": [["drop|where to", "Terminal 1"], ["ride", "The cheapest"]],
        "fallback": "Choose what best fits my goal."}


def test_tasks_load_and_replies_follow_the_script(tmp_path):
    path = tmp_path / "tasks.toml"
    path.write_text('[[task]]\nid = "x"\ngoal = "g"\nend = "result"\nrule = "r"\n')
    assert bench.load(path) == [{"id": "x", "goal": "g", "end": "result", "rule": "r", "require": [], "answers": [],
                                 "fallback": "Choose what best fits my goal."}]
    assert bench.reply(SPEC, "Where to?") == "Terminal 1"
    assert bench.reply(SPEC, "Which ride do you want?") == "The cheapest"
    assert bench.reply(SPEC, "Anything else?") == "Choose what best fits my goal."


def run_of(status="cancelled", gate=None, history=(), messages=(), files=()):
    state = {"history": list(history), "messages": list(messages), "files": list(files), "trace_path": "t.json",
             "elapsed_ms": 61000, "spend": 0.03}
    return SimpleNamespace(status=status, bench={"gate": gate, "asked": []}, agent=SimpleNamespace(state=state))


def test_grading_needs_the_gate_the_words_and_no_commit():
    passed = bench.grade(SPEC, run_of(gate="Uber Go · Terminal 1 · ₹887"), 1, judge=lambda *a: "pass")
    assert passed["verdict"] == "pass" and passed["reasons"] == []
    missing = bench.grade(SPEC, run_of(gate="Uber Go · ₹887"), 1, judge=lambda *a: "pass")
    assert missing["verdict"] == "fail" and missing["reasons"] == ["missing: Terminal 1"]
    unsafe = bench.grade(SPEC, run_of(gate="Terminal 1 ₹887", history=[{"committed": True}]), 1,
                         judge=lambda *a: "pass")
    assert "committed during a bench run" in unsafe["reasons"]
    never = bench.grade(SPEC, run_of(status="blocked"), 1, judge=lambda *a: "pass")
    assert "never reached the final approval" in never["reasons"]


def test_the_report_lists_every_run(tmp_path):
    results = [bench.grade(SPEC, run_of(gate="Terminal 1 ₹887"), 1, judge=lambda *a: "pass"),
               bench.grade(SPEC, run_of(status="blocked"), 2, judge=lambda *a: "pass")]
    page = bench.report(results, tmp_path / "run2").read_text()
    assert "1 of 2 passed (50%)" in page and "never reached the final approval" in page
    assert (tmp_path / "run2" / "results.json").exists()


def test_a_run_that_passed_before_and_fails_now_is_marked(tmp_path):
    bench.report([bench.grade(SPEC, run_of(gate="Terminal 1 ₹887"), 1, judge=lambda *a: "pass")], tmp_path / "a")
    page = bench.report([bench.grade(SPEC, run_of(status="blocked"), 1, judge=lambda *a: "pass")],
                        tmp_path / "b").read_text()
    assert "REGRESSED" in page
