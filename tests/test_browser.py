"""Observation and core execution against local fixtures in a real headless Chrome. Run: uv run pytest -m browser"""

import base64
import json
import struct
import threading
import time

import pytest
from helpers import find, log

import assistant.browser as browser
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
    assert struct.unpack(">II", whole[16:24]) == (browser.VIEWPORT["width"], browser.VIEWPORT["height"])


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


def test_unnamed_little_number_boxes_in_a_row_are_one_code_field(browser_at):
    b = browser_at("/otp-boxes.html")
    page = b.observe()
    code = [a for a in page["actions"] if a.get("group")]
    assert len(code) == 1 and len(code[0]["group"]) == 6 and code[0]["label"] == "One-time code"
    assert rules.secret_field(code[0]) and code[0]["submit"] == "Verify"
    fills = [a for a in page["actions"] if a["kind"] == "fill" and not a.get("group")]
    assert len(fills) == 12  # wide unnamed fields, named boxes, and boxes in a column stay fields of their own
    b.act("TYPE_TEXT", code[0], page, text="482913")
    assert b.evaluate("[...document.querySelectorAll('#verify input')].map(i => i.value).join('')") == "482913"
    assert [a for a in b.observe()["actions"] if a.get("group")][0]["value"] == "482913"  # still one field, typed



def test_a_first_code_box_that_takes_a_pasted_code_is_one_of_the_code_boxes(browser_at):
    # MyUPI (NPCI): six boxes in a row, the first holding up to 6 characters so a pasted code lands, the rest 1 each;
    # beside them a 1px screen-reader input catches the phone's autofill
    b = browser_at("/next.html")
    b.evaluate("document.body.insertAdjacentHTML('afterbegin', '<div><input autocomplete=\"one-time-code\" "
               "maxlength=\"6\" style=\"position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0)\">"
               "<div>' + [6, 1, 1, 1, 1, 1].map((m, n) => `<input id=\"otp-${n}\" maxlength=\"${m}\" "
               "inputmode=\"numeric\" style=\"width:48px;height:48px\">`).join('') + '</div></div>')")
    page = b.observe()
    code = [a for a in page["actions"] if a.get("group")]
    assert len(code) == 1 and len(code[0]["group"]) == 6
    b.act("TYPE_TEXT", code[0], page, text="482913")
    assert b.evaluate("[...document.querySelectorAll('[id^=otp-]')].map(i => i.value).join('')") == "482913"


def test_a_cards_own_action_names_its_card_even_when_one_card_shows(browser_at):
    # MyUPI shows one mandate card at a time, each with Pause and Cancel: a bare "Cancel" was pressed for the wrong one
    card = ("<div><div><div>{payee}</div><div>₹{amount}</div><div>Active on GOOGLE PAY</div></div>"
            "<div><button>Pause</button><button>Cancel</button></div></div>")
    b = browser_at("/next.html")
    b.evaluate(f"document.body.insertAdjacentHTML('afterbegin', '{card.format(payee='AISLE', amount=499)}')")
    labels = [a["label"] for a in b.observe()["actions"] if a["kind"] == "click"]
    assert "Cancel (in “AISLE”)" in labels and "Pause (in “AISLE”)" in labels
    b.evaluate(f"document.body.insertAdjacentHTML('afterbegin', '{card.format(payee='OPENAI LLC', amount=399)}')")
    labels = [a["label"] for a in b.observe()["actions"] if a["kind"] == "click"]
    assert "Cancel (in “AISLE”)" in labels and "Cancel (in “OPENAI LLC”)" in labels


def test_a_qr_to_scan_is_cut_out_and_enlarged_and_a_page_without_one_gives_none(browser_at):
    b = browser_at("/next.html")
    assert b.qr_screenshot() is None
    b.evaluate("document.body.insertAdjacentHTML('beforeend', '<div style=\"height:2000px\"></div><div>"
               "<canvas class=\"intent-qr-code-display\" width=\"200\" height=\"200\"></canvas></div>')")
    width, height = struct.unpack(">II", base64.b64decode(b.qr_screenshot())[16:24])  # PNG header: below the fold
    assert width == height and width >= 400  # scrolled to and doubled: a phone reads it off the chat's picture


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


