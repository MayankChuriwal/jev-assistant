"""Observation and core execution against local fixtures in a real headless Chrome. Run: uv run pytest -m browser"""

import base64
import json
import struct
import threading
import time

import pytest
from helpers import find, log

from assistant import rules
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


def test_fields_know_the_button_that_enter_submits(browser_at):
    page = browser_at("/pay-button.html").observe()
    assert find(page, "CVV", "fill")["submit"] == "Pay ₹79"
    assert "submit" not in find(browser_at("/guards.html").observe(), "City", "fill")


def test_amount_inside_the_pay_button_is_verbatim(browser_at):
    page = browser_at("/pay-button.html").observe()
    assert rules.verbatim("₹79", page["text"])


def test_screenshots_crop_to_an_element_and_blur_secret_fields(browser_at):
    b = browser_at("/pay-button.html")
    page = b.observe()
    b.act("TYPE_TEXT", find(page, "Card number", "fill"), page, text="4111111111111111")
    page = b.observe()
    card = find(page, "Card number", "fill")
    plain = base64.b64decode(b.screenshot(card))
    masked = base64.b64decode(b.screenshot(card, masked={(None, card["node"])}))
    width, height = struct.unpack(">II", plain[16:24])
    assert abs(width - (card["rect"]["w"] + 32)) <= 2 and abs(height - (card["rect"]["h"] + 32)) <= 2
    assert plain == masked  # a filled field is hidden in every capture, registered as a secret or not (P2-04)
    assert b.evaluate("document.getElementById('card').style.filter") == ""
    whole = base64.b64decode(b.screenshot())
    assert struct.unpack(">II", whole[16:24]) == (1120, 780)


def test_the_focused_control_can_be_named(browser_at):
    b = browser_at("/pay-button.html")
    b.observe()
    b.evaluate("document.querySelector('button').focus()")
    assert b.focused_label() == "Pay ₹79"
    b.evaluate("document.activeElement.blur()")
    assert b.focused_label() == ""


