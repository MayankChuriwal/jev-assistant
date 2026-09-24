"""The web channel speaks WhatsApp Cloud API shapes."""

import threading
import time

import pytest

from assistant.channel_web import WebChannel


def webhook(*messages):
    value = {"messaging_product": "whatsapp", "contacts": [{"wa_id": "local"}], "messages": list(messages)}
    return {"object": "whatsapp_business_account",
            "entry": [{"id": "local", "changes": [{"field": "messages", "value": value}]}]}


def test_webhook_messages_flatten_to_events():
    body = webhook(
        {"from": "local", "id": "wamid.1", "type": "text", "text": {"body": "Order Diet Coke"}},
        {"from": "local", "id": "wamid.2", "type": "interactive", "context": {"id": "wamid.out3"},
         "interactive": {"type": "button_reply", "button_reply": {"id": "q1:yes", "title": "Yes, Pay ₹79"}}},
        {"from": "local", "id": "wamid.3", "type": "interactive",
         "interactive": {"type": "list_reply", "list_reply": {"id": "q2:o4", "title": "Office"}}},
        {"from": "local", "id": "wamid.4", "type": "document", "document": {"id": "m1", "filename": "bill.pdf"}},
        {"from": "local", "id": "wamid.5", "type": "image", "image": {"id": "m2", "caption": "like this"}},
    )
    events = WebChannel.events(body)
    assert events[0] == {"from": "local", "id": "wamid.1", "type": "text", "text": "Order Diet Coke", "context": None,
                         "question_id": None, "secret_reply": False}
    assert events[1]["reply_id"] == "q1:yes" and events[1]["context"] == "wamid.out3"
    assert events[2]["reply_id"] == "q2:o4" and events[2]["text"] == "Office"
    assert events[3]["media_id"] == "m1" and events[3]["filename"] == "bill.pdf"
    assert events[4]["media_id"] == "m2" and events[4]["text"] == "like this"


@pytest.mark.parametrize("body", [{}, {"entry": "x"}, webhook({"id": "w", "type": "text"}), webhook({"from": "l"}),
                                  webhook({"from": "l", "id": "w", "type": "text", "text": {}})])
def test_anything_else_is_refused(body):
    with pytest.raises(ValueError):
        WebChannel.events(body)


def test_sent_messages_come_back_in_order_and_long_polls_wake_up(tmp_path):
    channel = WebChannel(tmp_path)
    first = channel.send("local", {"type": "text", "text": {"body": "hi"}})
    assert first == "wamid.out1"
    messages, typing = channel.since(0)
    assert messages == [{"messaging_product": "whatsapp", "to": "local", "type": "text", "text": {"body": "hi"},
                         "x_seq": 1, "x_id": "wamid.out1"}] and typing is False
    threading.Timer(0.1, lambda: channel.send("local", {"type": "text", "text": {"body": "later"}})).start()
    started = time.monotonic()
    messages, _ = channel.since(1, wait=5)
    assert [m["text"]["body"] for m in messages] == ["later"] and time.monotonic() - started < 2


def test_media_is_stored_and_registered_privately(tmp_path):
    channel = WebChannel(tmp_path / "media")
    stored = channel.store(b"\x89PNG", "image/png")
    path, mime, name = channel.media(stored)
    assert path.read_bytes() == b"\x89PNG" and mime == "image/png" and name is None
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    invoice = tmp_path / "invoice.pdf"
    invoice.write_bytes(b"%PDF")
    registered = channel.register(invoice)
    assert channel.media(registered) == (invoice, "application/pdf", "invoice.pdf")
    with pytest.raises(KeyError):
        channel.media("nope")


def test_files_keep_their_names_for_the_sites_they_go_to(tmp_path):
    channel = WebChannel(tmp_path / "media")
    statement = channel.media(channel.store(b"%PDF", "application/pdf", "../statement ₹.pdf"))[0]
    assert statement.name == "statement ₹.pdf" and statement.read_bytes() == b"%PDF"
    assert oct(statement.parent.stat().st_mode & 0o777) == "0o700"
    photo = channel.media(channel.store(b"\x89PNG", "image/png"))[0]
    assert photo.suffix == ".png" and photo.parent != statement.parent
