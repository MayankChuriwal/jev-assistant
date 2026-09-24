"""Profile, vault, and redaction contracts. The vault test uses a throwaway keychain, never the login keychain."""

import json
import stat
import subprocess
import threading

import pytest

from assistant import profile as profiles
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
    assert summary["cards"] == [{"id": "hdfc", "label": "HDFC Visa", "last4": "1234"}]
    assert summary["profile_keys"] == ["name", "phone", "email"]
    assert "card.hdfc.cvv" in summary["vault_keys"] and "login.www.swiggy.com" in summary["vault_keys"]
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


import sys  # noqa: E402

mac = pytest.mark.skipif(sys.platform != "darwin", reason="macOS Keychain")


@pytest.fixture
def keychain(tmp_path):
    path = tmp_path / "test.keychain-db"
    subprocess.run(["security", "create-keychain", "-p", "test", str(path)], check=True)
    subprocess.run(["security", "unlock-keychain", "-p", "test", str(path)], check=True)
    yield path
    subprocess.run(["security", "delete-keychain", str(path)], check=False)


@mac
def test_vault_round_trips_awkward_secrets_without_argv(keychain, monkeypatch):
    seen = []
    real = subprocess.run

    def spy(args, **kwargs):
        seen.append((list(args), kwargs.get("input")))
        return real(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy)
    vault = Vault(keychain)
    vault.set("card.hdfc.cvv", "1 2\"3'\\")
    assert vault.get("card.hdfc.cvv") == "1 2\"3'\\"
    vault.set("card.hdfc.cvv", "456")
    assert vault.get("card.hdfc.cvv") == "456"
    assert not any("456" in " ".join(args) for args, _ in seen)  # secrets never reach process arguments


@mac
def test_vault_reports_missing_and_rejects_bad_values(keychain):
    vault = Vault(keychain)
    with pytest.raises(KeyError):
        vault.get("card.none.cvv")
    for bad in ("", "two\nlines"):
        with pytest.raises(ValueError):
            vault.set("card.hdfc.cvv", bad)


@mac
def test_vault_notices_a_store_that_did_not_happen(tmp_path):
    with pytest.raises(RuntimeError, match="did not store"):
        Vault(tmp_path / "missing.keychain-db").set("card.hdfc.cvv", "123")


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


def test_a_locked_keychain_never_hangs_the_task(monkeypatch):
    seen = []

    def hangs(args, **options):
        seen.append(options.get("timeout"))
        raise subprocess.TimeoutExpired(args, options.get("timeout"))

    monkeypatch.setattr(profiles.subprocess, "run", hangs)
    with pytest.raises(KeyError):
        Vault().get("card.hdfc.cvv")  # the agent then asks for the value in chat instead
    with pytest.raises(RuntimeError, match="Keychain"):
        Vault().set("card.hdfc.cvv", "123")
    assert seen == [profiles.KEYCHAIN_TIMEOUT] * 2


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
