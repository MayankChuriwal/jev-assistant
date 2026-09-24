"""Agent messages become WhatsApp payloads within its limits; replies map back."""

from assistant import messages


def options(n):
    return [{"id": f"o{i}", "label": f"Option {i}", "description": f"Detail {i}"} for i in range(1, n + 1)]


def test_plain_text_is_split_at_the_limit():
    payloads = messages.render({"kind": "info", "text": "a" * 5000})
    assert [len(p["text"]["body"]) for p in payloads] == [4096, 904]


def test_three_options_become_reply_buttons_with_question_ids():
    [payload] = messages.render({"kind": "confirm", "text": "Pay ₹79?", "options": [
        {"id": "yes", "label": "Yes, Pay ₹79"}, {"id": "no", "label": "No"}]}, question_id=7)
    assert payload["type"] == "interactive" and payload["interactive"]["type"] == "button"
    assert payload["interactive"]["body"] == {"text": "Pay ₹79?"}
    assert payload["interactive"]["action"]["buttons"] == [
        {"type": "reply", "reply": {"id": "q7:yes", "title": "Yes, Pay ₹79"}},
        {"type": "reply", "reply": {"id": "q7:no", "title": "No"}}]
    assert payload["x_question"] == 7


def test_up_to_ten_options_become_a_list_and_more_become_numbered_text():
    [listed] = messages.render({"kind": "choice", "text": "Which size?", "options": options(7)}, question_id=2)
    rows = listed["interactive"]["action"]["sections"][0]["rows"]
    assert listed["interactive"]["type"] == "list" and len(rows) == 7
    assert rows[0] == {"id": "q2:o1", "title": "Option 1", "description": "Detail 1"}
    [numbered] = messages.render({"kind": "choice", "text": "Which city?", "options": options(12)}, question_id=3)
    assert numbered["type"] == "text" and "12. Option 12 — Detail 12" in numbered["text"]["body"]
    assert "Reply with a number" in numbered["text"]["body"]


def test_long_bodies_go_first_as_text_and_titles_are_cut():
    long_text = "x" * 1500
    payloads = messages.render({"kind": "choice", "text": long_text, "options": [
        {"id": "a", "label": "A" * 30}, {"id": "b", "label": "B"}]}, question_id=1)
    assert payloads[0]["type"] == "text" and payloads[0]["text"]["body"].startswith(long_text)
    assert payloads[1]["interactive"]["body"]["text"] == "Choose one:"
    assert payloads[1]["interactive"]["action"]["buttons"][0]["reply"]["title"] == "A" * 20


def test_secrets_files_images_and_titles():
    [secret] = messages.render({"kind": "secret", "text": "Enter the OTP.", "expects": {"length": 6}}, question_id=4)
    assert secret["x_secret"] is True and secret["x_expects"] == {"length": 6} and secret["x_question"] == 4
    [upload] = messages.render({"kind": "file", "text": "Send the statement."}, question_id=5)
    assert upload["x_expects"] == {"file": True}
    shots = messages.render({"kind": "image", "text": "Type the captcha."}, question_id=6, image_id="m1")
    assert shots[0]["type"] == "image" and shots[0]["image"] == {"id": "m1"}
    assert shots[1]["text"]["body"] == "Type the captcha."
    [titled] = messages.render({"kind": "info", "text": "Ordered."}, title="Swiggy · Diet Coke")
    assert titled["text"]["body"] == "*Swiggy · Diet Coke*\nOrdered."


def test_replies_map_back_to_the_question_they_answer():
    opts = options(12)
    assert messages.reply_for(3, opts, {"type": "interactive", "reply_id": "q3:o2"}) == {"option": "o2"}
    assert messages.reply_for(3, opts, {"type": "interactive", "reply_id": "q2:o2"}) is None
    assert messages.reply_for(3, opts, {"type": "text", "text": " 11 "}) == {"option": "o11"}
    assert messages.reply_for(3, opts, {"type": "text", "text": "13"}) == {"text": "13"}
    assert messages.reply_for(3, [], {"type": "text", "text": "Home"}) == {"text": "Home"}
    assert messages.reply_for(3, [], {"type": "document", "media_id": "m9", "text": ""}) == {"media_id": "m9"}


def test_long_review_keeps_every_charge_before_confirmation():
    lines = [f"Observed unfamiliar charge {n}: ₹1" for n in range(130)]
    payloads = messages.render({"kind": "confirm", "text": "\n".join(lines),
                               "options": [{"id": "yes", "label": "Confirm"}, {"id": "no", "label": "Change"}]},
                              question_id="t1-q1")
    bodies = [p["text"]["body"] if p["type"] == "text" else p["interactive"]["body"]["text"] for p in payloads]
    assert all(line in "".join(bodies) for line in lines)
    assert payloads[-1]["type"] == "interactive"
    assert payloads[-1]["x_question"] == "t1-q1"


def test_button_summaries_preserve_material_differences_as_plain_text():
    details = "<b>6 seats</b>, ₹180, arrives in 4 minutes; tolls excluded"
    payloads = messages.render({"kind": "choice", "text": "Which ride?", "options": [
        {"id": "a", "label": "Standard premium XL with luggage", "description": details},
        {"id": "b", "label": "Standard", "description": "4 seats, ₹95"}]}, question_id="t1-q1")
    assert details in payloads[-1]["interactive"]["body"]["text"]
    assert "Standard premium XL with luggage" in payloads[-1]["interactive"]["body"]["text"]


def test_forged_option_with_real_question_id_is_rejected():
    assert messages.reply_for('q', [{'id': 'no', 'label': 'No'}], {'reply_id': 'qq:yes'}) is None
