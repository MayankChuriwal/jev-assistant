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

    def showing(self):
        return {"id": 7, "secret": True}

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
    assert chat.client.get("/outbox").json() == {"messages": [], "typing": False, "open": {"id": 7, "secret": True}}
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


def test_one_named_public_origin_may_post_and_no_other(chat, monkeypatch):
    monkeypatch.setenv("JEV_PUBLIC_ORIGIN", "https://abc.ngrok-free.app")
    ok = chat.client.post("/webhook", json=WEBHOOK, headers={"Origin": "https://abc.ngrok-free.app"})
    assert ok.json() == {"ok": True}
    bad = chat.client.post("/webhook", json=WEBHOOK, headers={"Origin": "https://evil.ngrok-free.app"})
    assert bad.status_code == 403
