"""Agent messages to WhatsApp Cloud API payloads within its limits, and replies back to agent replies."""

TEXT_LIMIT, BODY_LIMIT = 4096, 1024
BUTTONS, BUTTON_TITLE = 3, 20
ROWS, ROW_TITLE, ROW_DESCRIPTION = 10, 24, 72


def short(label, limit):
    """A label within `limit` characters, cut at a word with “…” (live, a pill read “A credit enquiry I d”)."""
    if len(label) <= limit:
        return label
    cut = label[:limit - 1]
    return (cut.rsplit(" ", 1)[0] if " " in cut.strip() else cut).rstrip(" ,.;:-") + "…"


def text_payloads(text):
    return [{"type": "text", "text": {"body": text[i:i + TEXT_LIMIT]}} for i in range(0, len(text) or 1, TEXT_LIMIT)]


def render(message, question_id=None, image_id=None, title=None, tag=None):
    """Payloads for one agent message. Options become reply buttons (≤ 3), a list (≤ 10), or numbered text. Their ids
    start with the question's (q7) or, for a brain reply that opens no question, its `tag` (b7)."""
    text = f"*{title}*\n{message['text']}" if title else message["text"]
    tag = tag or f"q{question_id}"
    options = message.get("options") or []
    payloads = [{"type": "image", "image": {"id": image_id}}] if image_id else []
    if options and len(options) <= ROWS:
        body = text
        if len(text) > BODY_LIMIT:
            payloads += text_payloads(text)
            body = "Choose one:"
        if len(options) <= BUTTONS:
            action = {"buttons": [{"type": "reply", "reply": {"id": f"{tag}:{o['id']}",
                                                             "title": short(o["label"], BUTTON_TITLE)}} for o in options]}
            kind = "button"
        else:
            rows = [{"id": f"{tag}:{o['id']}", "title": short(o["label"], ROW_TITLE),
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
