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
        """Keep a file under its own name (sites show and check it), in a private folder of its own."""
        media_id = secrets.token_hex(8)
        folder = self.media_dir / media_id
        folder.mkdir(parents=True, mode=0o700)
        name = Path(filename or "").name
        if name in {"", ".", ".."}:
            name = media_id + (mimetypes.guess_extension(mime) or "")
        path = folder / name
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
                         "context": (m.get("context") or {}).get("id"),
                         "question_id": m.get("x_question_id"), "secret_reply": m.get("x_secret_reply", False)}
                if (event["question_id"] is not None and not isinstance(event["question_id"], str)
                        or type(event["secret_reply"]) is not bool):
                    raise ValueError("invalid question metadata")
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