def test_tasks_starting_together_share_one_daemon(chrome, site):
    from browser_harness.admin import restart_daemon

    from assistant.browser import Browser

    restart_daemon("jev-test")  # no daemon yet: two tasks starting at once must not each spawn or restart one
    opened, errors = [], []

    def start():
        try:
            opened.append(Browser(site["a"] + "/next.html"))
        except Exception as error:
            errors.append(error)

    threads = [threading.Thread(target=start) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    try:
        assert errors == [] and len(opened) == 2
        assert all("Second page." in b.observe()["text"] for b in opened)
    finally:
        for b in opened:
            b.close()


def test_only_file_drops_become_upload_zones(browser_at):
    b = browser_at("/next.html")
    b.evaluate("document.body.insertAdjacentHTML('afterbegin', ['Drop-off location', 'Drag the map to adjust your "
               "pickup', 'Drag and drop your files here'].map(t => `<div style=\"width:320px;height:60px\">${t}</div>`)"
               ".join(''))")
    zones = [a["label"] for a in b.observe()["actions"] if a.get("drop")]
    assert zones == ["Drop zone: Drag and drop your files here"]


def test_long_dropdowns_never_crowd_out_other_controls(browser_at):
    b = browser_at("/next.html")
    b.evaluate("""(() => {
      const s = document.createElement('select');
      s.setAttribute('aria-label', 'Country');
      for (let i = 0; i < 300; i++) s.add(new Option('Country ' + i, 'c' + i));
      document.body.prepend(s);
      for (let i = 0; i < 5; i++)
        document.body.append(Object.assign(document.createElement('button'), {textContent: 'Action ' + i}));
      return true;
    })()""")
    labels = [a["label"] for a in b.observe()["actions"]]
    assert all(f"Action {i}" in labels for i in range(5)) and any(x.startswith("Country → ") for x in labels)


def test_screenshots_hide_every_filled_field(browser_at, monkeypatch):
    b = browser_at("/next.html")
    b.evaluate("document.body.insertAdjacentHTML('afterbegin', '<input id=\"otp\" value=\"482913\"><input id=\"empty\">"
               "<textarea id=\"note\">Gate 4</textarea>')")
    b.observe()
    b.evaluate("document.getElementById('otp').replaceWith(Object.assign(document.createElement('input'),"
               " {id: 'otp', value: '482913'}))")  # the page re-rendered the field after it was typed
    styles = ("['otp', 'empty', 'note'].map(id => { const e = document.getElementById(id);"
              " return getComputedStyle(e).webkitTextSecurity + '|' + e.style.filter; })")
    seen, real = [], b.call

    def spy(method, session=None, **params):
        if method == "Page.captureScreenshot":
            seen.append(b.evaluate(styles))
        return real(method, session, **params)

    monkeypatch.setattr(b, "call", spy)
    b.screenshot()
    assert seen == [["disc|blur(12px)", "none|", "disc|blur(12px)"]]
    assert b.evaluate(styles) == ["none|", "none|", "none|"]


def test_resume_and_browse_drops_are_upload_zones(browser_at):
    b = browser_at("/next.html")
    b.evaluate("document.body.insertAdjacentHTML('afterbegin', ['Drag & drop your resume/CV here', 'Drop here or "
               "browse'].map(t => `<div style=\"width:320px;height:60px\">${t}</div>`).join(''))")
    zones = [a["label"] for a in b.observe()["actions"] if a.get("drop")]
    assert zones == ["Drop zone: Drag & drop your resume/CV here", "Drop zone: Drop here or browse"]


def test_only_fields_that_open_something_get_an_open_twin(browser_at):
    b = browser_at("/next.html")
    b.evaluate("document.body.insertAdjacentHTML('afterbegin', '<input aria-label=\"Phone number\" type=\"tel\">"
               "<input aria-label=\"Travel date\" type=\"date\"><input aria-label=\"City\" role=\"combobox\">')")
    clicks = [a["label"] for a in b.observe()["actions"] if a["kind"] == "click"]
    assert "Open Phone number" not in clicks and "Open Travel date" in clicks and "Open City" in clicks


def test_code_boxes_labelled_one_of_four_are_one_field(browser_at):
    b = browser_at("/next.html")
    b.evaluate("document.body.insertAdjacentHTML('afterbegin', '<div>' + [1, 2, 3, 4].map(n => "
               "`<input aria-label=\"Enter an OTP Code ${n} of 4\" inputmode=\"numeric\">`).join('') + '</div>')")
    fills = [a for a in b.observe()["actions"] if a["kind"] == "fill"]
    assert len(fills) == 1 and len(fills[0]["group"]) == 4 and fills[0]["label"] == "Enter an OTP Code"



def test_a_new_code_replaces_the_digits_a_rejected_one_left_in_the_boxes(browser_at):
    b = browser_at("/next.html")
    b.evaluate("document.body.insertAdjacentHTML('afterbegin', '<div>' + [1, 2, 3, 4].map(n => "
               "`<input aria-label=\"Enter an OTP Code ${n} of 4\" inputmode=\"numeric\" maxlength=\"1\" "
               "value=\"9\">`).join('') + '</div>')")
    page = b.observe()
    b.act("TYPE_TEXT", next(a for a in page["actions"] if a.get("group")), page, text="4821")
    assert b.evaluate("[...document.querySelectorAll('input[maxlength=\"1\"]')].map(i => i.value).join('')") == "4821"


def test_a_control_with_no_name_is_labelled_by_the_text_around_it(browser_at):
    b = browser_at("/next.html")
    b.evaluate("document.body.insertAdjacentHTML('afterbegin', '<div><span>Margherita Pizza ₹170</span>"
               "<div style=\"width:40px;height:20px\" onclick=\"1\"></div></div>')")
    labels = [a["label"] for a in b.observe()["actions"] if a["kind"] == "click"]
    assert any(label.startswith("clickable in “Margherita Pizza ₹170") for label in labels)


def test_visible_buttons_hidden_from_screen_readers_are_offered_and_told_apart(browser_at):
    b = browser_at("/next.html")
    card = ("<div><p>{0}</p><div aria-hidden=\\\"true\\\"><button onclick=\\\"1\\\">ADD</button></div></div>")
    html = card.format("Margherita Pizza ₹170") + card.format("Farmhouse Pizza ₹290")
    b.evaluate(f"document.body.insertAdjacentHTML('afterbegin', \"{html}\")")
    adds = [a["label"] for a in b.observe()["actions"] if a["kind"] == "click" and a["label"].startswith("ADD")]
    assert len(adds) == 2 and "Margherita" in adds[0] and "Farmhouse" in adds[1]


def test_same_named_buttons_are_told_apart_by_the_card_they_sit_in(browser_at):
    b = browser_at("/next.html")
    card = ("<div><p>{0}</p><div aria-hidden=\\\"true\\\"><div><div><div><span>−</span><button onclick=\\\"1\\\">"
            "ADD</button><span>+</span></div><span>Customisable</span></div></div></div></div>")
    html = card.format("Cheesy Macaroni Pizza ₹302") + card.format("Garlic Pizza ₹199")
    b.evaluate(f"document.body.insertAdjacentHTML('afterbegin', \"{html}\")")
    adds = [a["label"] for a in b.observe()["actions"] if a["kind"] == "click" and a["label"].startswith("ADD")]
    assert len(adds) == 2 and "Cheesy Macaroni" in adds[0] and "Garlic" in adds[1]


def test_a_clickable_wrapper_around_a_same_named_link_is_one_control(browser_at):
    b = browser_at("/next.html")
    html = "<div onclick=\\\"1\\\" style=\\\"cursor:pointer\\\"><a href=\\\"#help\\\">Help</a></div>"
    b.evaluate(f"document.body.insertAdjacentHTML('afterbegin', \"{html}\")")
    helps = [a["label"] for a in b.observe()["actions"] if a["kind"] == "click" and a["label"].startswith("Help")]
    assert helps == ["Help"]


def test_a_button_named_apart_from_its_visible_words_carries_both(browser_at):
    b = browser_at("/next.html")
    html = "<button aria-label=\\\"Make payment, tap to go to payment\\\">Proceed to Pay</button>"
    b.evaluate(f"document.body.insertAdjacentHTML('afterbegin', \"{html}\")")
    labels = [a["label"] for a in b.observe()["actions"] if a["kind"] == "click"]
    assert any("Make payment" in label and "Proceed to Pay" in label for label in labels)


def test_a_look_that_hovers_moves_the_pointer_without_clicking(browser_at):
    b = browser_at("/next.html")
    b.evaluate("document.body.insertAdjacentHTML('afterbegin', "
               "'<div id=sort style=\"width:200px;height:40px\">Sort by</div>'); window.__clicks = 0; "
               "window.__over = 0; sort.onclick = () => __clicks++; sort.onmouseover = () => __over++; 1")
    page = b.observe()
    look = next(a for a in page["actions"] if a["kind"] == "look")
    x, y = b.evaluate("(() => { const r = sort.getBoundingClientRect(); return [r.x + 20, r.y + 20]; })()")
    b.act("LOOK", look, page, point=[x, y], hover=True)
    assert b.evaluate("[__over > 0, __clicks]") == [True, 0]


def test_a_styled_radio_is_offered_through_its_visible_label(browser_at):
    b = browser_at("/next.html")
    b.evaluate("document.body.insertAdjacentHTML('afterbegin', '<label><input type=radio name=sort id=asc "
               "style=\"display:none\">Price: Low to High</label>'); 1")
    page = b.observe()
    option = next(a for a in page["actions"] if a["kind"] == "click" and a["label"] == "Price: Low to High")
    assert option["role"] == "radio"
    b.act("CLICK", option, page)
    assert b.evaluate("asc.checked") is True


def test_a_styled_radio_with_an_empty_label_is_offered_through_its_wrapper(browser_at):
    b = browser_at("/next.html")
    b.evaluate("document.body.insertAdjacentHTML('afterbegin', '<div onclick=\"med.checked = true\" "
               "style=\"position:relative;width:16px;height:19px\"><input type=radio id=med aria-label=\"Medium "
               "(serves 2), Cost: 687 rupees\" style=\"position:absolute;opacity:0;width:0;height:0\"><span "
               "style=\"display:inline-block;width:16px;height:19px\"></span><label for=med></label></div>'); 1")
    page = b.observe()
    option = next(a for a in page["actions"] if a["kind"] == "click" and a["label"].startswith("Medium (serves 2)"))
    b.act("CLICK", option, page)
    assert option["role"] == "radio" and b.evaluate("med.checked") is True

def test_forgetting_a_site_signs_the_agent_out(browser_at, site):
    from assistant.browser import forget_sites

    b = browser_at("/next.html")
    b.evaluate("document.cookie = 'session=abc; path=/'; localStorage.setItem('token', 'xyz'); true")
    assert "session=abc" in b.evaluate("document.cookie")
    assert forget_sites([site["a"]]) == {"127.0.0.1": 1}
    b.navigate(site["a"] + "/next.html")
    assert b.evaluate("document.cookie") == "" and b.evaluate("localStorage.getItem('token')") is None


def test_fields_that_share_a_label_are_told_apart(browser_at):
    b = browser_at("/next.html")
    b.evaluate("document.body.insertAdjacentHTML('afterbegin', '<input aria-label=\"Search for a location\" "
               "placeholder=\"Pickup location\"><input aria-label=\"Search for a location\" "
               "placeholder=\"Dropoff location\">')")
    fills = [a["label"] for a in b.observe()["actions"] if a["kind"] == "fill"]
    assert fills == ["Search for a location (Pickup location)", "Search for a location (Dropoff location)"]
