"""The mini + Jev loop in a real headless Chrome with scripted models: sign in with a code, choose the address,
reach the payment approval, and stop at NO. Run: uv run pytest -m browser"""

import pytest
from fakes import jev_fake, out, script

from assistant import driver
from assistant.agent import STOPPED
from assistant.profile import Profile

pytestmark = pytest.mark.browser


def test_sign_in_checkout_and_a_declined_payment(chrome, site, tmp_path, monkeypatch):
    replies = iter([{"text": "482913"}, {"text": "home"}, {"text": "no, stop"}])
    script(monkeypatch,
           out("TYPE", "Phone number", text="[profile:phone]"),
           out("CLICK", "Continue"),
           out("ASK", message="Please send the 6-digit code the shop sent you."),
           out("TYPE", "One-time code", text="[secret:answer1]"),
           out("CLICK", "Verify"),
           out("ASK", message="Which address?", options=["Home — 12 MG Road", "Office — 4 Residency Road"]),
           out("CLICK", "Home — 12 MG Road"),
           out("CLICK", "Pay ₹79", commits=True, message="Diet Coke 300 ml · Home · ₹79"),
           out("STOP", message="You said no, so nothing was paid."))
    jev_fake(monkeypatch, replies=["1"], approvals=["no"])
    profile = Profile(tmp_path / "profile.json")
    profile.data["phone"] = "9876543210"
    with driver.MiniAgent(site["a"] + "/shop.html", "Order Diet Coke", asker=lambda m: next(replies),
                          profile=profile, trace_dir=tmp_path) as a:
        for _ in range(12):
            if a.state["status"] in STOPPED:
                break
            a.command("tick")
        typed = [h["text"] for h in a.state["history"] if h["operation"] == "TYPE_TEXT"]
        assert typed == ["[profile:phone]", "[secret:answer1]"], a.state["feedback"]
        assert "Home — 12 MG Road" in [h["action"] for h in a.state["history"]]
        assert not any(h.get("committed") for h in a.state["history"])
        assert a.state["status"] == "blocked"
