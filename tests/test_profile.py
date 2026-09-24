"""Profile, vault, and redaction contracts. The vault test uses a throwaway keychain, never the login keychain."""

import json
import stat
import threading

import pytest

from assistant.profile import Profile, Vault


@pytest.fixture
def profile(tmp_path):
    p = Profile(tmp_path / "profile.json")
    p.data.update(name="Mayank Churiwal", phone="+91 98765 43210", email="m@example.com",
                  addresses=[{"label": "Home", "text": "12 MG Road, Bengaluru", "lat": 12.97, "lng": 77.6}],
                  cards=[{"id": "hdfc", "label": "HDFC Visa", "last4": "1234"}], logins=["www.swiggy.com"])
    return p


def test_models_see_addresses_and_labels_but_only_names_of_hidden_values(profile):
    summary = profile.summary()
    assert summary["addresses"] == [{"label": "Home", "text": "12 MG Road, Bengaluru"}]
    assert summary["cards"] == []
    assert summary["profile_keys"] == ["name", "phone", "email"]
    assert summary["vault_keys"] == ["login.www.swiggy.com"]
    assert "98765" not in json.dumps(summary) and "Mayank" not in json.dumps(summary)


def test_hidden_values_cover_common_reformattings(profile):
    pairs = dict(profile.hidden_values())
    assert pairs["+91 98765 43210"] == "[profile:phone]"
    assert pairs["919876543210"] == pairs["9876543210"] == "[profile:phone]"
    assert pairs["Mayank"] == pairs["Churiwal"] == "[profile:name]"
    assert pairs["m@example.com"] == "[profile:email]"


def test_values_are_read_only_for_set_hidden_fields(profile):
    assert profile.value("phone") == "+91 98765 43210"
    for key in ("upi_id", "addresses", "cards"):
        with pytest.raises(KeyError):
            profile.value(key)


def test_saves_privately_and_round_trips(profile):
    profile.remember("Default Instamart address: Home", "Order Diet Coke")
    profile.add_card("icici", "ICICI Amex", "9876")
    mode = stat.S_IMODE(profile.path.stat().st_mode)
    assert mode == 0o600
    again = Profile(profile.path)
    assert again.data["preferences"] == [{"text": "Default Instamart address: Home", "from_task": "Order Diet Coke"}]
    assert [c["id"] for c in again.data["cards"]] == ["hdfc", "icici"]
    assert again.geolocation() == {"latitude": 12.97, "longitude": 77.6}


def test_a_missing_file_is_an_empty_profile(tmp_path):
    p = Profile(tmp_path / "none.json")
    assert p.summary()["profile_keys"] == [] and p.geolocation() is None and p.hidden_values() == []


from assistant.profile import Secrets  # noqa: E402


def test_digit_secrets_are_hidden_in_any_spacing():
    s = Secrets([("4111 1111 1111 1111", "[secret:card.number]")])
    for shown in ("4111111111111111", "4111-1111-1111-1111", "card 4111 1111 1111 1111 ok"):
        assert "4111" not in s.text(shown) and "[secret:card.number]" in s.text(shown)
    assert s.text("order 94111111111111119") == "order 94111111111111119"  # longer digit runs are other numbers


def test_words_are_hidden_whole_and_case_insensitively():
    s = Secrets([("Mayank", "[profile:name]"), ("m@example.com", "[profile:email]"), ("ab", "[ignored]")])
    assert s.text("Hi mayank, mail m@example.com") == "Hi [profile:name], mail [profile:email]"
    assert s.text("Mayankpur") == "Mayankpur" and "ab" in s.text("abc ab")


def test_redaction_is_deep_and_leaves_images_alone():
    s = Secrets([("482913", "[secret:otp]")])
    body = {"a": ["code 482913", {"b": "482913"}], "n": 482913, "img": "data:image/png;base64,482913AAAA"}
    assert s.redact(body) == {"a": ["code [secret:otp]", {"b": "[secret:otp]"}], "n": 482913,
                              "img": "data:image/png;base64,482913AAAA"}


def test_phone_numbers_are_hidden_in_local_formats(profile):
    s = Secrets(profile.hidden_values())
    for shown in ("(98765) 43210", "098765 43210", "+91-98765-43210", "98765.43210"):
        assert "98765" not in s.text(f"call {shown} now"), shown


