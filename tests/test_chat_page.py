"""The chat page in real Chrome, driven the way the agent drives any page. Run: uv run pytest -m browser"""

import base64
import threading
from types import SimpleNamespace

import pytest
from helpers import find, observe_until, until

from assistant import server
from assistant.channel_web import WebChannel

pytestmark = pytest.mark.browser

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")
EVIL = '<img src=x onerror="window.pwned=1"><b>bold</b>'


class RecordingCore:
    def __init__(self):
        self.events, self.tasks, self.lock = [], {}, threading.RLock()
        self.open, self.refuse = None, False

    def receive_webhook(self, body):
        if self.refuse:
            raise ValueError("refused")
        self.events += WebChannel.events(body)

    def showing(self):
        return self.open


@pytest.fixture
def chat(chrome, tmp_path):
    from assistant.browser import Browser

    channel, core = WebChannel(tmp_path / "media"), RecordingCore()
    httpd = server.ChatServer(core, channel, port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    browser = Browser(f"http://127.0.0.1:{httpd.server_port}/")
    yield SimpleNamespace(browser=browser, channel=channel, core=core, httpd=httpd)
    browser.close()
    httpd.shutdown()
    httpd.server_close()


def js(chat, expression):
    return chat.browser.evaluate(expression)


def test_buttons_answer_with_the_question_they_belong_to(chat):
    sent = chat.channel.send("local", {"type": "interactive", "x_question": 1, "interactive": {
        "type": "button", "body": {"text": "Deliver to which address?"}, "action": {"buttons": [
            {"type": "reply", "reply": {"id": "q1:o1", "title": "Home"}},
            {"type": "reply", "reply": {"id": "q1:o2", "title": "Office"}}]}}})
    page = observe_until(chat.browser, lambda p: any(a["label"] == "Home" for a in p["actions"]))
    chat.browser.act("CLICK", find(page, "Home"), page)
    assert until(lambda: chat.core.events)
    event = chat.core.events[0]
    assert (event["type"], event["reply_id"], event["context"]) == ("interactive", "q1:o1", sent)
    assert until(lambda: js(chat, "[...document.querySelectorAll('.choice')].every(b => b.disabled)"))


def test_lists_show_every_row_and_a_tap_sends_a_list_reply(chat):
    rows = [{"id": f"q2:o{i}", "title": f"Size {i}", "description": f"{i * 100} ml"} for i in range(1, 6)]
    chat.channel.send("local", {"type": "interactive", "x_question": 2, "interactive": {
        "type": "list", "body": {"text": "Which size?"},
        "action": {"button": "Choose", "sections": [{"title": "Options", "rows": rows}]}}})
    page = observe_until(chat.browser, lambda p: "Size 5" in p["text"] and "500 ml" in p["text"])
    chat.browser.act("CLICK", find(page, "Size 3"), page)
    assert until(lambda: chat.core.events) and chat.core.events[0]["reply_id"] == "q2:o3"


def test_secret_questions_hide_what_is_typed(chat):
    chat.core.open = {"id": 3, "secret": True}
    chat.channel.send("local", {"type": "text", "x_question": 3, "x_secret": True, "x_expects": {"length": 6},
                                "text": {"body": "Enter the OTP."}})
    page = observe_until(chat.browser, lambda p: "Enter the OTP." in p["text"])
    assert js(chat, "document.getElementById('text').type") == "password"
    chat.browser.act("TYPE_TEXT", find(page, "Message", "fill"), page, text="482913")
    page = chat.browser.observe()
    chat.browser.act("PRESS_KEY", find(page, "Message", "fill"), page, key="Enter")
    assert until(lambda: chat.core.events) and chat.core.events[0]["text"] == "482913"
    chat.core.open = None
    page = observe_until(chat.browser, lambda p: "••••" in p["text"])
    assert "482913" not in page["text"] and js(chat, "document.getElementById('text').type") == "text"


def test_plain_text_goes_out_as_a_webhook_and_shows_on_the_right(chat):
    page = chat.browser.observe()
    chat.browser.act("TYPE_TEXT", find(page, "Message", "fill"), page, text="Order Diet Coke")
    page = chat.browser.observe()
    chat.browser.act("CLICK", find(page, "Send"), page)
    assert until(lambda: chat.core.events) and chat.core.events[0]["text"] == "Order Diet Coke"
    assert js(chat, "document.querySelector('.bubble.out .text').textContent") == "Order Diet Coke"


def test_message_text_is_never_html(chat):
    chat.channel.send("local", {"type": "text", "text": {"body": f"*{EVIL[:20]}*\n{EVIL}"}})
    rows = [{"id": "q4:o1", "title": EVIL[:24], "description": EVIL[:72]}]
    chat.channel.send("local", {"type": "interactive", "x_question": 4, "interactive": {
        "type": "list", "body": {"text": EVIL},
        "action": {"button": "Choose", "sections": [{"title": "Options", "rows": rows}]}}})
    chat.channel.send("local", {"type": "document", "document": {"id": "nope", "filename": EVIL}})
    assert until(lambda: js(chat, "document.querySelectorAll('.bubble').length") == 3)
    assert js(chat, "document.querySelectorAll('#chat img, #chat b').length") == 0
    assert js(chat, "window.pwned === undefined")
    assert js(chat, "document.querySelector('.bubble .text').textContent") == EVIL


def test_whatsapp_bold_shows_as_bold_and_stays_text(chat):
    chat.channel.send("local", {"type": "text", "text": {"body": f"You have *3* AutoPays: **AISLE** ₹499, *{EVIL}*"}})
    assert until(lambda: js(chat, "document.querySelectorAll('.bubble').length") == 1)
    strong = "[...document.querySelectorAll('.bubble .text strong')].map(s => s.textContent)"
    assert js(chat, strong) == ["3", "AISLE", EVIL]
    assert js(chat, "document.querySelector('.bubble .text').textContent") == f"You have 3 AutoPays: AISLE ₹499, {EVIL}"
    assert js(chat, "document.querySelectorAll('#chat img, #chat b').length") == 0 and js(chat, "window.pwned === undefined")


def test_images_documents_uploads_and_typing(chat, tmp_path):
    invoice = tmp_path / "invoice.pdf"
    invoice.write_bytes(b"%PDF-1.4 invoice")
    chat.channel.send("local", {"type": "image", "image": {"id": chat.channel.store(PNG, "image/png")}})
    chat.channel.send("local", {"type": "document", "document": {"id": chat.channel.register(invoice),
                                                                 "filename": "invoice.pdf"}})
    chat.channel.set_typing(True)
    assert until(lambda: js(chat, "document.getElementById('status').textContent") == "typing…")
    assert until(lambda: js(chat, "document.querySelector('img.shot')?.naturalWidth") == 1)
    link = js(chat, "document.querySelector('a.doc').href")
    assert "/media/" in link and "?t=" in link and js(chat, "document.querySelector('a.doc').textContent").endswith(
        "invoice.pdf")
    statement = tmp_path / "statement.pdf"
    statement.write_bytes(b"%PDF-1.4 statement")
    page = chat.browser.observe()
    chat.browser.act("UPLOAD", find(page, "Attach a file", "upload"), page, files=[str(statement)])
    assert until(lambda: chat.core.events)
    event = chat.core.events[0]
    assert event["type"] == "document" and event["filename"] == "statement.pdf"
    assert chat.channel.media(event["media_id"])[0].read_bytes() == b"%PDF-1.4 statement"


def test_secret_mode_follows_the_core_not_old_messages(chat):
    chat.channel.send("local", {"type": "text", "x_question": 5, "x_secret": True, "text": {"body": "Enter the OTP."}})
    assert until(lambda: "Enter the OTP." in js(chat, "document.body.innerText"))
    assert js(chat, "document.getElementById('text').type") == "text"  # answered before this page loaded
    chat.core.open = {"id": 6, "secret": True}
    chat.channel.set_typing(True)  # anything new wakes the page's long poll
    assert until(lambda: js(chat, "document.getElementById('text').type") == "password")


def test_a_secret_that_was_not_delivered_stays_hidden(chat):
    chat.core.open, chat.core.refuse = {"id": 8, "secret": True}, True
    chat.channel.send("local", {"type": "text", "x_question": 8, "x_secret": True,
                                "text": {"body": "Enter your password."}})
    page = observe_until(chat.browser, lambda p: "Enter your password." in p["text"])
    assert until(lambda: js(chat, "document.getElementById('text').type") == "password")
    chat.browser.act("TYPE_TEXT", find(page, "Message", "fill"), page, text="hunter2")
    page = chat.browser.observe()
    chat.browser.act("PRESS_KEY", find(page, "Message", "fill"), page, key="Enter")
    assert until(lambda: js(chat, "document.querySelector('.bubble.out.failed') !== null"))
    assert js(chat, "document.getElementById('text').type") == "password"
    assert "hunter2" not in js(chat, "document.body.innerText")


def test_a_restarted_server_asks_for_a_reload(chat):
    assert until(lambda: js(chat, "document.getElementById('status').textContent") == "online")
    chat.httpd.token = "a-new-launch"  # what a restart does to the page's token
    chat.channel.set_typing(True)  # wakes the page's long poll; the next poll meets the new token
    assert until(lambda: js(chat, "document.getElementById('status').textContent") ==
                 "jev restarted: reload this page", timeout=5)
