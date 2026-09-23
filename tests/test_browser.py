"""Observation and core execution against local fixtures in a real headless Chrome. Run: uv run pytest -m browser"""

import json
import time

import pytest
from helpers import find, log

from assistant.browser import StalePage

pytestmark = pytest.mark.browser


def test_observation_covers_every_candidate_kind(browser_at):
    b = browser_at("/ops.html")
    page = b.observe()
    kinds = {a["kind"] for a in page["actions"]}
    assert {"click", "fill", "select", "drag", "scroll_down", "scroll_right", "wait"} <= kinds
    card = find(page, "Diet Coke 300 ml", "click")
    assert card["role"] == "clickable" and "₹40" in card["label"]
    assert not any(a["label"] == "₹40" for a in page["actions"])  # a card's children are not separate targets
    assert find(page, "Coke Zero", "click")["role"] == "clickable"  # cursor:pointer without an onclick attribute
    password = find(page, "Password", "fill")
    assert password["value"] == "" and password["constraints"]["type"] == "password"
    code = find(page, "One-time code", "fill")
    assert len(code["group"]) == 6
    boxes = [a for a in page["actions"] if a["kind"] == "fill" and a.get("constraints", {}).get("maxlength") == 1]
    assert len(boxes) == 1
    assert find(page, "Date", "fill")["constraints"]["type"] == "date"
    assert find(page, "Quantity", "click")["role"] == "slider"
    assert find(page, "Order history", "scroll_down")["node"] is not None
    assert find(page, "Offers", "scroll_right")
    assert [a["label"] for a in page["actions"] if a["kind"] == "drag"] == ["Alpha", "Beta", "Gamma"]


def test_upload_page_offers_hidden_inputs_and_drop_zones(browser_at):
    page = browser_at("/upload.html").observe()
    uploads = [a["label"] for a in page["actions"] if a["kind"] == "upload"]
    assert "Drop zone: Drag and drop a photo of the receipt here" in uploads
    assert "File input: Card statement (PDF, max 5 MB)" in uploads
    assert any(label.startswith("File input: Other documents") for label in uploads)


def test_moving_target_is_clicked_where_it_is_now(browser_at):
    b = browser_at("/guards.html")
    page = b.observe()
    b.evaluate("document.querySelector('#target').style.transform='translateX(200px)'")
    b.act("CLICK", find(page, "Continue"), page)
    assert log(b) == ["continue"]


@pytest.mark.parametrize("expression", [
    "document.querySelector('#context').textContent='Cart total: $100'",
    "document.querySelector('#target').setAttribute('aria-label','Delete account')",
    "document.querySelector('#field').value='London'",
    "document.querySelector('#toggle').checked=true",
    "document.querySelector('#target').disabled=true",
    "document.querySelector('#target').style.display='none'",
    "document.querySelector('#target').outerHTML=document.querySelector('#target').outerHTML",
])
def test_semantic_change_rejects_the_decision_before_input(browser_at, expression):
    b = browser_at("/guards.html")
    page = b.observe()
    b.evaluate(expression)
    with pytest.raises(StalePage):
        b.act("CLICK", find(page, "Continue"), page)
    assert log(b) == []


def test_unrelated_update_keeps_the_click_guard_but_not_the_full_check(browser_at):
    b = browser_at("/guards.html")
    page = b.observe()
    b.evaluate("document.querySelector('#unrelated').textContent='Fresh news'")
    assert b.fresh(page, find(page, "Continue")) and not b.fresh(page)


def test_overlay_blocks_the_click_before_input(browser_at):
    b = browser_at("/guards.html")
    page = b.observe()
    b.evaluate("const c=document.createElement('div'); c.style.cssText='position:fixed;inset:0;z-index:9999;"
               "background:white'; document.body.append(c)")
    with pytest.raises(StalePage):
        b.act("CLICK", find(page, "Continue"), page)
    assert log(b) == []


def test_password_values_never_leave_the_page(browser_at):
    page = browser_at("/guards.html").observe()
    assert "never expose this" not in json.dumps(page)
    assert find(page, "Secret", "fill")["value"] == "[filled]"
    assert not any(a["label"] == "Disabled" for a in page["actions"])


def test_native_dropdown_selects_an_observed_option(browser_at):
    b = browser_at("/guards.html")
    page = b.observe()
    assert [a["value"] for a in page["actions"] if a["kind"] == "select"] == ["Design"]
    b.act("SELECT", find(page, "Design", "select"), page)
    assert b.evaluate("document.querySelector('#category').value") == "Design"


def test_typing_replaces_text_and_waits_for_suggestions(browser_at):
    b = browser_at("/guards.html")
    page = b.observe()
    b.act("TYPE_TEXT", find(page, "Query", "fill"), page, text="Generated")
    page = b.observe()
    assert b.evaluate("document.querySelector('#query').value") == "Generated"
    assert any(a.get("role") == "option" for a in page["actions"])


def test_page_scrolls(browser_at):
    b = browser_at("/guards.html")
    page = b.observe()
    b.act("SCROLL_DOWN", find(page, "Page", "scroll_down"), page)
    assert b.evaluate("scrollY") > 0


def test_large_pages_stay_fast_and_capped(browser_at):
    b = browser_at("/guards.html")
    b.evaluate("document.body.insertAdjacentHTML('afterbegin', '<div id=rows>' + "
               "Array.from({length: 5000}, (_, i) => `<div style=\"cursor:pointer\">Row ${i}</div>`).join('') + "
               "'</div>')")
    started = time.monotonic()
    page = b.observe()
    assert time.monotonic() - started < 1.5
    assert sum(1 for a in page["actions"] if a.get("node") is not None) <= 250


def test_password_code_boxes_never_leave_the_page(browser_at):
    b = browser_at("/guards.html")
    b.evaluate("document.querySelectorAll('.pin input').forEach((box, i) => box.value = String(i + 1))")
    page = b.observe()
    assert find(page, "Card PIN", "fill")["value"] == "[filled]"
    assert "1234" not in json.dumps(page)


def test_covered_controls_are_not_offered_but_label_covered_inputs_are(browser_at):
    b = browser_at("/covered.html")
    page = b.observe()
    assert not any(a["label"] == "Hidden behind banner" for a in page["actions"])
    assert find(page, "Accept cookies")
    large = find(page, "Large", "click")
    assert large["role"] == "radio"
    b.act("CLICK", large, page)
    assert b.evaluate("document.querySelector('[value=large]').checked") is True


def test_countdowns_do_not_make_every_decision_stale(browser_at):
    b = browser_at("/countdown.html")
    page = b.observe()
    time.sleep(0.7)  # the countdown ticks several times
    assert b.fresh(page) and b.fresh(page, find(page, "Verify"))
    b.act("TYPE_TEXT", find(page, "Code", "fill"), page, text="482913")
    assert b.evaluate("document.getElementById('code').value") == "482913"
    page = b.observe()
    b.evaluate("document.getElementById('total').textContent = 'Total ₹790'")
    assert not b.fresh(page, find(page, "Verify"))