def test_a_click_within_a_container_lands_on_the_quoted_words(browser_at):
    b = browser_at("/next.html")
    b.evaluate("document.body.insertAdjacentHTML('afterbegin', '<div onclick=\"window.__hit = 1\" "
               "style=\"display:flex;cursor:pointer;width:400px\"><span style=\"width:300px\">Add a delivery "
               "address</span><div onclick=\"window.__hit = 2; event.stopPropagation()\">Add New</div></div>'); 1")
    page = b.observe()
    box = next(a for a in page["actions"] if a["kind"] == "click" and "Add New" in a["label"])
    b.act("CLICK", {**box, "within": "Add New"}, page)
    assert b.evaluate("window.__hit") == 2


def test_a_control_keeps_its_own_name_apart_from_added_context(browser_at):
    b = browser_at("/next.html")
    b.evaluate("document.body.insertAdjacentHTML('afterbegin', '<button aria-label=\"Place order\">Proceed to Pay "
               "120</button><div><p>Buy 1 Get 1 Margherita</p><button>ADD</button></div><div><p>Farmhouse</p>"
               "<button>ADD</button></div>'); 1")
    actions = b.observe()["actions"]
    place = next(a for a in actions if a["label"].startswith("Place order"))
    adds = [a for a in actions if a["label"].startswith("ADD (")]
    assert place["name"] == "Place order" and place["shown"] == "Proceed to Pay 120"
    assert [a["name"] for a in adds] == ["ADD", "ADD"]


def test_an_editable_field_never_shows_its_content_in_its_label(browser_at):
    b = browser_at("/next.html")
    b.evaluate("document.body.insertAdjacentHTML('afterbegin', '<div role=textbox contenteditable=true "
               "aria-label=\"Enter OTP\">482913</div>'); 1")
    labels = [a["label"] for a in b.observe()["actions"] if "Enter OTP" in a["label"]]
    assert labels and not any("482913" in label for label in labels)


def test_a_radio_moved_off_screen_is_offered_through_its_label(browser_at):
    b = browser_at("/next.html")
    b.evaluate("document.body.insertAdjacentHTML('afterbegin', '<label style=\"cursor:pointer\"><input type=radio "
               "id=far style=\"position:absolute;left:-9999px\">Large (serves 4)</label>'); 1")
    page = b.observe()
    option = next(a for a in page["actions"] if a["label"] == "Large (serves 4)")
    b.act("CLICK", option, page)
    assert b.evaluate("far.checked") is True


def test_sibling_hidden_radios_are_not_offered_through_one_shared_wrapper(browser_at):
    b = browser_at("/next.html")
    radio = "<input type=radio name=diet id={0} aria-label=\"{1}\" style=\"opacity:0;width:0;height:0\">"
    b.evaluate("document.body.insertAdjacentHTML('afterbegin', '<div style=\"width:60px;height:20px\">"
               + radio.format("veg", "Veg") + radio.format("nonveg", "Non veg") + "</div>'); 1")
    nodes = [a["node"] for a in b.observe()["actions"] if a["label"] in ("Veg", "Non veg")]
    assert len(nodes) == len(set(nodes))


def test_a_transparent_code_input_over_drawn_boxes_is_offered(browser_at):
    b = browser_at("/next.html")
    b.evaluate("document.body.insertAdjacentHTML('afterbegin', '<p>OTP Verification</p><div style=\"position:relative;"
               "width:240px;height:48px\"><div>_ _ _ _ _ _</div><input id=otp inputmode=numeric maxlength=6 "
               "autocomplete=one-time-code style=\"position:absolute;left:0;top:0;width:240px;height:48px;opacity:0\">"
               "</div>'); 1")
    fills = [a for a in b.observe()["actions"] if a["kind"] == "fill"]
    assert any(b.evaluate(f"window.__jevFast.nodes.get({a['node']}).id") == "otp" for a in fills)

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


def test_style_and_script_words_never_join_a_controls_name(browser_at):
    page = browser_at("/labels.html").observe()
    names = [a["label"] for a in page["actions"] if "One-time purchase" in a["label"]]
    assert names == ["One-time purchase"]  # no CSS, script, or noscript text in it


def test_same_named_buttons_in_different_sections_carry_their_sections_heading(browser_at):
    page = browser_at("/labels.html").observe()
    shows = [a["label"] for a in page["actions"] if a["label"].startswith("Book 07:50 PM")]
    assert len(shows) == 2 and "Drishyam The Conclusion" in shows[0] and "Jolly LLB 3" in shows[1]
    adds = [a["label"] for a in page["actions"] if a["label"].startswith("ADD")]
    assert len(adds) == 2 and "Margherita" in adds[0] and "Farmhouse" in adds[1]
    assert "Margherita" not in adds[1]  # a heading in another card is never borrowed


