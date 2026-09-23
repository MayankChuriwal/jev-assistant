"""Browser contracts that need no Chrome."""

import time
from copy import deepcopy
from unittest.mock import Mock

import pytest

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
