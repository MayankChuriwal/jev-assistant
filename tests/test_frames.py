"""Shadow DOM, same-origin frames, and cross-origin frames in a real headless Chrome. Run: uv run pytest -m browser"""

import time

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


def test_a_change_to_a_frame_target_invalidates_it(browser_at):
    b = browser_at("/pay.html")
    page = observe_until(b, HAS_FRAME)
    cvv = find(page, "CVV", "fill")
    b.evaluate("document.querySelector('label').firstChild.textContent = 'PIN '", b.frames[cvv["frame"]]["session"])
    assert not b.fresh(page, cvv)


def test_changing_third_party_frames_do_not_block_other_targets(browser_at):
    b = browser_at("/pay.html")
    page = observe_until(b, lambda p: HAS_FRAME(p) and "agents online" in p["text"])
    time.sleep(0.6)  # the support widget changes several times
    assert b.fresh(page)
    b.act("TYPE_TEXT", find(page, "CVV", "fill"), page, text="123")
    b.act("TYPE_TEXT", find(page, "Coupon", "fill"), page, text="SAVE10")
    assert b.evaluate("document.getElementById('coupon').value") == "SAVE10"


def test_a_cross_origin_frame_inside_a_same_origin_frame_can_be_driven(browser_at):
    b = browser_at("/nested.html")
    page = observe_until(b, HAS_FRAME)
    b.act("TYPE_TEXT", find(page, "CVV", "fill"), page, text="456")
    page = b.observe()
    b.act("CLICK", find(page, "Pay ₹79", "click"), page)
    assert until(lambda: log(b) == ["pay:456"])


def test_tracker_pixels_are_not_read(browser_at, site):
    b = browser_at("/next.html")
    frame = f"{site['b']}/widget-frame.html"
    b.evaluate(f"document.body.insertAdjacentHTML('beforeend', '<iframe src=\"{frame}\" width=\"1\" height=\"1\">"
               f"</iframe><iframe src=\"{frame}\" width=\"320\" height=\"160\"></iframe>')")
    assert observe_until(b, lambda p: len(p["frames"]) >= 2) is not None
    time.sleep(0.5)  # time for the 1×1 frame to attach as well
    assert len(b.observe()["frames"]) == 2  # the page and the visible frame, never the tracking pixel


def test_a_frame_that_closes_while_being_attached_is_skipped(browser_at):
    b = browser_at("/pay.html")
    page = observe_until(b, HAS_FRAME)
    main = b._frame_ids(b.session)[0]
    gone = {"type": "iframe", "targetId": "0" * 32, "parentFrameId": main}  # listed, then navigated away
    b._sync_frames([gone])  # Chrome answers "No target with given id found": the page moved on, observe again
    assert "0" * 32 not in b.frames and page["actions"]


def test_a_support_chat_frame_is_never_read(browser_at, site):
    # live SmartBuy: GyFTR's Salesforce chat panel (…my.site.com/ESW…) opened beside the sign-in, and every control
    # offered was the chat's, so its password box and "Request OTP" were never reached
    b = browser_at("/next.html")
    chat = f"{site['b']}/ESWChatBot/index.html"
    b.evaluate(f"document.body.insertAdjacentHTML('beforeend', '<iframe src=\"{chat}\" width=\"320\" height=\"480\">"
               "</iframe>')")
    time.sleep(1.5)  # time for the frame to attach
    page = b.observe()
    assert not any("Scroll the latest messages" in (a.get("label") or "") for a in page["actions"])
    assert "Let’s chat" not in page["text"]