def test_parallel_saves_never_collide(tmp_path):
    p, errors = Profile(tmp_path / "profile.json"), []

    def save_many():
        try:
            for _ in range(40):
                p.save()
        except Exception as error:
            errors.append(error)

    threads = [threading.Thread(target=save_many) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == [] and json.loads((tmp_path / "profile.json").read_text())["cards"] == []


def test_explicit_default_requires_consent_and_roundtrips(tmp_path):
    profile = Profile(tmp_path / "profile.json")
    with pytest.raises(ValueError, match="consent"):
        profile.set_default("provider", "Chosen shop", consent=False)
    profile.set_default("provider", "Chosen shop", consent=True)
    saved = Profile(profile.path)
    assert saved.summary()["defaults"]["provider"] == {
        "value": "Chosen shop", "source": "user", "consent": True, "scope": "task"}
    saved.remember("Used Old shop last time", "t1")
    assert saved.summary()["defaults"]["provider"]["value"] == "Chosen shop"


def test_defaults_cannot_persist_secrets(tmp_path):
    profile = Profile(tmp_path / "profile.json")
    with pytest.raises(ValueError):
        profile.set_default("password", "sensitive", consent=True)
    with pytest.raises(ValueError):
        profile.set_default("provider", {"otp": "123456"}, consent=True)


def test_hidden_profile_fields_cannot_be_exposed_as_plain_defaults(tmp_path):
    profile = Profile(tmp_path / "profile.json")
    with pytest.raises(ValueError):
        profile.set_default("phone", "+91 9876543210", consent=True)
    assert not profile.data["defaults"]


def test_vault_namespaces_and_explicit_legacy_cleanup(profile, monkeypatch):
    from assistant import keychain
    calls = []
    monkeypatch.setattr(keychain, "password", lambda *args: calls.append(args) or "秘密")
    vault = Vault()
    assert vault.get("login.a.test") == "秘密"
    vault.set("login.a.test", "秘密")
    for op in (lambda: vault.get("card.hdfc.cvv"), lambda: vault.set("card.hdfc.cvv", "123")):
        with pytest.raises(KeyError):
            op()
    keys = profile.legacy_card_keys()
    with pytest.raises(ValueError):
        vault.delete_legacy_cards(keys, consent=False)
    with pytest.raises(ValueError):
        vault.delete_legacy_cards([*keys, "login.a.test"], consent=True)
    assert len(calls) == 2
    vault.delete_legacy_cards(keys, consent=True)
    assert calls[2:] == [("delete", key, None) for key in keys]


def test_redaction_cleanup_preserves_other_records():
    registry = Secrets([("4111111111111111", "[secret:card]"), ("123", "[secret:cvv]"),
                        ("秘密pass", "[secret:password]"), ("654321", "[secret:otp]")])
    record = registry.redact({"history": ["4111111111111111 123 秘密pass 654321"]})
    registry.remove("[secret:card]")
    assert "4111111111111111" not in str(record)
    assert registry.text("123 秘密pass 654321") == "[secret:cvv] [secret:password] [secret:otp]"


def test_vault_denial_becomes_missing_password(monkeypatch):
    from assistant import keychain
    def denied(*args):
        raise RuntimeError("Keychain unavailable")
    monkeypatch.setattr(keychain, "password", denied)
    with pytest.raises(KeyError, match="unavailable"):
        Vault().get("login.a.test")


def test_legacy_cleanup_is_specific_local_confirmation(profile):
    requirement = profile.legacy_card_cleanup()
    assert requirement["id"] == requirement["purpose"] == "legacy_card_cleanup"
    assert requirement["keys"] == profile.legacy_card_keys()
    assert len(requirement["keys"]) == 4
    assert profile.data["cards"]  # enumerating or declining does not delete metadata


def test_removing_a_shared_value_keeps_older_placeholder_redaction():
    registry = Secrets([("same秘密", "[secret:old]"), ("same秘密", "[secret:new]")])
    registry.remove("[secret:new]")
    assert registry.text("same秘密") == "[secret:old]"
    registry.remove("[secret:old]")
    assert registry.text("same秘密") == "same秘密"
