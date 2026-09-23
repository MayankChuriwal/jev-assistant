# Plan 3 — Chat Channel, Task Runner, and Parallel Tasks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local chat app that looks and behaves like WhatsApp: the user types a goal, the agent works in the hidden Chrome, questions arrive one at a time as buttons, lists, or secret prompts, several tasks run in parallel, and mid-task messages are understood as answers, amendments, new tasks, cancellations, or status questions.

**Architecture:** `channel_web.py` speaks the WhatsApp Cloud API shapes (webhook in, send-message out, media ids) over local HTTP, so a WhatsApp channel can replace it one-for-one. `messages.py` turns agent messages into those payloads within WhatsApp's limits and turns replies back into agent replies. `core.py` owns the conversation: it classifies each free-text message with Jev, keeps one question open at a time, and runs every task's `Agent` in its own thread with limits per site and overall. `server.py` serves the chat page and the channel's endpoints on loopback only.

**Tech Stack:** Python ≥ 3.12 standard library HTTP server and threads; browser-harness, httpx; vanilla HTML/CSS/JS for the page; pytest, ruff, node.

**Spec:** `docs/superpowers/specs/2026-09-23-jev-assistant-design.md` (§11, §12; §13.3 downloads to chat)

**Builds on:** Plans 1–2 on `master` (commit `99a1a59`). Work lands on `master` (the user's standing preference); each task is its own commit.

## Global Constraints

- Everything in Plans 1–2's Global Constraints still holds.
- No new dependencies. The server binds `127.0.0.1:8766` only and checks Host, Origin, and a per-launch token on every API and media route (header `X-Jev-Token`, or `?t=` for `<img>`/download links).
- WhatsApp limits enforced for every outbound message: text ≤ 4,096 characters (split beyond), interactive body ≤ 1,024, at most 3 reply buttons with titles ≤ 20, lists of at most 10 rows with titles ≤ 24 and descriptions ≤ 72, more options as a numbered text list answered by number. Button and row ids are `q<question id>:<option id>`.
- Uploads ≤ 20 MB; downloads delivered to chat ≤ 100 MB; webhook bodies ≤ 64 KB.
- Tasks: at most 3 running; at most 1 per site (host without a leading `www.` or `m.`); extra tasks wait with a message.
- One question open in chat at a time; priority: `secret` and `confirm` first, then oldest. A question unanswered for 15 minutes pauses its task; after 24 hours the task is cancelled.
- Free text is classified by Jev (intent + task) unless it answers an open secret question, which is never sent to any model. Ambiguous when the top intent probability is below 0.6 or within 0.2 of the second.
- The chat page renders all message text as text (never HTML).

## Review Focus

1. A secret answer typed while two tasks are waiting reaches the open secret question only and never the classifier — Task 5 `test_secret_answers_never_reach_the_classifier`.
2. A tap on a button of an already-answered question (the user scrolled up) is ignored and does not answer the current question — Task 5 `test_taps_on_answered_questions_are_ignored`.
3. A download started by task A after task B's tab set the download folder last still reaches task A — Task 4 `test_parallel_downloads_reach_the_task_that_started_them`.
4. A malformed or oversized webhook body is refused with 400 and the server keeps serving — Task 6 `test_bad_bodies_are_refused_and_the_server_survives`.
5. Question text containing HTML (`<img src=x onerror=…>`) shows as text and never runs — Task 7 `test_message_text_is_never_html`.

## Deviations from the spec (deliberate)

- "Remember this" becomes a follow-up button sent after a choice is answered (WhatsApp allows only 3 buttons, so it cannot sit next to the options); tapping it saves the preference.
- Chat prefixes a question or message with the task title only when more than one task exists.
- The page long-polls `GET /outbox` (up to 25 s) instead of polling every 500 ms; same data, less traffic.
- "typing…" shows while a task is working, not while every task is waiting for the user (as on WhatsApp).
- The debug view is JSON at `/api/debug` (recent steps and a masked screenshot per task), not a separate HTML panel.
- Reloading the chat page shows the assistant's messages again; the user's own bubbles live only in that page session.

## File map

| File | Responsibility |
|---|---|
| `assistant/channel_web.py` (new) | `WebChannel`: outbox with sequence numbers and long-poll, typing flag, media store, webhook parsing |
| `assistant/messages.py` (new) | `render(message, …) -> payloads` within WhatsApp limits; `reply_for(question, event) -> agent reply` |
| `assistant/core.py` (new) | `Core`: tasks, threads, limits, question queue, pause/cancel timers, intent routing, downloads to chat, remember offers |
| `assistant/model.py`, `assistant/questions.py`, `assistant/jobs.py` | `classify` (Jev intent + task), `INTENTS`, `jobs.chat` for STATUS/OTHER replies |
| `assistant/browser.py` | Downloads routed to the task whose tab started them; download routing re-issued across tasks |
| `assistant/agent.py` | `title=` parameter |
| `assistant/server.py` | `ChatServer` (loopback HTTP, token/Host/Origin checks, static page, channel endpoints, debug view); `jev` with no subcommand serves the chat |
| `assistant/static/index.html`, `app.js`, `style.css` (new) | WhatsApp-like chat page |
| `tests/test_channel.py`, `tests/test_messages.py`, `tests/test_classify.py`, `tests/test_core.py`, `tests/test_chat_server.py`, `tests/test_chat_page.py`, `tests/test_chat_e2e.py` | Tests per area |

---

### Task 1: WhatsApp-shaped web channel

**Files:**
- Create: `assistant/channel_web.py`, `tests/test_channel.py`

**Interfaces:**
- Produces: `WebChannel(media_dir)` with `send(user, payload) -> str` (message id `wamid.out<n>`), `since(after=0, wait=0.0) -> (messages, typing)`, `typing: bool`, `store(data: bytes, mime: str, filename=None) -> media_id`, `register(path, filename=None) -> media_id`, `media(media_id) -> (path, mime, filename)` (KeyError when unknown); static `events(body) -> list[event]` where `event = {"from", "id", "type", "text", "reply_id"?, "media_id"?, "filename"?, "context"?}` and it raises `ValueError` for bodies that are not WhatsApp webhook shaped.

- [ ] **Step 1: Write the failing tests** — `tests/test_channel.py`:

```python
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
    assert events[0] == {"from": "local", "id": "wamid.1", "type": "text", "text": "Order Diet Coke", "context": None}
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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_channel.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'assistant.channel_web'`.

- [ ] **Step 3: Implement** — `assistant/channel_web.py`:

```python
"""The web channel: WhatsApp Cloud API shapes over local HTTP, so a WhatsApp channel can replace it one-for-one.
Inbound is a webhook body; outbound is a send-message payload; files travel as media ids."""

import itertools
import mimetypes
import secrets
import threading
from pathlib import Path


class WebChannel:
    def __init__(self, media_dir):
        self.media_dir = Path(media_dir)
        self.outbox, self.files, self.typing = [], {}, False
        self.changed = threading.Condition()
        self.numbers = itertools.count(1)

    def send(self, user, payload):
        """Queue one Graph API send-message payload for `user`; returns its message id."""
        with self.changed:
            seq = next(self.numbers)
            message = {"messaging_product": "whatsapp", "to": user, **payload, "x_seq": seq, "x_id": f"wamid.out{seq}"}
            self.outbox.append(message)
            self.changed.notify_all()
            return message["x_id"]

    def set_typing(self, typing):
        with self.changed:
            if typing != self.typing:
                self.typing = typing
                self.changed.notify_all()

    def since(self, after=0, wait=0.0):
        """Messages after sequence number `after`, waiting up to `wait` seconds for the first one."""
        with self.changed:
            if wait and not any(m["x_seq"] > after for m in self.outbox):
                self.changed.wait(wait)
            return [m for m in self.outbox if m["x_seq"] > after], self.typing

    def store(self, data, mime, filename=None):
        media_id = secrets.token_hex(8)
        self.media_dir.mkdir(parents=True, exist_ok=True)
        path = self.media_dir / (media_id + (mimetypes.guess_extension(mime) or ""))
        path.write_bytes(data)
        path.chmod(0o600)
        self.files[media_id] = (path, mime, filename)
        return media_id

    def register(self, path, filename=None):
        path = Path(path)
        media_id = secrets.token_hex(8)
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.files[media_id] = (path, mime, filename or path.name)
        return media_id

    def media(self, media_id):
        return self.files[media_id]

    @staticmethod
    def events(body):
        """Messages from a WhatsApp webhook body, flattened for the core."""
        try:
            messages = [m for entry in body["entry"] for change in entry["changes"]
                        for m in change["value"].get("messages", [])]
            events = []
            for m in messages:
                event = {"from": str(m["from"]), "id": str(m["id"]), "type": m["type"],
                         "context": (m.get("context") or {}).get("id")}
                if m["type"] == "text":
                    event["text"] = str(m["text"]["body"])
                elif m["type"] == "interactive":
                    reply = m["interactive"].get("button_reply") or m["interactive"]["list_reply"]
                    event.update(reply_id=str(reply["id"]), text=str(reply.get("title", "")))
                elif m["type"] in {"image", "document"}:
                    media = m[m["type"]]
                    event.update(media_id=str(media["id"]), filename=media.get("filename"),
                                 text=str(media.get("caption", "")))
                else:
                    raise ValueError(m["type"])
                events.append(event)
        except (KeyError, TypeError, AttributeError, ValueError):
            raise ValueError("Not a WhatsApp webhook body.") from None
        return events
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -q && uv run ruff check .`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: WhatsApp-shaped web channel with outbox, long-poll, media, and webhook parsing"
```

---

### Task 2: Messages within WhatsApp limits, and replies back

**Files:**
- Create: `assistant/messages.py`, `tests/test_messages.py`

**Interfaces:**
- Consumes: agent message shapes from Plan 2 (`kind`, `text`, `options`, `expects`, `image`).
- Produces: `render(message, question_id=None, image_id=None, title=None) -> list[payload]`; `reply_for(question_id, options, event) -> dict | None` (`{"option"}`, `{"text"}`, `{"file"}` placeholder `{"media_id"}`, or `None` when the event is a tap on another question); constants `TEXT_LIMIT=4096`, `BODY_LIMIT=1024`, `BUTTONS=3`, `BUTTON_TITLE=20`, `ROWS=10`, `ROW_TITLE=24`, `ROW_DESCRIPTION=72`.

- [ ] **Step 1: Write the failing tests** — `tests/test_messages.py`:

```python
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
    assert payloads[0]["type"] == "text" and payloads[0]["text"]["body"] == long_text
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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_messages.py -q`
Expected: FAIL — `ImportError: cannot import name 'messages'`.

- [ ] **Step 3: Implement** — `assistant/messages.py`:

```python
"""Agent messages to WhatsApp Cloud API payloads within its limits, and replies back to agent replies."""

TEXT_LIMIT, BODY_LIMIT = 4096, 1024
BUTTONS, BUTTON_TITLE = 3, 20
ROWS, ROW_TITLE, ROW_DESCRIPTION = 10, 24, 72


def text_payloads(text):
    return [{"type": "text", "text": {"body": text[i:i + TEXT_LIMIT]}} for i in range(0, len(text) or 1, TEXT_LIMIT)]


def render(message, question_id=None, image_id=None, title=None):
    """Payloads for one agent message. Options become reply buttons (≤ 3), a list (≤ 10), or numbered text."""
    text = f"*{title}*\n{message['text']}" if title else message["text"]
    options = message.get("options") or []
    payloads = [{"type": "image", "image": {"id": image_id}}] if image_id else []
    if options and len(options) <= ROWS:
        body = text
        if len(text) > BODY_LIMIT:
            payloads += text_payloads(text)
            body = "Choose one:"
        if len(options) <= BUTTONS:
            action = {"buttons": [{"type": "reply", "reply": {"id": f"q{question_id}:{o['id']}",
                                                             "title": o["label"][:BUTTON_TITLE]}} for o in options]}
            kind = "button"
        else:
            rows = [{"id": f"q{question_id}:{o['id']}", "title": o["label"][:ROW_TITLE],
                     "description": (o.get("description") or "")[:ROW_DESCRIPTION]} for o in options]
            action = {"button": "Choose", "sections": [{"title": "Options", "rows": rows}]}
            kind = "list"
        interactive = {"type": kind, "body": {"text": body}, "action": action}
        payloads.append({"type": "interactive", "interactive": interactive})
    elif options:
        lines = [f"{n}. {o['label']}" + (f" — {o['description']}" if o.get("description") else "")
                 for n, o in enumerate(options, 1)]
        payloads += text_payloads(text + "\n\n" + "\n".join(lines) + "\n\nReply with a number.")
    else:
        payloads += text_payloads(text)
    if question_id is not None:
        for payload in payloads:
            payload["x_question"] = question_id
        if message["kind"] == "secret":
            payloads[-1]["x_secret"] = True
        if message.get("expects"):
            payloads[-1]["x_expects"] = message["expects"]
        if message["kind"] == "file":
            payloads[-1]["x_expects"] = {"file": True}
    return payloads


def reply_for(question_id, options, event):
    """The agent reply an event gives this question, or None when it is a tap on some other question."""
    if event.get("reply_id"):
        prefix, _, option = event["reply_id"].partition(":")
        return {"option": option} if prefix == f"q{question_id}" else None
    if event.get("media_id"):
        return {"media_id": event["media_id"]}
    text = event.get("text", "").strip()
    if options and len(options) > ROWS and text.isdigit() and 1 <= int(text) <= len(options):
        return {"option": options[int(text) - 1]["id"]}
    return {"text": text}
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -q && uv run ruff check .`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: agent messages rendered within WhatsApp limits, replies mapped back"
```

---

### Task 3: Jev reads each chat message; the text model answers status and small talk

**Files:**
- Modify: `assistant/questions.py` (add `INTENTS`, `INTENT_RULE`, `TASK_RULE`, `CHAT`), `assistant/model.py` (add `classify`, `ambiguous`), `assistant/jobs.py` (add `chat`)
- Create: `tests/test_classify.py`

**Interfaces:**
- Consumes: `post_json`, `validate_choice`, `text_json` (Plans 1–2).
- Produces: `classify(message, tasks, chat) -> (intent, task_id | "none", intent_probabilities)` where `tasks = [{"id", "title", "goal", "step", "open_question"?}]` and `chat = [{"from", "text"}]`; `ambiguous(probabilities) -> bool`; `jobs.chat(message, tasks) -> (text, meta)`.

- [ ] **Step 1: Write the failing tests** — `tests/test_classify.py`:

```python
"""Jev classifies chat messages; the text model answers status questions and small talk."""

import json
from unittest.mock import Mock

import pytest

from assistant import jobs, model

TASKS = [{"id": "t1", "title": "Swiggy · Diet Coke", "goal": "Order Diet Coke", "step": "Checkout",
          "open_question": "Deliver to which address?"}]


def choice(ids, selected, top=1.0):
    rest = (1 - top) / max(len(ids) - 1, 1)
    return {"choice": selected, "confidence": top, "probabilities": {i: top if i == selected else rest for i in ids}}


def test_one_request_reads_intent_and_task(monkeypatch):
    calls = []

    def post(_url, _key, body):
        calls.append(body)
        q = body["questions"]
        return {"model": "jev", "answers": {"intent": choice(q["intent"]["criteria"], "AMEND", 0.9),
                                            "task": choice(q["task"]["criteria"], "t1")}}

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    chat = [{"from": "user", "text": str(n)} for n in range(10)]
    intent, task, probabilities = model.classify("make it 2 cans", TASKS, chat)
    assert (intent, task) == ("AMEND", "t1") and probabilities["AMEND"] == 0.9
    body = calls[0]
    assert set(body["questions"]["intent"]["criteria"]) == {"ANSWER", "AMEND", "NEW_TASK", "CANCEL", "STATUS", "OTHER"}
    assert set(body["questions"]["task"]["criteria"]) == {"t1", "none"}
    assert body["state"]["message"] == "make it 2 cans" and len(body["state"]["recent_chat"]) == 6


def test_invalid_answers_are_refused(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", lambda *_: {"model": "jev", "answers": {
        "intent": {"choice": "SHOUT", "confidence": 1.0, "probabilities": {"SHOUT": 1.0}}}})
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.classify("hi", TASKS, [])


@pytest.mark.parametrize("probabilities, expected", [
    ({"ANSWER": 0.9, "AMEND": 0.1}, False),
    ({"ANSWER": 0.55, "AMEND": 0.2}, True),
    ({"ANSWER": 0.62, "AMEND": 0.38}, False),
    ({"ANSWER": 0.6, "AMEND": 0.45}, True),
])
def test_ambiguity_thresholds(probabilities, expected):
    assert model.ambiguous(probabilities) is expected


def test_status_and_small_talk_get_short_replies(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    reply = json.dumps({"text": "Your Diet Coke is at checkout."})
    post = Mock(return_value={"choices": [{"message": {"content": reply}}]})
    monkeypatch.setattr(model, "post_json", post)
    assert jobs.chat("where are we?", TASKS)[0] == "Your Diet Coke is at checkout."
    assert json.loads(post.call_args.args[2]["messages"][1]["content"])["tasks"] == TASKS
    post.return_value = {"choices": [{"message": {"content": "{}"}}]}
    with pytest.raises(ValueError, match="invalid reply"):
        jobs.chat("hi", [])
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_classify.py -q`
Expected: FAIL — `AttributeError: module 'assistant.model' has no attribute 'classify'`.

- [ ] **Step 3: Implement**

Append to `assistant/questions.py`:
```python
INTENTS = {
    "ANSWER": "Answers the question the assistant just asked.",
    "AMEND": "Changes something about an existing task: quantity, item, address, payment, time, or destination.",
    "NEW_TASK": "Asks for a new, separate task.",
    "CANCEL": "Stops or cancels an existing task.",
    "STATUS": "Asks how a task is going or what is happening.",
    "OTHER": "Anything else: thanks, greetings, small talk.",
}

INTENT_RULE = """Read the user's chat message in the context of their tasks and the open question, and choose what
the message does. A short reply to the open question ("Home", "yes", "2") is ANSWER."""

TASK_RULE = """Choose the task the message is about, or "none" for a new request or a message about no task."""

CHAT = """Reply to the user's chat message in one or two short sentences. When they ask about progress, say for each
task in tasks what it is doing now and whether it is waiting for them. Never invent progress.
Return JSON {"text": "..."}."""
```

In `assistant/model.py` extend the questions import with `INTENT_RULE, INTENTS, TASK_RULE` and append:
```python
def classify(message, tasks, chat):
    """Jev's reading of one chat message: (intent, task id or "none", intent probabilities)."""
    targets = {t["id"]: {k: t[k] for k in ("title", "goal", "step", "open_question") if t.get(k)} for t in tasks}
    targets["none"] = "No existing task: a new request, or a message about no task"
    body = {
        "model": os.environ.get("TYPESAFE_MODEL", "jev-latest"),
        "state": {"message": message, "tasks": tasks, "recent_chat": chat[-6:]},
        "questions": {
            "intent": {"type": "choice", "criteria": INTENTS,
                       "instructions": {"message": message, "rules": INTENT_RULE}},
            "task": {"type": "choice", "criteria": targets, "instructions": {"message": message, "rules": TASK_RULE}},
        },
    }
    result = post_json("https://api.typesafe.ai/v1/systemone", os.environ["TYPESAFE_API_KEY"], body)
    intent = validate_choice(result["answers"].get("intent", {}), INTENTS)
    task = validate_choice(result["answers"].get("task", {}), targets)
    return intent["choice"], task["choice"], intent["probabilities"]


def ambiguous(probabilities):
    """Too close to call: the top intent is under 0.6, or within 0.2 of the second."""
    top, second = (sorted(probabilities.values(), reverse=True) + [0.0, 0.0])[:2]
    return top < 0.6 or top - second < 0.2
```

In `assistant/jobs.py` add `CHAT` to the questions import and append:
```python
def chat(message, tasks):
    out, meta = text_json(CHAT, {"message": message, "tasks": tasks})
    text = words(out, "text", 1000)
    if not text:
        raise invalid("reply")
    return text, meta
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -q && uv run ruff check .`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: Jev classifies chat messages; short replies for status and small talk"
```

---

### Task 4: Downloads reach the task that started them

**Files:**
- Modify: `assistant/browser.py` (module `OWNERS`, `DOWNLOADS`; `_adopt`, `_forget`, `_on_event`)
- Modify: `tests/test_events.py` (append)

**Interfaces:**
- Consumes: Plan 1's per-tab `_route_downloads(session)` and event pump.
- Produces: `OWNERS: WeakValueDictionary[target id -> Browser]`; `DOWNLOADS: dict[guid -> Browser]`. A download is credited to the `Browser` owning the frame that started it, whichever session Chrome reports it to, and every open task's tabs set the download behaviour again whenever any tab closes.

- [ ] **Step 1: Write the failing test** — append to `tests/test_events.py`:

```python
def test_parallel_downloads_reach_the_task_that_started_them(browser_at, tmp_path):
    first = browser_at("/events.html", downloads=tmp_path / "first")
    second = browser_at("/events.html", downloads=tmp_path / "second")  # set the download folder last
    page = first.observe()
    first.act("CLICK", find(page, "Download invoice"), page)
    assert [Path(f).parent for f in until(lambda: first.observe()["downloads"])] == [tmp_path / "first"]
    page = second.observe()
    second.act("CLICK", find(page, "Download invoice"), page)
    assert [Path(f).parent for f in until(lambda: second.observe()["downloads"])] == [tmp_path / "second"]
    second.close()  # a closing tab resets Chrome's download behaviour for everyone
    page = first.observe()
    first.act("CLICK", find(page, "Download invoice"), page)
    assert [Path(f).parent for f in until(lambda: first.observe()["downloads"])] == [tmp_path / "first"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest -m browser tests/test_events.py -q -k parallel`
Expected: FAIL — the first task's download is credited to the second task (or never arrives after the second task closes).

- [ ] **Step 3: Implement** — in `assistant/browser.py`:

Add `import weakref` and, after `PUMP = EventPump()`:
```python
OWNERS = weakref.WeakValueDictionary()  # tab target id -> Browser, so a download reaches the task that started it
DOWNLOADS = {}  # download guid -> Browser that started it
```

In `_adopt`, after `self.tabs[target] = session`, add `OWNERS[target] = self`.

In `_forget`, replace the loop that re-routes this browser's remaining tabs with one that covers every task:
```python
        OWNERS.pop(target, None)
        for owner in list(OWNERS.values()):  # the closed tab may have reset downloads for every task
            for remaining in list(owner.tabs.values()):
                try:
                    owner._route_downloads(remaining)
                except (RuntimeError, TimeoutError):
                    pass  # that tab is closing too
```

In `_on_event`, replace the two download branches with:
```python
        elif method == "Browser.downloadWillBegin":
            owner = OWNERS.get(params.get("frameId")) or self  # a main frame's id is its tab's target id
            DOWNLOADS[params["guid"]] = owner
            owner.pending_downloads[params["guid"]] = params["suggestedFilename"]
        elif method == "Browser.downloadProgress" and params.get("state") == "completed":
            owner = DOWNLOADS.pop(params["guid"], self)
            name = owner.pending_downloads.pop(params["guid"], None)
            if name:
                owner.downloads.append(owner._store_download(params["guid"], name))
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -q && uv run pytest -m browser -q && uv run ruff check .`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "fix: downloads reach the task that started them, across parallel tasks"
```

---

### Task 5: The conversation core — routing, one open question, parallel tasks

**Files:**
- Create: `assistant/core.py`, `tests/test_core.py`
- Modify: `assistant/agent.py` (`title=` parameter), `tests/test_talk.py` (append)

**Interfaces:**
- Consumes: `WebChannel` (Task 1); `messages.render/reply_for`, `messages.ROWS` (Task 2); `model.classify/ambiguous`, `jobs.chat/start/remember` (Task 3, Plan 2); `Agent`, `STOPPED` (Plan 2); `Profile`, `Secrets`, `SECRETS` (Plan 2).
- Produces:
  - `Core(channel, *, user="local", profile=None, make_agent=None, start=None, classify=None, chat=None, remember=None, clock=time.monotonic, poll=1.0)`; `make_agent(url, goal, title, asker, notify) -> agent` where the agent has `state` (`status`, `history`, `files`, `memory`, `approval`, `commit`), `command("tick")`, and `close()`.
  - `Core.receive_webhook(body)`, `Core.receive(event)`, `Core.tasks: dict[id -> Task]`, `Core.open: Question | None`, `Core.queue`, `Core.log`, `Core.lock` (an `RLock` guarding tasks and the question queue).
  - `Task` (`id`, `goal`, `title`, `host`, `status` ∈ `starting|waiting|running|paused|done|blocked|failed|cancelled`, `agent`, `updates`, `cancelled`); `Question` (`id`, `task`, `message`, `reply`, `sent`).
  - Constants `MAX_RUNNING = 3`, `PAUSE_AFTER = 900`, `CANCEL_AFTER = 86400`, `MAX_DOWNLOAD = 100 MiB`; `site(url)`.
  - `Agent(..., title=None)`: the title shown for the task (the start job's title when `url` is None).

- [ ] **Step 1: Write the failing tests** — `tests/test_core.py`:

```python
"""The conversation core with scripted agents: routing, one open question, limits, timers, files. No browser."""

import threading
from types import SimpleNamespace

import pytest
from helpers import until

from assistant import core as conversation
from assistant.channel_web import WebChannel
from assistant.profile import Profile

ADDRESS = {"kind": "choice", "text": "Deliver to which address?", "remember": True,
           "options": [{"id": "o1", "label": "Home"}, {"id": "o2", "label": "Office"}]}
OTP = {"kind": "secret", "text": "Enter the OTP.", "expects": {"length": 6}}


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class FakeAgent:
    """Each tick runs one scripted step; with no steps left the task is done."""

    def __init__(self, script, asker, notify):
        self.script, self.asker, self.notify = list(script), asker, notify
        self.state = {"status": "ready", "history": [], "files": [], "memory": [],
                      "approval": {"action_label": "Pay ₹79"}, "commit": None}
        self.replies, self.closed = [], False

    def command(self, name):
        if self.script:
            self.script.pop(0)(self)
        else:
            self.state["status"] = "done"
        return self.state

    def close(self):
        self.closed = True


def ask(message):
    def step(agent):
        reply = agent.asker({"from": "agent", **message})
        agent.replies.append(reply)
        if reply.get("cancel"):
            agent.state["status"] = "cancelled"
    return step


def tell(text):
    return lambda agent: agent.notify({"from": "agent", "kind": "result", "text": text})


def hold(gate):
    return lambda agent: gate.wait(5)


@pytest.fixture
def world(tmp_path):
    w = SimpleNamespace(scripts={}, hosts={}, agents={}, classified=[], intents=[], clock=Clock(), n=0)
    w.channel = WebChannel(tmp_path / "media")

    def make_agent(url, goal, title, asker, notify):
        w.agents[goal] = FakeAgent(w.scripts.get(goal, []), asker, notify)
        return w.agents[goal]

    def classify(text, tasks, chat):
        w.classified.append(text)
        return w.intents.pop(0) if w.intents else ("NEW_TASK", "none", {"NEW_TASK": 1.0})

    w.core = conversation.Core(
        w.channel, profile=Profile(tmp_path / "profile.json"), make_agent=make_agent,
        start=lambda goal: (f"https://{w.hosts.get(goal, 'shop.test')}/", goal, {}),
        classify=classify, chat=lambda message, tasks: (f"About {len(tasks)} task(s).", {}),
        remember=lambda question, answer: (f"Default choice: {answer}", {}), clock=w.clock, poll=0.01)
    return w


def text(w, body):
    w.n += 1
    w.core.receive({"from": "local", "id": f"wamid.in{w.n}", "type": "text", "text": body, "context": None})


def tap(w, reply_id):
    w.core.receive({"from": "local", "id": "wamid.tap", "type": "interactive", "reply_id": reply_id, "text": "",
                    "context": None})


def sent(w):
    return w.channel.since(0)[0]


def bodies(w):
    return [m["text"]["body"] if m["type"] == "text" else m["interactive"]["body"]["text"]
            for m in sent(w) if m["type"] in {"text", "interactive"}]


def interactive(w):
    return [m for m in sent(w) if m["type"] == "interactive"]


def buttons(w):
    action = interactive(w)[-1]["interactive"]["action"]
    return [b["reply"]["id"] for b in action["buttons"]] if "buttons" in action else [
        r["id"] for r in action["sections"][0]["rows"]]


def test_a_goal_starts_a_task_whose_questions_arrive_as_buttons(world):
    world.scripts["Order Diet Coke"] = [ask(ADDRESS), tell("Ordered.")]
    text(world, "Order Diet Coke")
    assert until(lambda: interactive(world)) and buttons(world) == ["q1:o1", "q1:o2"]
    tap(world, "q1:o1")
    assert until(lambda: "Ordered." in bodies(world))
    agent = world.agents["Order Diet Coke"]
    assert agent.replies == [{"option": "o1"}] and until(lambda: agent.closed)
    assert until(lambda: world.core.tasks["t1"].status == "done")


def test_secret_answers_never_reach_the_classifier(world):
    world.hosts.update({"Book a cab": "uber.test", "Log in to Swiggy": "swiggy.test"})
    world.scripts["Book a cab"] = [ask(ADDRESS)]
    world.scripts["Log in to Swiggy"] = [ask(OTP)]
    text(world, "Book a cab")
    assert until(lambda: interactive(world))
    text(world, "Log in to Swiggy")
    assert until(lambda: len(world.core.queue) == 2)
    tap(world, "q1:o1")
    assert until(lambda: any(m.get("x_secret") for m in sent(world)))
    text(world, "482913")
    assert until(lambda: world.agents["Log in to Swiggy"].replies == [{"text": "482913"}])
    assert "482913" not in world.classified and "482913" not in str(world.core.log)


def test_waiting_questions_show_secrets_and_confirmations_first(world):
    world.hosts.update({"A": "a.test", "B": "b.test", "C": "c.test"})
    world.scripts.update(A=[ask({"kind": "text", "text": "Where to?"})], B=[ask(ADDRESS)], C=[ask(OTP)])
    text(world, "A")
    assert until(lambda: world.core.open is not None)
    text(world, "B")
    assert until(lambda: len(world.core.queue) == 2)
    text(world, "C")
    assert until(lambda: len(world.core.queue) == 3)
    world.intents.append(("ANSWER", "t1", {"ANSWER": 0.95}))
    text(world, "Airport")
    assert until(lambda: world.core.open is not None and world.core.open.task.goal == "C")
    assert world.agents["A"].replies == [{"text": "Airport"}]


def test_taps_on_answered_questions_are_ignored(world):
    world.scripts["Order Diet Coke"] = [ask(ADDRESS), ask({**ADDRESS, "text": "Which size?", "remember": False})]
    text(world, "Order Diet Coke")
    assert until(lambda: interactive(world))
    tap(world, "q1:o1")
    assert until(lambda: world.core.open is not None and world.core.open.id == 2)
    tap(world, "q1:o2")
    assert world.agents["Order Diet Coke"].replies == [{"option": "o1"}] and world.core.open.id == 2


def test_long_lists_are_answered_by_number_without_classifying(world):
    cities = [{"id": f"o{i}", "label": f"City {i}"} for i in range(1, 13)]
    world.scripts["Pick a city"] = [ask({"kind": "choice", "text": "Which city?", "options": cities})]
    text(world, "Pick a city")
    assert until(lambda: any("Reply with a number" in b for b in bodies(world)))
    text(world, "11")
    assert until(lambda: world.agents["Pick a city"].replies == [{"option": "o11"}])
    assert "11" not in world.classified


def test_files_answer_the_question_that_asked_for_them(world):
    world.scripts["Dispute a charge"] = [ask({"kind": "file", "text": "Send the statement."})]
    text(world, "Dispute a charge")
    assert until(lambda: any(m.get("x_expects") == {"file": True} for m in sent(world)))
    media = world.channel.store(b"%PDF", "application/pdf", "statement.pdf")
    world.core.receive({"from": "local", "id": "wamid.f", "type": "document", "media_id": media,
                        "filename": "statement.pdf", "text": "", "context": None})
    path = world.channel.media(media)[0]
    assert until(lambda: world.agents["Dispute a charge"].replies == [{"file": str(path)}])


def test_amendments_status_small_talk_and_cancel(world):
    gate = threading.Event()
    world.scripts["Order Diet Coke"] = [hold(gate), ask(ADDRESS)]
    text(world, "Order Diet Coke")
    assert until(lambda: world.core.tasks["t1"].status == "running")
    world.intents += [("AMEND", "t1", {"AMEND": 0.9}), ("STATUS", "t1", {"STATUS": 0.9}),
                      ("OTHER", "none", {"OTHER": 0.95}), ("CANCEL", "t1", {"CANCEL": 0.97})]
    text(world, "make it 2 cans")
    text(world, "where are we?")
    text(world, "thanks")
    gate.set()
    agent = world.agents["Order Diet Coke"]
    assert until(lambda: "User update: make it 2 cans" in agent.state["memory"]) and agent.state["approval"] is None
    assert until(lambda: interactive(world))
    text(world, "stop the coke order")
    assert until(lambda: agent.replies == [{"cancel": True}])
    got = bodies(world)
    assert "Got it. I'll use that for Order Diet Coke." in got and got.count("About 1 task(s).") == 2


def test_amending_after_a_payment_says_it_already_went_through(world):
    gate = threading.Event()
    world.scripts["Order Diet Coke"] = [lambda agent: agent.state["memory"].append("Done: Pay ₹79 (Order placed)"),
                                        hold(gate)]
    text(world, "Order Diet Coke")
    assert until(lambda: "Order Diet Coke" in world.agents and world.agents["Order Diet Coke"].state["memory"])
    world.intents.append(("AMEND", "t1", {"AMEND": 0.9}))
    text(world, "make it 2 cans")
    assert until(lambda: any(b.startswith("Pay ₹79 already went through.") for b in bodies(world)))
    gate.set()
    assert until(lambda: "User update: make it 2 cans" in world.agents["Order Diet Coke"].state["memory"])


def test_unclear_messages_ask_which_task_they_are_for(world):
    gate = threading.Event()
    world.scripts["Order Diet Coke"] = [hold(gate)]
    text(world, "Order Diet Coke")
    assert until(lambda: world.core.tasks["t1"].status == "running")
    world.intents.append(("AMEND", "t1", {"AMEND": 0.5, "NEW_TASK": 0.45}))
    text(world, "2 please")
    assert until(lambda: interactive(world))
    assert buttons(world) == ["route:t1:wamid.in2", "route:new:wamid.in2"]
    tap(world, "route:t1:wamid.in2")
    gate.set()
    assert until(lambda: "User update: 2 please" in world.agents["Order Diet Coke"].state["memory"])


def test_a_remember_button_follows_an_answered_choice(world):
    world.scripts["Order Diet Coke"] = [ask(ADDRESS)]
    text(world, "Order Diet Coke")
    assert until(lambda: interactive(world))
    tap(world, "q1:o1")
    assert until(lambda: buttons(world) == ["remember:1"])
    tap(world, "remember:1")
    assert until(lambda: "Saved for next time: Default choice: Home" in bodies(world))
    assert world.core.profile.data["preferences"][-1] == {"text": "Default choice: Home",
                                                          "from_task": "Order Diet Coke"}


def test_three_tasks_at_once_and_one_per_site(world):
    gates = {g: threading.Event() for g in "ABCDE"}
    world.hosts.update(A="a.test", B="b.test", C="c.test", D="d.test", E="b.test")
    for g in "ABCDE":
        world.scripts[g] = [hold(gates[g])]
    for g in "ABC":
        text(world, g)
    assert until(lambda: sum(t.status == "running" for t in world.core.tasks.values()) == 3)
    text(world, "D")
    text(world, "E")
    assert until(lambda: "*D*\nQueued: D starts when one of your other tasks finishes." in bodies(world))
    assert until(lambda: "*E*\nWaiting for your other b.test task to finish first." in bodies(world))
    gates["A"].set()
    assert until(lambda: world.core.tasks["t4"].status == "running") and world.core.tasks["t5"].status == "waiting"
    gates["B"].set()
    assert until(lambda: world.core.tasks["t5"].status == "running")
    for g in "CDE":
        gates[g].set()


def test_unanswered_questions_pause_their_task_then_cancel_it(world):
    world.scripts["Order Diet Coke"] = [ask(ADDRESS)]
    text(world, "Order Diet Coke")
    assert until(lambda: interactive(world))
    world.clock.now += conversation.PAUSE_AFTER
    assert until(lambda: "Paused until you answer." in bodies(world))
    assert world.core.tasks["t1"].status == "paused"
    world.clock.now += conversation.CANCEL_AFTER
    assert until(lambda: world.agents["Order Diet Coke"].replies == [{"cancel": True}]) and world.core.open is None


def test_files_the_agent_gets_are_sent_as_documents(world, tmp_path, monkeypatch):
    invoice, huge = tmp_path / "invoice.pdf", tmp_path / "huge.bin"
    invoice.write_bytes(b"%PDF")
    huge.write_bytes(b"x" * 10)
    monkeypatch.setattr(conversation, "MAX_DOWNLOAD", 5)
    world.scripts["Get my invoice"] = [lambda agent: agent.state["files"].extend([str(invoice), str(huge)])]
    text(world, "Get my invoice")
    assert until(lambda: [m for m in sent(world) if m["type"] == "document"])
    document = next(m for m in sent(world) if m["type"] == "document")["document"]
    assert document["filename"] == "invoice.pdf" and world.channel.media(document["id"])[0] == invoice
    assert until(lambda: any("huge.bin is too large to send here" in b for b in bodies(world)))


def test_model_errors_are_reported_and_three_in_a_row_stop_the_task(world):
    def broken(agent):
        raise ValueError("bad JSON")

    world.scripts["Order Diet Coke"] = [broken, broken, broken, tell("never")]
    text(world, "Order Diet Coke")
    assert until(lambda: "Stopping after 3 problems in a row." in bodies(world))
    assert bodies(world).count("Problem: bad JSON") == 3 and "never" not in bodies(world)


def test_a_problem_after_a_commit_stops_with_a_warning(world):
    def committed(agent):
        agent.state["commit"] = {"label": "Pay ₹79", "checks": 1}
        raise RuntimeError("HTTP 503")

    world.scripts["Order Diet Coke"] = [committed, tell("never")]
    text(world, "Order Diet Coke")
    assert until(lambda: any("Pay ₹79 may already have gone through" in b for b in bodies(world)))
    assert "never" not in bodies(world)


def test_titles_prefix_messages_only_when_several_tasks_exist(world):
    first, second = threading.Event(), threading.Event()
    world.hosts["Book a cab"] = "uber.test"
    world.scripts["Order Diet Coke"] = [tell("First."), hold(first), tell("Third.")]
    world.scripts["Book a cab"] = [hold(second)]
    text(world, "Order Diet Coke")
    assert until(lambda: "First." in bodies(world))
    text(world, "Book a cab")
    assert until(lambda: world.core.tasks["t2"].status == "running")
    first.set()
    assert until(lambda: "*Order Diet Coke*\nThird." in bodies(world))
    second.set()


def test_typing_shows_while_a_task_works_and_a_failed_start_says_why(world):
    gate = threading.Event()
    world.scripts["Order Diet Coke"] = [hold(gate)]
    text(world, "Order Diet Coke")
    assert until(lambda: world.channel.typing)
    gate.set()
    assert until(lambda: not world.channel.typing)

    def cannot(goal):
        raise ValueError("The text model returned an invalid start; nothing executed.")

    world.core.start = cannot
    text(world, "Do the impossible")
    assert until(lambda: any("invalid start" in b for b in bodies(world)))


def test_a_message_that_cannot_be_read_says_so(world):
    def down(text, tasks, chat):
        raise RuntimeError("Model connection failed; no action executed.")

    world.core.classify = down
    text(world, "Order Diet Coke")
    assert "I couldn't read that message: Model connection failed; no action executed." in bodies(world)
    assert world.core.tasks == {}
```

Append to `tests/test_talk.py` (it already imports `Mock`, `fake_page`, `FakeVault`, `Profile`, and `loop`):

```python
def test_a_given_title_is_kept(tmp_path):
    a = loop.Agent("https://www.swiggy.com/instamart", "Order Diet Coke", title="Swiggy · Diet Coke",
                   profile=Profile(tmp_path / "p.json"), vault=FakeVault(),
                   browser=Mock(observe=Mock(return_value=fake_page())))
    assert a.state["title"] == "Swiggy · Diet Coke"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_core.py tests/test_talk.py -q`
Expected: FAIL — `ImportError: cannot import name 'core' from 'assistant'`, and `TypeError: Agent.__init__() got an unexpected keyword argument 'title'`.

- [ ] **Step 3: Give the agent a title parameter** — in `assistant/agent.py`, change the signature to
`def __init__(self, url, goal, *, asker=None, notify=None, profile=None, vault=None, record_dir=None, screenshots=False, provide_file=None, browser=None, title=None):`
and replace `title = goal[:60]` with `title = title or goal[:60]`.

- [ ] **Step 4: Implement** — `assistant/core.py`:

```python
"""The conversation: routes every chat message, keeps one question open at a time, and runs each task's agent in
its own thread — at most three at once and one per site."""

import base64
import itertools
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from . import jobs, messages, model
from .agent import STOPPED, Agent
from .profile import SECRETS, Profile, Secrets

MAX_RUNNING = 3
PAUSE_AFTER = 15 * 60
CANCEL_AFTER = 24 * 3600
MAX_DOWNLOAD = 100 * 1024 * 1024
FIRST = {"secret", "confirm"}  # asked before anything older
ACTIVE = {"starting", "waiting", "running", "paused"}


def site(url):
    host = urlparse(url).netloc.lower()
    for prefix in ("www.", "m."):
        host = host.removeprefix(prefix)
    return host


class Question:
    def __init__(self, qid, task, message, asked_at):
        self.id, self.task, self.message, self.asked_at = qid, task, message, asked_at
        self.reply, self.sent, self.answered = None, [], threading.Event()


class Task:
    def __init__(self, tid, goal):
        self.id, self.goal, self.title, self.host = tid, goal, goal[:60], None
        self.status, self.agent, self.updates = "starting", None, []
        self.cancelled, self.sent_files, self.problems = False, 0, 0

    def view(self, question=None):
        history = self.agent.state["history"] if self.agent else []
        view = {"id": self.id, "title": self.title, "goal": self.goal,
                "step": history[-1]["action"] if history else self.status}
        if question:
            view["open_question"] = question.message["text"]
        return view


class Core:
    def __init__(self, channel, *, user="local", profile=None, make_agent=None, start=None, classify=None,
                 chat=None, remember=None, clock=time.monotonic, poll=1.0):
        self.channel, self.user = channel, user
        self.profile = profile or Profile()
        self.make_agent = make_agent or self.agent_for
        self.start = start or jobs.start
        self.classify = classify or model.classify
        self.chat = chat or jobs.chat
        self.remember_job = remember or jobs.remember
        self.clock, self.poll = clock, poll
        self.lock = threading.RLock()
        self.slots = threading.Condition(self.lock)
        self.tasks, self.queue, self.open, self.log = {}, [], None, []
        self.remembering, self.routes = {}, {}
        self.task_ids, self.question_ids = itertools.count(1), itertools.count(1)
        self.secrets = Secrets(self.profile.hidden_values())  # classification never sees the profile's values

    def agent_for(self, url, goal, title, asker, notify):
        return Agent(url, goal, title=title, asker=asker, notify=notify, profile=self.profile)

    def hidden(self, call, *args):
        """A model call with the user's profile values redacted."""
        token = SECRETS.set(self.secrets)
        try:
            return call(*args)
        finally:
            SECRETS.reset(token)

    # ---- sending

    def several(self):
        with self.lock:  # task threads call this while the webhook thread may be adding a task
            return sum(t.status in ACTIVE for t in self.tasks.values()) > 1

    def send(self, message, task=None, question=None):
        title = task.title if task and self.several() else None
        image_id = self.channel.store(base64.b64decode(message["image"]), "image/png") if message.get("image") else None
        for payload in messages.render(message, question.id if question else None, image_id, title):
            sent = self.channel.send(self.user, payload)
            if question:
                question.sent.append(sent)
        self.log.append({"from": "agent", "text": message["text"]})

    def say(self, text, task=None):
        self.send({"kind": "info", "text": text}, task)

    def buttons(self, body, options):
        self.channel.send(self.user, {"type": "interactive", "interactive": {"type": "button", "body": {"text": body},
                          "action": {"buttons": [{"type": "reply", "reply": {"id": rid, "title": title[:20]}}
                                                 for rid, title in options]}}})

    def update_typing(self):
        with self.lock:
            asking = {q.task for q in self.queue}
            self.channel.set_typing(any(t.status == "running" and t not in asking for t in self.tasks.values()))

    # ---- receiving

    def receive_webhook(self, body):
        for event in self.channel.events(body):
            self.receive(event)

    def receive(self, event):
        if (event.get("reply_id") or "").startswith("remember:"):
            return self.remember(event["reply_id"].partition(":")[2])  # a model call: outside the lock
        with self.lock:
            question = self.open
            secret = question is not None and question.message["kind"] == "secret"
            text = (event.get("text") or "").strip()
            self.log.append({"from": "user", "text": "••••" if secret and not event.get("reply_id") else text})
            if event.get("reply_id"):
                return self.tapped(event)
            takes_file = question is not None and question.message["kind"] in {"file", "image", "text"}
            if question and (secret or (event.get("context") and event["context"] in question.sent)
                             or (event.get("media_id") and takes_file) or self.numbered(question, text)):
                return self.answer(question, event)  # a secret answer never reaches any model
            if event.get("media_id"):
                return self.say("I wasn't expecting a file just now.")
            if not text:
                return None
            views, chat = self.views(), self.log[-7:-1]
        try:
            intent, target, probabilities = self.hidden(self.classify, text, views, chat)
        except Exception as error:  # a model or network failure is a chat message, never a failed webhook
            return self.say(f"I couldn't read that message: {error}")
        return self.route(event, text, intent, target, probabilities)

    def numbered(self, question, text):
        options = question.message.get("options") or []
        return len(options) > messages.ROWS and text.isdigit() and 1 <= int(text) <= len(options)

    def views(self):
        return [t.view(self.open if self.open and self.open.task is t else None)
                for t in self.tasks.values() if t.status in ACTIVE]

    def route(self, event, text, intent, target, probabilities):
        with self.lock:
            active = [t for t in self.tasks.values() if t.status in ACTIVE]
            task = self.tasks.get(target)
            if task not in active:
                task = active[0] if len(active) == 1 else None
            if active and model.ambiguous(probabilities):
                return self.which(event, text, active)
            if intent == "ANSWER" and self.open:
                return self.answer(self.open, event)
            if intent == "NEW_TASK":
                return self.new_task(text)
            if intent == "CANCEL" and task:
                return self.cancel(task)
            if intent == "AMEND" and task:
                task.updates.append(text)
                done = [m.removeprefix("Done: ").rpartition(" (")[0] for m in task.agent.state["memory"]
                        if m.startswith("Done: ")] if task.agent else []  # the agent's record of a finished commit
                if done:
                    return self.say(f"{done[-1]} already went through. I'll try to change it on the site; anything "
                                    "that costs money again waits for your OK. Say “cancel” to stop instead.", task)
                return self.say(f"Got it. I'll use that for {task.title}.")
            views = self.views()
        try:
            reply, _ = self.hidden(self.chat, text, views)  # STATUS, OTHER, or nothing to act on
        except Exception as error:
            reply = f"I couldn't answer that: {error}"
        self.say(reply)

    def which(self, event, text, active):
        self.routes[event["id"]] = text
        options = [(f"route:{t.id}:{event['id']}", t.title) for t in active[:2]]
        self.buttons("Is this about one of your tasks, or something new?",
                     options + [(f"route:new:{event['id']}", "New task")])

    def tapped(self, event):
        kind, _, rest = event["reply_id"].partition(":")
        if kind == "route":
            target, _, key = rest.partition(":")
            text = self.routes.pop(key, None)
            task = self.tasks.get(target)
            if text is None:
                return None
            if target == "new" or not task or task.status not in ACTIVE:
                return self.new_task(text)
            if self.open and self.open.task is task:
                return self.answer(self.open, {"type": "text", "text": text})
            task.updates.append(text)
            return self.say(f"Got it. I'll use that for {task.title}.")
        question = self.open
        if question and messages.reply_for(question.id, question.message.get("options") or [], event) is not None:
            return self.answer(question, event)
        return None  # a tap on a question that is already answered

    def answer(self, question, event):
        reply = messages.reply_for(question.id, question.message.get("options") or [], event)
        if reply is None:
            return None
        if "media_id" in reply:
            try:
                path, _mime, _name = self.channel.media(reply.pop("media_id"))
            except KeyError:
                return self.say("That file didn't arrive. Please send it again.")
            reply["file"] = str(path)
        with self.lock:
            question.reply = reply
            if question in self.queue:
                self.queue.remove(question)
            if self.open is question:
                self.open = None
            if question.message.get("remember") and reply.get("option"):
                self.offer_remember(question, reply["option"])
            self.show_next()
        question.answered.set()

    def offer_remember(self, question, option):
        label = next((o["label"] for o in question.message["options"] if o["id"] == option), None)
        if label:
            self.remembering[str(question.id)] = (question.message["text"], label, question.task.goal)
            self.buttons(f"Remember “{label}” for next time?", [(f"remember:{question.id}", "Remember this")])

    def remember(self, key):
        item = self.remembering.pop(key, None)
        if item:
            question, label, goal = item
            try:
                preference, _ = self.hidden(self.remember_job, question, label)
            except Exception as error:
                return self.say(f"I couldn't save that: {error}")
            self.profile.remember(preference, goal)
            self.say(f"Saved for next time: {preference}")

    def show_next(self):
        if self.open or not self.queue:
            return
        question = min(self.queue, key=lambda q: (q.message["kind"] not in FIRST, q.asked_at, q.id))
        self.open = question
        self.send(question.message, question.task, question)

    # ---- tasks

    def new_task(self, goal):
        task = Task(f"t{next(self.task_ids)}", goal)
        self.tasks[task.id] = task
        threading.Thread(target=self.run, args=(task,), name=f"jev-{task.id}", daemon=True).start()
        return task

    def cancel(self, task):
        task.cancelled = True
        waiting = next((q for q in self.queue if q.task is task), None)
        if waiting:
            waiting.reply = {"cancel": True}
            self.queue.remove(waiting)
            if self.open is waiting:
                self.open = None
                self.show_next()
            waiting.answered.set()
        else:
            self.say(f"Stopping {task.title} after its current step.")
        self.slots.notify_all()

    def acquire(self, task):
        """Wait for a slot: at most MAX_RUNNING tasks, and never two on the same site."""
        with self.slots:
            told = False
            while not task.cancelled:
                busy = [t for t in self.tasks.values() if t is not task and t.status in {"running", "paused"}]
                clash = next((t for t in busy if t.host == task.host), None)
                if len(busy) < MAX_RUNNING and not clash:
                    task.status = "running"
                    return True
                task.status = "waiting"
                if not told:
                    told = True
                    self.say(f"Waiting for your other {task.host} task to finish first." if clash else
                             f"Queued: {task.title} starts when one of your other tasks finishes.", task)
                self.slots.wait(self.poll)
            return False

    def run(self, task):
        try:
            url, task.title, _ = self.hidden(self.start, task.goal)
            task.host = site(url)
            if not self.acquire(task):
                return self.say(f"Cancelled {task.title} before it started.")
            task.agent = self.make_agent(url, task.goal, task.title, lambda m: self.ask(task, m),
                                         lambda m: self.send(m, task))
            self.update_typing()
            while task.agent.state["status"] not in STOPPED and not task.cancelled:
                self.apply_updates(task)
                try:
                    task.agent.command("tick")
                    task.problems = 0
                except (ValueError, RuntimeError) as error:
                    if not self.problem(task, error):
                        break
                self.deliver_files(task)
        except Exception as error:  # the start job, launching the browser, anything: say it once
            self.say(f"I couldn't carry on with {task.title}: {error}")
        finally:
            state = task.agent.state["status"] if task.agent else None
            task.status = state if state in STOPPED else ("cancelled" if task.cancelled else "failed")
            if task.agent:
                try:
                    task.agent.close()
                except Exception:
                    pass
            with self.slots:
                self.slots.notify_all()
            self.update_typing()

    def apply_updates(self, task):
        while task.updates:
            task.agent.state["memory"].append(f"User update: {task.updates.pop(0)}")
            task.agent.state["approval"] = None  # an amendment voids any approval given before it

    def problem(self, task, error):
        """Report a model or network error. False when the task must stop."""
        task.problems += 1
        self.say(f"Problem: {error}", task)
        commit = task.agent.state.get("commit")
        if commit:
            self.say(f"{commit['label']} may already have gone through. Check it before asking me again.", task)
            return False
        if task.problems >= 3:
            self.say("Stopping after 3 problems in a row.", task)
            return False
        return True

    def ask(self, task, message):
        """The agent's asker: queue the question and wait for its answer, pausing and finally cancelling."""
        question = Question(next(self.question_ids), task, message, self.clock())
        with self.lock:
            self.queue.append(question)
            self.show_next()
            self.update_typing()
        paused = False
        while not question.answered.wait(self.poll):
            waited = self.clock() - question.asked_at
            if waited >= CANCEL_AFTER:
                with self.lock:
                    if question in self.queue:
                        self.queue.remove(question)
                    if self.open is question:
                        self.open = None
                        self.show_next()
                self.say(f"No answer for a day, so I cancelled {task.title}.", task)
                return {"cancel": True}
            if waited >= PAUSE_AFTER and not paused:
                paused, task.status = True, "paused"
                self.say("Paused until you answer.", task)
        task.status = "running"
        self.update_typing()
        return question.reply

    def deliver_files(self, task):
        files = task.agent.state["files"]
        for name in files[task.sent_files:]:
            path = Path(name)
            if path.is_file() and path.stat().st_size <= MAX_DOWNLOAD:
                media_id = self.channel.register(path)
                self.channel.send(self.user, {"type": "document", "document": {"id": media_id, "filename": path.name}})
            else:
                self.say(f"{path.name} is too large to send here; it's saved at {path}.", task)
        task.sent_files = len(files)
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest -q && uv run ruff check .`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: conversation core with intent routing, one open question, and parallel tasks"
```

---

### Task 6: The chat server — loopback, token-checked, WhatsApp-shaped endpoints

**Files:**
- Modify: `assistant/server.py` (add `ChatServer`, `ChatHandler`, `serve`, `debug_view`; `jev` with no subcommand serves the chat)
- Modify: `assistant/core.py` (`receive_webhook` accepts only the configured user)
- Create: `tests/test_chat_server.py`
- Modify: `tests/test_server.py` (replace `test_no_command_prints_help`)

**Interfaces:**
- Consumes: `WebChannel.since/store/media/events` (Task 1); `Core.receive_webhook`, `Core.tasks`, `Core.lock` (Task 5).
- Produces:
  - `ChatServer(core, channel, *, port=CHAT_PORT, token=None)` (a `ThreadingHTTPServer` on `127.0.0.1`; `.token`, `.server_port`).
  - Routes: `GET /`, `/app.js`, `/style.css` (Host checked; `{{TOKEN}}` in `index.html` replaced with the token); `GET /outbox?after=&wait=` → `{"messages", "typing"}`; `POST /webhook` (WhatsApp webhook JSON ≤ 64 KB) → `{"ok": true}`; `POST /media` (raw bytes ≤ 20 MB, `Content-Type`, URL-encoded `X-Filename`) → `{"id"}`; `GET /media/<id>`; `GET /api/debug` (steps and a masked screenshot per task) only when `JEV_DEBUG=1`. API and media routes need `X-Jev-Token` or `?t=`.
  - Constants `CHAT_PORT = 8766`, `MAX_WEBHOOK = 64 KiB`, `MAX_UPLOAD = 20 MiB`, `STATIC = assistant/static`.
  - `serve(port=CHAT_PORT) -> int`: starts the hidden agent Chrome if needed, then serves until Ctrl+C.

- [ ] **Step 1: Write the failing tests** — `tests/test_chat_server.py`:

```python
"""The chat server: loopback only, token-checked, WhatsApp-shaped endpoints. No browser."""

import threading
from types import SimpleNamespace

import httpx
import pytest

from assistant import server
from assistant.channel_web import WebChannel

WEBHOOK = {"object": "whatsapp_business_account", "entry": [{"id": "local", "changes": [{"field": "messages", "value": {
    "messaging_product": "whatsapp", "messages": [{"from": "local", "id": "wamid.1", "type": "text",
                                                   "text": {"body": "hi"}}]}}]}]}


class FakeCore:
    def __init__(self):
        self.bodies, self.tasks, self.lock = [], {}, threading.RLock()

    def receive_webhook(self, body):
        WebChannel.events(body)  # refuses what the real core refuses
        self.bodies.append(body)


@pytest.fixture
def chat(tmp_path, monkeypatch):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text('<meta name="jev-token" content="{{TOKEN}}">')
    (static / "app.js").write_text("// app")
    (static / "style.css").write_text("/* style */")
    monkeypatch.setattr(server, "STATIC", static)
    channel, core = WebChannel(tmp_path / "media"), FakeCore()
    httpd = server.ChatServer(core, channel, port=0, token="secret-token")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_port}"
    client = httpx.Client(base_url=base, headers={"X-Jev-Token": "secret-token"}, timeout=10)
    yield SimpleNamespace(core=core, channel=channel, client=client, base=base, port=httpd.server_port)
    client.close()
    httpd.shutdown()
    httpd.server_close()


def test_the_page_carries_the_token_under_a_strict_policy(chat):
    page = httpx.get(chat.base + "/")
    assert page.status_code == 200 and page.text == '<meta name="jev-token" content="secret-token">'
    policy = page.headers["content-security-policy"]
    assert "script-src 'self'" in policy and "frame-ancestors 'none'" in policy
    assert httpx.get(chat.base + "/app.js").headers["content-type"].startswith("text/javascript")
    assert httpx.get(chat.base + "/style.css").text == "/* style */"
    assert httpx.get(chat.base + "/", headers={"Host": f"evil.test:{chat.port}"}).status_code == 403


def test_api_routes_need_the_token_and_this_server_s_own_name(chat):
    assert httpx.get(chat.base + "/outbox").status_code == 403
    assert chat.client.get("/outbox", headers={"X-Jev-Token": "wrong"}).status_code == 403
    assert chat.client.get("/outbox", headers={"Host": f"evil.test:{chat.port}"}).status_code == 403
    assert chat.client.post("/webhook", json=WEBHOOK, headers={"Origin": "http://evil.test"}).status_code == 403
    assert chat.client.get("/outbox").json() == {"messages": [], "typing": False}
    assert httpx.get(chat.base + "/outbox?t=secret-token").status_code == 200
    assert chat.core.bodies == []


def test_webhooks_reach_the_core_and_the_outbox_long_polls(chat):
    assert chat.client.post("/webhook", json=WEBHOOK, headers={"Origin": chat.base}).json() == {"ok": True}
    assert chat.core.bodies == [WEBHOOK]
    threading.Timer(0.1, lambda: chat.channel.send("local", {"type": "text", "text": {"body": "hello"}})).start()
    got = chat.client.get("/outbox", params={"after": 0, "wait": 5}).json()
    assert [m["text"]["body"] for m in got["messages"]] == ["hello"]
    assert chat.client.get("/outbox", params={"after": 1, "wait": "nan"}).json()["messages"] == []
    assert chat.client.get("/outbox", params={"after": "x"}).status_code == 400


def test_bad_bodies_are_refused_and_the_server_survives(chat):
    assert chat.client.post("/webhook", content=b"{not json").status_code == 400
    assert chat.client.post("/webhook", json={"entry": "x"}).status_code == 400
    assert chat.client.post("/webhook", json=[1, 2]).status_code == 400
    assert chat.client.post("/webhook", content=b"x" * (65 * 1024)).status_code == 413
    assert chat.client.post("/nowhere", json={}).status_code == 404
    assert chat.client.get("/outbox").status_code == 200 and chat.core.bodies == []


def test_uploads_and_media_round_trip(chat, monkeypatch):
    stored = chat.client.post("/media", content=b"%PDF-1.4", headers={
        "Content-Type": "application/pdf", "X-Filename": "..%2F..%2Fstatement%20%E2%82%B9.pdf"}).json()["id"]
    path, mime, name = chat.channel.media(stored)
    assert path.read_bytes() == b"%PDF-1.4" and mime == "application/pdf" and name == "statement ₹.pdf"
    got = chat.client.get(f"/media/{stored}")
    assert got.content == b"%PDF-1.4" and got.headers["content-disposition"].startswith("attachment;")
    assert got.headers["content-security-policy"] == "sandbox"
    image = chat.channel.store(b"\x89PNG", "image/png")
    assert httpx.get(f"{chat.base}/media/{image}?t=secret-token").headers["content-disposition"] == "inline"
    assert chat.client.get("/media/unknown").status_code == 404
    assert chat.client.post("/media", content=b"").status_code == 400
    monkeypatch.setattr(server, "MAX_UPLOAD", 4)
    assert chat.client.post("/media", content=b"12345").status_code == 413


def test_the_debug_view_exists_only_when_asked(chat, monkeypatch):
    monkeypatch.delenv("JEV_DEBUG", raising=False)
    assert chat.client.get("/api/debug").status_code == 404
    monkeypatch.setenv("JEV_DEBUG", "1")
    assert chat.client.get("/api/debug").json() == []


def test_the_debug_view_shows_steps_and_a_masked_screenshot():
    masks = []
    agent = SimpleNamespace(state={"history": [{"action": "Pay ₹79"}]}, secrets=SimpleNamespace(nodes={(1, 2)}),
                            browser=SimpleNamespace(screenshot=lambda masked: masks.append(masked) or "iVBOR"))
    task = SimpleNamespace(id="t1", title="Diet Coke", status="running", host="shop.test", agent=agent)
    assert server.debug_view(SimpleNamespace(lock=threading.RLock(), tasks={"t1": task})) == [
        {"id": "t1", "title": "Diet Coke", "status": "running", "host": "shop.test",
         "history": [{"action": "Pay ₹79"}], "screenshot": "iVBOR"}]
    assert masks == [{(1, 2)}]
```

In `tests/test_server.py`, replace `test_no_command_prints_help` with:

```python
def test_no_command_serves_the_chat(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    calls = []
    monkeypatch.setattr(server, "serve", lambda: calls.append("serve") or 0)
    assert server.main([]) == 0 and calls == ["serve"]
```

Append to `tests/test_core.py`:

```python
def test_only_the_configured_user_is_heard(world):
    stranger = {"from": "someone-else", "id": "wamid.x", "type": "text", "text": {"body": "Order Diet Coke"}}
    body = {"object": "whatsapp_business_account",
            "entry": [{"id": "x", "changes": [{"field": "messages", "value": {"messages": [stranger]}}]}]}
    world.core.receive_webhook(body)
    assert world.classified == [] and world.core.tasks == {}
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_chat_server.py tests/test_server.py tests/test_core.py -q`
Expected: FAIL — `AttributeError: module 'assistant.server' has no attribute 'ChatServer'`, `test_no_command_serves_the_chat` fails (help is printed, `serve` never called), and `test_only_the_configured_user_is_heard` fails (a task starts).

- [ ] **Step 3: Implement**

In `assistant/core.py`, `receive_webhook` becomes:
```python
    def receive_webhook(self, body):
        for event in self.channel.events(body):
            if event["from"] == self.user:  # one user; on WhatsApp, strangers messaging the number are ignored
                self.receive(event)
```

In `assistant/server.py`, update the module docstring to
`"""Command line and chat server. `jev` serves the chat; `jev chrome` launches the hidden agent Chrome; `jev probe`
checks sites for blocking; `jev run` does one task in the terminal."""`, extend the imports to

```python
import argparse
import base64
import getpass
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit
```

and add after `PORT = 9333`:

```python
CHAT_PORT = 8766
MAX_WEBHOOK = 64 * 1024
MAX_UPLOAD = 20 * 1024 * 1024
DRAIN_LIMIT = 64 * 1024 * 1024  # larger refused bodies are cut off instead of read
STATIC = Path(__file__).parent / "static"
ASSETS = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8")}
IMAGES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
PAGE_POLICY = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; "
               "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
```

Then add, before `def main`:

```python
def debug_view(core):
    """Each task's recent steps and a live screenshot with secret fields masked. Development only."""
    with core.lock:
        tasks = list(core.tasks.values())
    view = []
    for t in tasks:  # screenshots are taken outside the core's lock: they are slow
        try:
            shot = t.agent.browser.screenshot(masked=t.agent.secrets.nodes) if t.agent else None
        except Exception:  # the task's tab may be closing
            shot = None
        view.append({"id": t.id, "title": t.title, "status": t.status, "host": t.host,
                     "history": t.agent.state["history"][-20:] if t.agent else [], "screenshot": shot})
    return view


class ChatHandler(BaseHTTPRequestHandler):
    server_version = "jev"

    def log_message(self, *_args):
        pass  # chat content never reaches the terminal

    def reply(self, status, body=b"", kind="application/json", headers=()):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def local(self):
        """Host and Origin name this server, so no other site and no DNS-rebound name reaches it."""
        names = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        origin = self.headers.get("Origin")
        return self.headers.get("Host") in names and (origin is None or origin.removeprefix("http://") in names)

    def authorised(self, query):
        token = self.headers.get("X-Jev-Token") or (query.get("t") or [""])[0]
        return self.local() and secrets.compare_digest(token.encode(), self.server.token.encode())

    def body(self, limit):
        """The request body, or None when its length is missing, malformed, or over `limit`."""
        length = self.headers.get("Content-Length", "")
        if length.isdigit() and int(length) <= limit:
            return self.rfile.read(int(length))
        self.close_connection = True
        if length.isdigit() and int(length) <= DRAIN_LIMIT:  # read and drop it, so the client sees the 413
            remaining = int(length)                           # instead of a reset connection
            while remaining > 0 and (chunk := self.rfile.read(min(remaining, 65536))):
                remaining -= len(chunk)
        return None

    def do_GET(self):
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        if url.path in ASSETS:
            if not self.local():
                return self.reply(403, {"error": "forbidden"})
            name, kind = ASSETS[url.path]
            data = (STATIC / name).read_bytes().replace(b"{{TOKEN}}", self.server.token.encode())
            return self.reply(200, data, kind, [("Content-Security-Policy", PAGE_POLICY)])
        if not self.authorised(query):
            return self.reply(403, {"error": "forbidden"})
        if url.path == "/outbox":
            try:
                after = int(query.get("after", ["0"])[0])
                wait = max(0.0, min(float(query.get("wait", ["0"])[0]), 25.0))  # NaN becomes 0
            except ValueError:
                return self.reply(400, {"error": "bad query"})
            messages, typing = self.server.channel.since(after, wait)
            return self.reply(200, {"messages": messages, "typing": typing})
        if url.path.startswith("/media/"):
            try:
                path, mime, name = self.server.channel.media(url.path.removeprefix("/media/"))
                data = Path(path).read_bytes()
            except (KeyError, OSError):
                return self.reply(404, {"error": "not found"})
            download = quote(name or Path(path).name)
            disposition = "inline" if mime in IMAGES else f"attachment; filename*=UTF-8''{download}"
            return self.reply(200, data, mime, [("Content-Disposition", disposition),
                                                ("Content-Security-Policy", "sandbox")])
        if url.path == "/api/debug" and os.environ.get("JEV_DEBUG") == "1":
            return self.reply(200, debug_view(self.server.core))
        return self.reply(404, {"error": "not found"})

    def do_POST(self):
        url = urlsplit(self.path)
        if not self.authorised(parse_qs(url.query)):
            return self.reply(403, {"error": "forbidden"})
        if url.path == "/webhook":
            data = self.body(MAX_WEBHOOK)
            if data is None:
                return self.reply(413, {"error": "body missing or too large"})
            try:
                self.server.core.receive_webhook(json.loads(data))
            except ValueError:  # bad JSON, or JSON that is not a WhatsApp webhook body
                return self.reply(400, {"error": "not a WhatsApp webhook body"})
            return self.reply(200, {"ok": True})
        if url.path == "/media":
            data = self.body(MAX_UPLOAD)
            if data is None:
                return self.reply(413, {"error": "body missing or too large"})
            if not data:
                return self.reply(400, {"error": "empty file"})
            mime = (self.headers.get("Content-Type") or "application/octet-stream").split(";")[0].strip()
            name = Path(unquote(self.headers.get("X-Filename") or "")).name or None
            return self.reply(200, {"id": self.server.channel.store(data, mime, name)})
        return self.reply(404, {"error": "not found"})


class ChatServer(ThreadingHTTPServer):
    """The chat page and the channel's endpoints, on loopback only."""

    daemon_threads = True

    def __init__(self, core, channel, *, port=CHAT_PORT, token=None):
        self.core, self.channel = core, channel
        self.token = token or secrets.token_urlsafe(24)
        super().__init__(("127.0.0.1", port), ChatHandler)


def serve(port=CHAT_PORT):
    from .channel_web import WebChannel
    from .core import Core

    if chrome():
        return 1
    channel = WebChannel(home() / "media")
    httpd = ChatServer(Core(channel), channel, port=port)
    print(f"Chat: http://127.0.0.1:{httpd.server_port}/  (Ctrl+C stops)", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0
```

In `main`, change the parser description to `"Jev Assistant. With no command, serves the chat."`, and replace the final `parser.print_help()` / `return 0` with `return serve()`.

A JSON list such as `[1, 2]` reaches `WebChannel.events`, which already turns its `TypeError` into `ValueError`, so it is refused with 400 like any other non-webhook body.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -q && uv run ruff check .`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: loopback chat server with token, Host and Origin checks, webhook, outbox, and media"
```

---

### Task 7: The WhatsApp-like chat page

**Files:**
- Create: `assistant/static/index.html`, `assistant/static/app.js`, `assistant/static/style.css`, `tests/test_chat_page.py`

**Interfaces:**
- Consumes: Task 6 routes (`/outbox`, `/webhook`, `/media`, `/media/<id>`, token in `<meta name="jev-token">`); Task 2 payload shapes (`text`, `interactive` button/list, `image`, `document`, `x_question`, `x_secret`, `x_expects`, `x_id`, `x_seq`).
- Produces: a page whose composer input is labelled `Message` (a password field while a secret question is open), whose file input is labelled `Attach a file`, whose header status reads `typing…` while a task works, and which posts WhatsApp webhook bodies: text `{"type": "text", "text": {"body"}}`, taps `{"type": "interactive", "context": {"id": <x_id>}, "interactive": {"type": "button_reply"|"list_reply", ...: {"id", "title"}}}`, files `{"type": "document"|"image", ...: {"id", "filename"?, "mime_type"}}`, all `from: "local"`.

- [ ] **Step 1: Write the failing tests** — `tests/test_chat_page.py`:

```python
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

    def receive_webhook(self, body):
        self.events += WebChannel.events(body)


@pytest.fixture
def chat(chrome, tmp_path):
    from assistant.browser import Browser

    channel, core = WebChannel(tmp_path / "media"), RecordingCore()
    httpd = server.ChatServer(core, channel, port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    browser = Browser(f"http://127.0.0.1:{httpd.server_port}/")
    yield SimpleNamespace(browser=browser, channel=channel, core=core)
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
    chat.channel.send("local", {"type": "text", "x_question": 3, "x_secret": True, "x_expects": {"length": 6},
                                "text": {"body": "Enter the OTP."}})
    page = observe_until(chat.browser, lambda p: "Enter the OTP." in p["text"])
    assert js(chat, "document.getElementById('text').type") == "password"
    chat.browser.act("TYPE_TEXT", find(page, "Message", "fill"), page, text="482913")
    page = chat.browser.observe()
    chat.browser.act("PRESS_KEY", find(page, "Message", "fill"), page, key="Enter")
    assert until(lambda: chat.core.events) and chat.core.events[0]["text"] == "482913"
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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest -m browser tests/test_chat_page.py -q`
Expected: FAIL — the page never loads (the server raises `FileNotFoundError` for `assistant/static/index.html`), so the first `observe_until` or `until` times out.

- [ ] **Step 3: Implement** — `assistant/static/index.html`:

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="jev-token" content="{{TOKEN}}">
<title>Jev</title>
<link rel="stylesheet" href="/style.css">
<script src="/app.js" defer></script>
</head>
<body>
<header>
  <div class="avatar" aria-hidden="true">J</div>
  <div><h1>Jev</h1><p id="status">online</p></div>
</header>
<main id="chat" aria-live="polite"></main>
<form id="composer" autocomplete="off">
  <label class="attach" title="Attach a file">📎<input type="file" id="file" aria-label="Attach a file" hidden></label>
  <input id="text" aria-label="Message" placeholder="Message" autocomplete="off">
  <button id="send" type="submit" aria-label="Send">➤</button>
</form>
</body>
</html>
```

`assistant/static/app.js`:

```javascript
// The chat page. WhatsApp Cloud API shapes in and out; every message text is set as text, never as HTML.
'use strict';

const token = document.querySelector('meta[name="jev-token"]').content;
const chat = document.getElementById('chat');
const form = document.getElementById('composer');
const input = document.getElementById('text');
const file = document.getElementById('file');
const status = document.getElementById('status');
const MAX_UPLOAD = 20 * 1024 * 1024;
let after = 0;
let sent = 0;
let secretFor = null; // the open secret question: what is typed is hidden and shown as ••••

const api = (path, options = {}) => fetch(path, {...options, headers: {...options.headers, 'X-Jev-Token': token}});
const media = id => `/media/${encodeURIComponent(id)}?t=${encodeURIComponent(token)}`;

function el(tag, className, text) {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text !== undefined) e.textContent = text;
  return e;
}

function bubble(side) {
  const b = el('div', `bubble ${side}`);
  chat.append(b);
  return b;
}

function note(text) {
  chat.append(el('div', 'note', text));
  chat.scrollTop = chat.scrollHeight;
}

function body(target, text) {
  const title = /^\*([^*\n]+)\*\n/.exec(text); // WhatsApp bold: the task title when several tasks run
  if (title) {
    target.append(el('strong', 'title', title[1]));
    text = text.slice(title[0].length);
  }
  target.append(el('p', 'text', text));
}

function choices(b, m) {
  const action = m.interactive.action;
  const kind = action.buttons ? 'button_reply' : 'list_reply';
  const options = action.buttons ? action.buttons.map(x => x.reply) : action.sections.flatMap(s => s.rows);
  const group = el('div', action.buttons ? 'buttons' : 'rows');
  for (const option of options) {
    const button = el('button', 'choice', option.title);
    button.type = 'button';
    if (option.description) button.append(el('small', '', option.description));
    button.addEventListener('click', () => {
      group.querySelectorAll('button').forEach(x => { x.disabled = true; });
      button.classList.add('picked');
      send({type: 'interactive', context: {id: m.x_id},
            interactive: {type: kind, [kind]: {id: option.id, title: option.title}}}, option.title);
    });
    group.append(button);
  }
  b.append(group);
}

function show(m) {
  const b = bubble('in');
  if (m.type === 'text') body(b, m.text.body);
  if (m.type === 'interactive') {
    body(b, m.interactive.body.text);
    choices(b, m);
  }
  if (m.type === 'image') {
    const img = el('img', 'shot');
    img.alt = 'Screenshot';
    img.src = media(m.image.id);
    b.append(img);
  }
  if (m.type === 'document') {
    const link = el('a', 'doc', '📄 ' + (m.document.filename || 'file'));
    link.href = media(m.document.id);
    link.download = m.document.filename || '';
    b.append(link);
  }
  if (m.x_question !== undefined && m.x_question !== secretFor) secretFor = m.x_secret ? m.x_question : null;
  if (m.x_expects && m.x_expects.file) document.body.classList.add('wants-file');
  mode();
}

function mode() {
  input.type = secretFor === null ? 'text' : 'password';
  input.placeholder = secretFor === null ? 'Message' : 'Hidden answer, never sent to any model';
}

async function send(message, shown) {
  const own = bubble('out');
  own.append(el('p', 'text', shown));
  chat.scrollTop = chat.scrollHeight;
  sent += 1;
  const entry = {from: 'local', id: `wamid.in${Date.now()}.${sent}`, timestamp: String(Math.floor(Date.now() / 1000)),
                 ...message};
  const hook = {object: 'whatsapp_business_account', entry: [{id: 'local', changes: [{field: 'messages', value: {
    messaging_product: 'whatsapp', contacts: [{wa_id: 'local'}], messages: [entry]}}]}]};
  try {
    const r = await api('/webhook', {method: 'POST', headers: {'Content-Type': 'application/json'},
                                     body: JSON.stringify(hook)});
    if (!r.ok) throw new Error(String(r.status));
  } catch {
    own.classList.add('failed');
    own.append(el('small', 'error', 'Not delivered. Is jev still running?'));
  }
}

form.addEventListener('submit', event => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  const secret = secretFor !== null;
  input.value = '';
  secretFor = null;
  mode();
  send({type: 'text', text: {body: text}}, secret ? '••••' : text);
});

file.addEventListener('change', async () => {
  const chosen = file.files[0];
  file.value = '';
  if (!chosen) return;
  if (chosen.size > MAX_UPLOAD) return note(`${chosen.name} is over 20 MB, so it can't be sent here.`);
  try {
    const r = await api('/media', {method: 'POST', body: chosen, headers: {
      'Content-Type': chosen.type || 'application/octet-stream', 'X-Filename': encodeURIComponent(chosen.name)}});
    if (!r.ok) throw new Error(String(r.status));
    const {id} = await r.json();
    const type = chosen.type.startsWith('image/') ? 'image' : 'document';
    document.body.classList.remove('wants-file');
    send({type, [type]: {id, filename: chosen.name, mime_type: chosen.type}}, '📎 ' + chosen.name);
  } catch {
    note(`${chosen.name} could not be sent.`);
  }
});

async function poll() {
  for (;;) {
    try {
      const r = await api(`/outbox?after=${after}&wait=25`);
      if (!r.ok) throw new Error(String(r.status));
      const {messages, typing} = await r.json();
      for (const m of messages) {
        show(m);
        after = Math.max(after, m.x_seq);
      }
      status.textContent = typing ? 'typing…' : 'online';
      if (messages.length) chat.scrollTop = chat.scrollHeight;
    } catch {
      status.textContent = 'offline, retrying';
      await new Promise(done => setTimeout(done, 2000));
    }
  }
}

poll();
```

`assistant/static/style.css`:

```css
:root { --bar: #008069; --bg: #efeae2; --in: #fff; --out: #d9fdd3; --ink: #111b21; --muted: #667781;
        --accent: #00a884; --link: #027eb5; --composer: #f0f2f5; --line: rgba(134, 150, 160, .3); }
@media (prefers-color-scheme: dark) {
  :root { --bar: #202c33; --bg: #0b141a; --in: #202c33; --out: #005c4b; --ink: #e9edef; --muted: #8696a0;
          --link: #53bdeb; --composer: #202c33; }
}
* { box-sizing: border-box; }
html, body { height: 100%; margin: 0; }
body { display: flex; flex-direction: column; background: var(--bg); color: var(--ink);
       font: 15px/1.4 system-ui, -apple-system, "Segoe UI", sans-serif; }
header { display: flex; gap: 12px; align-items: center; padding: 10px 16px; background: var(--bar); color: #fff; }
.avatar { width: 40px; height: 40px; border-radius: 50%; display: grid; place-items: center;
          background: var(--accent); font-weight: 600; }
h1 { margin: 0; font-size: 16px; }
#status { margin: 0; font-size: 13px; opacity: .85; }
main { flex: 1; overflow-y: auto; padding: 12px 16px; display: flex; flex-direction: column; gap: 6px; }
.bubble { max-width: min(80%, 520px); padding: 6px 10px 8px; border-radius: 8px; background: var(--in);
          box-shadow: 0 1px .5px rgba(11, 20, 26, .13); overflow-wrap: anywhere; }
.bubble.in { align-self: flex-start; }
.bubble.out { align-self: flex-end; background: var(--out); }
.bubble p { margin: 0; white-space: pre-wrap; }
.title { display: block; color: var(--accent); font-size: 13px; }
.buttons, .rows { display: flex; flex-direction: column; margin: 6px -10px -8px; border-top: 1px solid var(--line); }
.choice { background: none; border: 0; border-bottom: 1px solid var(--line); color: var(--link); padding: 9px 12px;
          font: inherit; cursor: pointer; }
.rows .choice { text-align: left; }
.choice:last-child { border-bottom: 0; }
.choice small { display: block; color: var(--muted); font-size: 13px; }
.choice:disabled { color: var(--muted); cursor: default; }
.choice.picked { font-weight: 600; }
.shot { display: block; max-width: 100%; border-radius: 6px; }
.doc { color: var(--link); }
.note { align-self: center; font-size: 12.5px; color: var(--muted); background: var(--in); padding: 4px 10px;
        border-radius: 8px; }
.failed .error { display: block; color: #d93025; font-size: 12px; }
form { display: flex; gap: 8px; align-items: center; padding: 8px 12px; background: var(--composer); }
.attach { font-size: 20px; cursor: pointer; padding: 4px; border-radius: 50%; }
.wants-file .attach { outline: 2px solid var(--accent); }
#text { flex: 1; min-width: 0; border: 0; border-radius: 8px; padding: 10px 12px; font: inherit; background: var(--in);
        color: var(--ink); }
#send { width: 42px; height: 42px; border: 0; border-radius: 50%; background: var(--accent); color: #fff;
        font-size: 18px; cursor: pointer; }
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -m browser tests/test_chat_page.py -q && uv run pytest -q && node --check assistant/static/app.js && uv run ruff check .`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: WhatsApp-like chat page with buttons, lists, secret input, media, and uploads"
```

---

### Task 8: Chat end to end — two goals in parallel, real agents, real Chrome

**Files:**
- Create: `tests/test_chat_e2e.py`

**Interfaces:**
- Consumes: `Core` (Task 5) with its real `classify`, `start`, `chat`, and `remember` defaults; the real `Agent` (Plan 2) built by a `make_agent` that swaps in `FakeVault`; `jev`, `text_model`, `choice` from `tests/test_shop.py`; `FakeVault` from `tests/fakes.py`; the `shop.html` and `next.html` fixtures.
- Produces: nothing new. This task proves that Tasks 1–5 work together with the real agent. If it fails, the fix goes where the failure is, with a ledger ruling.

- [ ] **Step 1: Write the test** — `tests/test_chat_e2e.py`:

```python
"""Chat end to end in real Chrome: two goals typed into chat run in parallel. The shop order goes through login, a
wrong OTP, an address choice, and the commit gate; the other task saves a page as a PDF that arrives in chat as a
document. Models are scripted behind the real HTTP client, so every request body is checked for leaks.
Run: uv run pytest -m browser"""

import json as jsonlib
import re

import pytest
from fakes import FakeVault
from helpers import until
from test_shop import choice, jev, text_model

from assistant import agent as loop
from assistant import core as conversation
from assistant import model, questions
from assistant.channel_web import WebChannel
from assistant.profile import Profile

pytestmark = pytest.mark.browser


class Response:
    status_code, is_error = 200, False

    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


def webhook(**message):
    value = {"messaging_product": "whatsapp", "messages": [{"from": "local", **message}]}
    return {"object": "whatsapp_business_account",
            "entry": [{"id": "local", "changes": [{"field": "messages", "value": value}]}]}


def tap(question, option):
    return {"type": "interactive", "context": {"id": question["x_id"]}, "interactive": {
        "type": "button_reply", "button_reply": {"id": f"q{question['x_question']}:{option}", "title": option}}}


def test_two_goals_in_chat_run_in_parallel_without_leaks(chrome, site, tmp_path, monkeypatch):
    sent = []

    def jev_chat(body):
        q = body["questions"]
        if "intent" in q:  # every free-text message this test types outside a question is a new goal
            return {"model": "fake-jev", "answers": {"intent": choice(q["intent"]["criteria"], "NEW_TASK"),
                                                     "task": choice(q["task"]["criteria"], "none")}}
        if body["state"]["page"]["title"] == "Next page":
            saved = any(a["operation"] == "SAVE_PDF" for a in body["state"]["recent_actions"])
            return {"model": "fake-jev", "answers": {
                "operation": choice(q["operation"]["criteria"], "DONE" if saved else "SAVE_PDF")}}
        return jev(body)

    def text_chat(system, payload):
        if system == questions.START:
            if "PDF" in payload["goal"]:
                return {"start_url": site["b"] + "/next.html", "title": "Second page PDF"}
            return {"start_url": site["a"] + "/shop.html", "title": "Diet Coke"}
        if system == questions.RESULT and "Second page." in payload["page"]["text"]:
            return {"text": "Saved the second page as a PDF.", "evidence": "Second page."}
        return text_model(system, payload)

    def post(url, json=None, headers=None):  # the keyword httpx uses; the module is jsonlib here
        sent.append(json)
        if "typesafe" in url:
            return Response(jev_chat(json))
        answer = text_chat(json["messages"][0]["content"], jsonlib.loads(json["messages"][1]["content"]))
        return Response({"choices": [{"message": {"content": jsonlib.dumps(answer)}}]})

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(model.CLIENT, "post", post)
    profile = Profile(tmp_path / "profile.json")
    profile.data.update(name="Mayank Churiwal", phone="+91 98765 43210",
                        cards=[{"id": "hdfc", "label": "HDFC Visa", "last4": "1234"}])

    def make_agent(url, goal, title, asker, notify):  # the real Agent, with a fake vault instead of the Keychain
        return loop.Agent(url, goal, title=title, asker=asker, notify=notify, profile=profile,
                          vault=FakeVault({"card.hdfc.cvv": "123"}))

    channel = WebChannel(tmp_path / "media")
    chat = conversation.Core(channel, profile=profile, make_agent=make_agent, poll=0.05)
    count = 0

    def user(**message):
        nonlocal count
        count += 1
        chat.receive_webhook(webhook(id=f"wamid.in{count}", **message))

    def shown(check):
        return until(lambda: next((m for m in channel.since(0)[0] if check(m)), None), timeout=30)

    def buttons(m):
        if m["type"] != "interactive":
            return []
        return [b["reply"]["id"] for b in m["interactive"]["action"].get("buttons", [])]

    user(type="text", text={"body": "Order a Diet Coke"})
    user(type="text", text={"body": "Also save the second page on the other site as a PDF"})
    first = shown(lambda m: m.get("x_secret"))
    assert first, [t.status for t in chat.tasks.values()]
    user(type="text", text={"body": "111111"})
    assert shown(lambda m: m.get("x_secret") and m["x_question"] != first["x_question"])  # the wrong code is re-asked
    user(type="text", text={"body": "482913"})
    address = shown(lambda m: m["type"] == "interactive"
                    and "Deliver to which address?" in m["interactive"]["body"]["text"])
    user(**tap(address, "o1"))
    confirm = shown(lambda m: any(i.endswith(":yes") for i in buttons(m)))
    user(**tap(confirm, "yes"))
    assert until(lambda: {t.status for t in chat.tasks.values()} == {"done"}, timeout=30), [
        (t.title, t.status) for t in chat.tasks.values()]

    messages = channel.since(0)[0]
    texts = " ".join(m["text"]["body"] for m in messages if m["type"] == "text")
    assert "Ordered Diet Coke for ₹79" in texts and "Saved the second page as a PDF." in texts
    [document] = [m["document"] for m in messages if m["type"] == "document"]
    assert document["filename"] == "Next page.pdf"
    assert channel.media(document["id"])[0].read_bytes().startswith(b"%PDF")
    assert chat.open is None and chat.queue == [] and until(lambda: not channel.typing)

    classified = [b["state"]["message"] for b in sent if "intent" in b.get("questions", {})]
    assert classified == ["Order a Diet Coke", "Also save the second page on the other site as a PDF"]
    everything = jsonlib.dumps(sent, ensure_ascii=False)
    for secret in ("98765", "9876543210", "Mayank", "482913", "111111"):
        assert secret not in everything, secret
    assert not re.search(r"(?<!\d)123(?!\d)", everything)
    assert "482913" not in str(chat.log) and "111111" not in str(chat.log)
```

- [ ] **Step 2: Run it**

Run: `uv run pytest -m browser tests/test_chat_e2e.py -q`
Expected: PASS. Tasks 1–5 already hold the behaviour, so this test is not red first: it is the check that they work together. If it fails, find the cause with superpowers:systematic-debugging, write the smallest failing unit test for it in the owning task's test file, fix it there, and ledger a ruling.

- [ ] **Step 3: Run everything**

Run: `uv run pytest -q && uv run pytest -m browser -q && uv run ruff check . && node --check assistant/static/app.js && node --check assistant/snapshot.js`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "test: chat end to end with two parallel tasks, a wrong OTP, the commit gate, and a PDF to chat"
```

---

### Task 9: README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Rewrite the README's sections after the design line**. Replace everything from `## Setup` to the end with:

````markdown
## Setup

    uv sync
    cp .env.example .env      # add TYPESAFE_API_KEY and OPENAI_API_KEY
    uv run jev probe          # optional: check the smoke-set sites for 403s and captchas

## Chat

    uv run jev                # starts the hidden agent Chrome if needed, then serves the chat
                              # open http://127.0.0.1:8766/

Type a goal, such as "Order a Diet Coke on Instamart". The agent works in its own hidden Chrome and asks only for
what it needs, one question at a time: buttons and lists for choices, a hidden field for OTPs and CVVs, and a
summary card before anything is paid for or booked. Up to three tasks run at once (one per site). Messages sent
mid-task count as answers, changes, new tasks, cancellations, or status questions. Files the agent saves, such as
receipts or PDFs, arrive in chat as documents.

The chat uses WhatsApp Cloud API shapes: a webhook comes in, send-message payloads go out, and files travel as
media ids. A WhatsApp channel can therefore replace `assistant/channel_web.py` without touching the conversation core
(`assistant/core.py`). The server listens on 127.0.0.1 only and checks a per-launch token. `JEV_DEBUG=1` adds
`/api/debug` with each task's recent steps.

## One task in the terminal

    uv run jev chrome         # hidden agent Chrome with its own profile
    uv run jev run "Order a Diet Coke on Instamart"

Questions are asked in the terminal; type `cancel` to stop.

## Use the loop

    from assistant.agent import Agent

    with Agent("https://en.wikipedia.org/wiki/Main_Page", "Open the article about Gödel's incompleteness theorems") as agent:
        for state in agent.run():
            print(state["elapsed_ms"], state["status"], state["history"][-1:])

Run with `uv run --env-file .env python script.py` after `uv run jev chrome`. Without an `asker`, goals that need
the user (logins, OTPs, files, approvals) stop at BLOCKED.

## Checks

    uv run ruff check .
    uv run pytest             # offline logic
    uv run pytest -m browser  # real headless Chrome on local fixtures
    node --check assistant/snapshot.js
    node --check assistant/static/app.js
````

- [ ] **Step 2: Check the commands it names exist**

Run: `uv run jev --help && uv run jev run --help`
Expected: both print usage; `jev --help` mentions `chrome`, `probe`, and `run`.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: chat, terminal, and loop usage in the README"
```
