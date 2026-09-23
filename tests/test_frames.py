"""Shadow DOM, same-origin frames, and cross-origin frames in a real headless Chrome. Run: uv run pytest -m browser"""

import pytest
from helpers import find, log, observe_until, until

from assistant.browser import StalePage

pytestmark = pytest.mark.browser
HAS_FRAME = lambda p: any(a.get("frame") for a in p["actions"])  # noqa: E731


def test_shadow_and_same_origin_frame_controls_are_clickable(browser_at):
    b = browser_at("/events.html")
    page = b.observe()
    b.act("CLICK", find(page, "Shadow button"), page)
    page = b.observe()
    b.act("CLICK", find(page, "Inner button"), page)
    assert log(b) == ["shadow", "frame"]


def test_cross_origin_payment_frame_is_observed_and_driven(browser_at):
    b = browser_at("/pay.html")
    page = observe_until(b, HAS_FRAME)
    cvv = find(page, "CVV", "fill")
    assert cvv["frame"] and cvv["value"] == ""
    assert "[Frame localhost:" in page["text"]
    b.act("TYPE_TEXT", cvv, page, text="123")
    page = b.observe()
    b.act("CLICK", find(page, "Pay ₹79", "click"), page)
    assert until(lambda: log(b) == ["pay:123"])


def test_covered_frame_is_not_clicked(browser_at):
    b = browser_at("/pay.html")
    page = observe_until(b, HAS_FRAME)
    b.evaluate("const c=document.createElement('div'); c.style.cssText='position:fixed;inset:0;z-index:9;"
               "background:white'; document.body.append(c)")
    with pytest.raises(StalePage):
        b.act("CLICK", find(page, "Pay ₹79", "click"), page)
    assert log(b) == []


def test_offscreen_frames_never_reach_jev(browser_at):
    b = browser_at("/pay.html")
    page = observe_until(b, HAS_FRAME)
    assert not any("Win a prize" in a["label"] for a in page["actions"])
    assert "Win a prize" not in page["text"]


def test_a_change_inside_the_frame_invalidates_the_full_check(browser_at):
    b = browser_at("/pay.html")
    page = observe_until(b, HAS_FRAME)
    frame = find(page, "CVV", "fill")["frame"]
    b.evaluate("document.body.insertAdjacentHTML('beforeend', '<p>Session expired</p>')", b.frames[frame]["session"])
    assert not b.fresh(page)
