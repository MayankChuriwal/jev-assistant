"""Browser contracts that need no Chrome."""

import os
import subprocess
import sys
import time
from copy import deepcopy
from unittest.mock import Mock

import pytest
from helpers import until

import assistant.browser as browser
from assistant.browser import Browser, StalePage, fingerprint, merge, safe_name


def state(url="https://a.test/", text="Main", actions=None):
    return {"url": url, "title": "T", "text": text, "scroll": {"y": 0}, "actions": actions or [],
            "marker": [url], "page_key": [1], "guards": {}, "omitted_actions": 0}


def bare():
    b = Browser.__new__(Browser)
    b.tabs, b.active, b.frames, b.dialogs = {"T": "S"}, "T", {}, {}
    return b


def test_merge_numbers_elements_page_wide_and_marks_frame_actions():
    main = state(actions=[{"id": "e1", "kind": "click", "node": 5, "label": "Buy"},
                          {"id": "page_scroll_down", "kind": "scroll_down", "node": None, "label": "Page"},
                          {"id": "wait", "kind": "wait", "node": None, "label": "Wait"}])
    frame = state("https://pay.test/f", "CVV", [{"id": "e1", "kind": "fill", "node": 5, "label": "CVV"},
                                                {"id": "page_scroll_down", "kind": "scroll_down", "node": None,
                                                 "label": "Page"},
                                                {"id": "wait", "kind": "wait", "node": None, "label": "Wait"}])
    page = merge([(None, main), ("F1", frame)])
    assert [a["id"] for a in page["actions"]] == ["e1", "page_scroll_down", "wait", "e2", "page_scroll_down_f1"]
    assert page["actions"][3]["frame"] == "F1" and page["actions"][3]["node"] == 5
    assert page["actions"][4]["label"] == "Frame pay.test"
    assert set(page["frames"]) == {"main", "F1"}
    assert "[Frame pay.test]\nCVV" in page["text"]


def test_fingerprint_tracks_values_and_identity_not_screenshots():
    p = merge([(None, state(actions=[{"id": "e1", "kind": "fill", "node": 1, "label": "City", "value": "Zurich"}]))])
    other = deepcopy(p)
    other["screenshot"] = "changed"
    assert fingerprint(p) == fingerprint(other)
    other["actions"][0]["value"] = "London"
    assert fingerprint(p) != fingerprint(other)


def test_stale_page_is_rejected_before_any_input():
    b = bare()
    b.fresh = Mock(return_value=False)
    b._do_click = Mock()
    with pytest.raises(StalePage):
        b.act("CLICK", {"id": "e1", "kind": "click", "node": 1, "label": "Go"}, {})
    b._do_click.assert_not_called()


def test_unknown_operations_are_refused():
    b = bare()
    b.fresh = Mock(return_value=True)
    with pytest.raises(ValueError, match="Unsupported"):
        b.act("CLOSE", {"id": "x", "kind": "click", "node": None, "label": "x"}, {})


@pytest.mark.parametrize("response", [{"exceptionDetails": {"text": "context destroyed"}}, {"result": {}}])
def test_interrupted_dropdown_cannot_be_retried_as_stale(response):
    b = bare()
    b.call = Mock(return_value=response)
    with pytest.raises(RuntimeError, match="Dropdown execution"):
        b._do_select({"id": "e1", "kind": "select", "node": 1, "value": "Design"})
    assert b.call.call_count == 1


def test_slow_page_is_observed_after_its_deadline():
    b = bare()
    b.evaluate = Mock(return_value=[1.0, "loading"])
    started = time.monotonic()
    b._wait_loaded(previous=0.0, timeout=0.2)
    assert time.monotonic() - started < 1.0


def test_blocked_input_reports_an_open_dialog_instead_of_hanging(monkeypatch):
    b = bare()
    monkeypatch.setattr(browser.PUMP, "flush", lambda: None)
    b.call = Mock(side_effect=TimeoutError("timed out"))

    def dialog_appears():
        b.dialogs["S"] = {"type": "confirm", "message": "Sure?"}

    monkeypatch.setattr(browser.PUMP, "flush", dialog_appears)
    assert b.input("Input.dispatchMouseEvent", type="mouseReleased", x=1, y=1) is None
    assert b.call.call_count == 1
    assert b.input("Input.dispatchMouseEvent", type="mouseMoved", x=1, y=1) is None  # skipped while it is open
    assert b.call.call_count == 1
    b.dialogs.clear()
    monkeypatch.setattr(browser.PUMP, "flush", lambda: None)
    b._tab_closed = Mock(return_value=False)
    with pytest.raises(RuntimeError, match="did not return"):
        b.input("Input.dispatchMouseEvent", type="mouseReleased", x=1, y=1)