def test_a_rotating_placeholder_never_makes_a_decision_stale(browser_at):
    b = browser_at("/rotating.html")
    page = b.observe()
    search = find(page, "Search")
    time.sleep(0.5)  # the placeholder shows other words, and fewer or more of them
    assert b.fresh(page) and b.fresh(page, search)
    b.act("CLICK", search, page)
    assert b.evaluate("location.hash") == "#search"
    page = b.observe()
    b.evaluate("document.getElementById('total').textContent = 'Cart ₹110'")
    assert not b.fresh(page)
    b.evaluate("document.getElementById('words').insertAdjacentHTML('beforeend', '<div>Sold out</div>')")
    assert not b.fresh(page, find(page, "Search"))


def test_a_price_drawn_hidden_from_screen_readers_is_read_from_its_screen_reader_copy(browser_at):
    text = browser_at("/sr-price.html").observe()["text"]
    assert "₹799.00" in text.splitlines() and "Inclusive of all taxes" in text
    assert text.count("Masala Dosa") == 1  # a visible screen-reader copy is read once, its drawn twin not again


def test_time_slots_in_rows_of_several_sections_carry_their_rows_title(browser_at):
    page = browser_at("/showtimes.html").observe()
    labels = [a["label"] for a in page["actions"] if a["kind"] == "click" and "PM" in a["label"] or "AM" in a["label"]]
    assert any(label.startswith("07:50 PM") and "Drishyam The Conclusion" in label for label in labels)
    assert any(label.startswith("10:30 PM") and "Avengers Endgame" in label for label in labels)
    assert not any("ENG IMAX" in label and "Drishyam" in label for label in labels)  # each row's own title


def test_same_named_pictures_read_as_pictures_not_as_options(browser_at):
    labels = [a["label"] for a in browser_at("/gallery.html").observe()["actions"] if a["kind"] == "click"]
    assert "Diet Coke Can (picture 1 of 3)" in labels and "Diet Coke Can (picture 3 of 3)" in labels
    assert "Add (1 of 2)" in labels  # buttons with words keep the plain count


def test_a_text_field_is_never_named_by_an_alt_attribute(browser_at):
    labels = [a["label"] for a in browser_at("/trip.html").observe()["actions"]]
    assert "Enter pickup" in labels and "Enter drop" in labels  # live rapido: alt="Drop" sat on the pickup box
    assert "Search rides" in labels  # an image button is named by its alt


def test_a_submit_or_button_input_with_no_value_is_named_by_its_alt_so_the_commit_rule_sees_it(browser_at):
    page = browser_at("/submit-alt.html").observe()
    clicks = {a["label"]: a for a in page["actions"] if a["kind"] == "click"}
    assert "Place order" in clicks and "Pay now" in clicks  # nameless before: the commit rule never saw them
    assert "Clear" in clicks  # a value still comes first
    assert rules.commit_label("CLICK", clicks["Place order"], page) == "Place order"
    assert "Add a note" in [a["label"] for a in page["actions"] if a["kind"] == "fill"]  # a text field: never its alt


def test_a_control_that_shows_only_a_picture_is_marked_as_one(browser_at):
    clicks = {a["label"]: a for a in browser_at("/gallery.html").observe()["actions"] if a["kind"] == "click"}
    assert clicks["Black earbuds in an open case"]["picture"] is True  # live amazon: gallery photos are radios
    assert clicks["Three cases in white, teal and black"]["picture"] is True
    assert "picture" not in clicks["Black ₹799.00"] and "picture" not in clicks["Add (1 of 2)"]


def test_a_name_written_as_markup_reads_as_its_words(browser_at):
    labels = [a["label"] for a in browser_at("/markup.html").observe()["actions"] if a["kind"] == "click"]
    assert any(label.startswith("Buy for ₹799 + cashback") for label in labels)  # live amazon: “<span class=…”
    assert any(label.startswith("Size < 10") for label in labels)


def test_a_page_laid_out_for_a_common_laptop_width_shows_its_right_column(browser_at):
    page = browser_at("/wide.html").observe()  # live invoice: Amazon's Invoice link sat at x=1157, off a 1120 view
    assert find(page, "Invoice")
