"""Profile, vault, and redaction contracts. The vault test uses a throwaway keychain, never the login keychain."""

import json
import stat

import pytest

from assistant.profile import Profile


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