@pytest.mark.parametrize("name", ["../../evil.txt", "..\\..\\evil.txt", "/etc/passwd", "", "..", "a/b/c.pdf"])
def test_download_names_stay_one_safe_component(name):
    cleaned = safe_name(name)
    assert "/" not in cleaned and "\\" not in cleaned and cleaned not in {"", ".", ".."}


def test_every_key_jev_can_choose_can_be_pressed():
    from assistant.questions import KEY_HELP

    assert set(browser.KEYS) == set(KEY_HELP)


def test_the_agent_never_falls_back_to_the_default_daemon():
    env = {k: v for k, v in os.environ.items() if k not in {"BU_NAME", "BU_CDP_URL"}}
    code = ("import os, assistant.browser, browser_harness.helpers as h; "
            "print(os.environ['BU_NAME'], os.environ['BU_CDP_URL'], h.NAME)")
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True).stdout
    assert out.split() == ["jev", "http://127.0.0.1:9333", "jev"]
    refused = subprocess.run([sys.executable, "-c", "import assistant.browser"], env={**env, "BU_NAME": "default"},
                             capture_output=True, text=True)
    assert refused.returncode != 0 and "BU_NAME" in refused.stderr


def test_a_failure_after_input_is_uncertain_never_stale():
    b = bare()
    b.fresh = Mock(return_value=True)
    b.call = Mock(return_value={})

    def half_done(action, **_):
        b.input("Input.dispatchMouseEvent", type="mousePressed", x=1, y=1, button="left", clickCount=1)
        raise StalePage("re-rendered after the press")

    b._do_click = half_done
    with pytest.raises(browser.ExecutionUncertain):
        b.act("CLICK", {"id": "e1", "kind": "click", "node": 1, "label": "Go"}, {})


def test_a_failure_before_input_stays_stale():
    b = bare()
    b.fresh = Mock(return_value=True)
    b._do_click = Mock(side_effect=StalePage("covered"))
    with pytest.raises(StalePage):
        b.act("CLICK", {"id": "e1", "kind": "click", "node": 1, "label": "Go"}, {})


def test_code_boxes_are_all_located_before_any_typing():
    b = bare()
    b.input = Mock()
    points = iter([(1, 1), (2, 2), StalePage("re-rendered")])

    def point(_action):
        p = next(points)
        if isinstance(p, Exception):
            raise p
        return p

    b._point = point
    with pytest.raises(StalePage):
        b._do_type_text({"id": "e1", "kind": "fill", "node": 1, "group": [1, 2, 3], "label": "Code"}, text="123")
    b.input.assert_not_called()


def test_the_event_pump_survives_an_unexpected_error(monkeypatch):
    batches = [KeyError("boom"), [{"method": "Page.frameNavigated", "session_id": "s1"}]]

    def drain():
        batch = batches.pop(0) if batches else []
        if isinstance(batch, Exception):
            raise batch
        return batch

    monkeypatch.setattr(browser, "drain_events", drain)
    pump, seen = browser.EventPump(), []
    pump.register("s1", seen.append)
    try:
        assert until(lambda: seen) and seen[0]["method"] == "Page.frameNavigated"
    finally:
        pump.running = False


def test_visual_candidates_reject_unknown_cells_coordinates_and_incomplete_frames():
    b = bare()
    b._visual_grid = {"cells": {"A1": {"x": 0, "y": 0, "w": 100, "h": 100}},
                      "marker": ["same"], "screenshot_ref": "shot-1", "tab": "T", "session": "S",
                      "candidates": {}}
    b.evaluate = Mock(side_effect=lambda expression: ["same"] if expression == browser.MARKER else 42)
    page = {"frames": {"main": {"marker": ["same"]}},
            "completeness": {"frames_incomplete": []}, "fingerprint": "page-1"}
    with pytest.raises(ValueError, match="cell"):
        b.visual_candidates(page, [{"cell": "Z9", "label": "Blue", "text": ""}])
    with pytest.raises(ValueError, match="coordinates"):
        b.visual_candidates(page, [{"cell": "A1", "label": "Blue", "text": "", "x": 50}])
    with pytest.raises(StalePage, match="frame"):
        b.visual_candidates({**page, "completeness": {"frames_incomplete": ["F1"]}},
                            [{"cell": "A1", "label": "Blue", "text": ""}])
    assert b.visual_candidates(page, [{"cell": "A1", "label": "Blue", "text": ""}])[0]["visual_ref"] == "shot-1"
