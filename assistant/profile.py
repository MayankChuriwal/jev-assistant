"""The user's profile (profile.json), secrets in the macOS Keychain, and per-task redaction of both."""

import contextvars
import json
import os
import re
import threading
from pathlib import Path

HOME = Path(os.environ.get("JEV_HOME") or Path.home() / ".jev")
HIDDEN = ("name", "phone", "email", "upi_id")  # reach models only as [profile:<key>]
CARD_PARTS = ("number", "expiry", "name", "cvv")
SAVING = threading.Lock()  # parallel tasks share one profile


class Profile:
    """Non-secret facts about the user. Hidden fields reach models only as keys."""

    def __init__(self, path=None):
        self.path = Path(path) if path else HOME / "profile.json"
        stored = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.data = {"name": "", "phone": "", "email": "", "upi_id": "", "addresses": [], "preferences": [],
                     "cards": [], "logins": [], "defaults": {}, **stored}

    def save(self):
        with SAVING:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False))
            tmp.chmod(0o600)
            tmp.replace(self.path)

    def value(self, key):
        if key not in HIDDEN or not self.data.get(key):
            raise KeyError(key)
        return str(self.data[key])

    def vault_keys(self):
        return [f"login.{host}" for host in self.data["logins"]]

    def legacy_card_keys(self) -> list[str]:
        """Local metadata only; never infer deletion accounts from model/page text."""
        return [f"card.{c['id']}.{part}" for c in self.data["cards"] for part in CARD_PARTS]

    def legacy_card_cleanup(self) -> dict | None:
        """Code-owned chat requirement. Core must bind the reply to these exact keys."""
        keys = self.legacy_card_keys()
        if not keys:
            return None
        return {"id": "legacy_card_cleanup", "kind": "confirm", "purpose": "legacy_card_cleanup",
                "keys": keys, "text": "Delete the legacy card details saved in the local Jev vault?"}

    def summary(self):
        """Everything models may see about the user."""
        return {
            "addresses": [{k: a[k] for k in ("label", "text") if k in a} for a in self.data["addresses"]],
            "preferences": [p["text"] for p in self.data["preferences"]],
            "defaults": self.data["defaults"],
            "cards": [],  # legacy local cards are not available payment methods
            "profile_keys": [k for k in HIDDEN if self.data.get(k)],
            "vault_keys": self.vault_keys(),
        }

    def geolocation(self):
        for address in self.data["addresses"]:
            if "lat" in address and "lng" in address:
                return {"latitude": address["lat"], "longitude": address["lng"]}
        return None

    def remember(self, text, task):
        self.data["preferences"].append({"text": text, "from_task": task})
        self.save()

    def set_default(self, key: str, value: object, *, consent: bool) -> None:
        """Save a public, explicitly authorized default; never infer it from history."""
        from .state import _safe

        if consent is not True:
            raise ValueError("saving a default requires explicit consent")
        if (not isinstance(key, str) or not key.strip() or key in HIDDEN
                or _safe({key: value}) != {key: value}):
            raise ValueError("defaults must contain only public JSON data")
        if value is None or value == "":
            raise ValueError("default must be nonempty")
        # Validate serializability before mutating or persisting the profile.
        value = json.loads(json.dumps(value, allow_nan=False))
        self.data["defaults"][key] = {"value": value, "source": "user", "consent": True, "scope": "task"}
        self.save()

    def add_card(self, card_id, label, last4):
        self.data["cards"] = [c for c in self.data["cards"] if c["id"] != card_id]
        self.data["cards"].append({"id": card_id, "label": label, "last4": last4})
        self.save()

    def hidden_values(self):
        """(value, placeholder) pairs a task's redaction registry hides, with common reformattings."""
        pairs = []
        for key in HIDDEN:
            value = str(self.data.get(key) or "").strip()
            if not value:
                continue
            placeholder = f"[profile:{key}]"
            pairs.append((value, placeholder))
            if key == "phone":
                digits = re.sub(r"\D", "", value)
                pairs += [(digits, placeholder), (digits[-10:], placeholder), ("0" + digits[-10:], placeholder)]
            if key == "name":
                pairs += [(part, placeholder) for part in value.split() if len(part) >= 3]
        return pairs


class Vault:
    """Authorized login passwords only; legacy cards require explicit cleanup."""

    SERVICE = "jev"

    def __init__(self, keychain=None):
        # Retain the old constructor signature but never open arbitrary keychains.
        self.keychain = keychain

    def _password(self, operation, key, value=None):
        from .keychain import password
        if self.keychain is not None:
            raise RuntimeError("Keychain unavailable")
        return password(operation, key, value)

    @staticmethod
    def _login(key):
        if not isinstance(key, str) or not re.fullmatch(r"login\.[A-Za-z0-9.-]+(?::[0-9]+)?", key):
            raise KeyError("password namespace unavailable")

    def get(self, key):
        self._login(key)
        try:
            return self._password("get", key)
        except RuntimeError:
            raise KeyError("password unavailable") from None

    def set(self, key, value):
        self._login(key)
        if not isinstance(value, str) or not value or "\n" in value:
            raise ValueError("A secret must be one non-empty line.")
        self._password("set", key, value)

    def delete_legacy_cards(self, keys: list[str], *, consent: bool) -> None:
        if consent is not True:
            raise ValueError("legacy cleanup requires explicit consent")
        if not isinstance(keys, list) or any(not isinstance(key, str) or not re.fullmatch(
                r"card\.[A-Za-z0-9_-]+\.(number|expiry|name|cvv)", key) for key in keys):
            raise ValueError("invalid legacy card accounts")
        for key in dict.fromkeys(keys):
            try:
                self._password("delete", key)
            except KeyError:
                pass  # already absent; never search or delete a broader namespace


SECRETS = contextvars.ContextVar("jev_secrets", default=None)


class Secrets:
    """One task's registry of values no model, log, or message may contain, and the fields that hold them."""

    def __init__(self, pairs=()):
        self.values = {}
        self._owners = {}
        self.nodes = set()  # (frame, node) of fields that received a secret
        for value, placeholder in pairs:
            self.add(value, placeholder)

    def add(self, value, placeholder):
        value = str(value or "").strip()
        if len(value) < 3:
            return
        digits = re.sub(r"[\s-]", "", value)
        if digits.isdigit() and len(digits) >= 4:  # card numbers and phones, in any spacing
            pattern = r"(?<!\d)" + r"[\s().-]*".join(digits) + r"(?!\d)"  # any spacing, brackets, dots, dashes
        else:
            pattern = r"(?<!\w)" + re.escape(value) + r"(?!\w)"
        owners = self._owners.setdefault(value, [])
        if placeholder not in owners:
            owners.append(placeholder)
        self.values[value] = (re.compile(pattern, re.IGNORECASE), placeholder)

    def remove(self, placeholder: str) -> None:
        """After sanitizing all retained records, forget this placeholder's copies.

        The task owner must sanitize model/error/history buffers before calling.
        Other placeholders stay registered, including overlapping values.
        """
        for value, owners in list(self._owners.items()):
            if placeholder in owners:
                owners.remove(placeholder)
            if owners:
                self.values[value] = (self.values[value][0], owners[-1])
            else:
                del self.values[value]
                del self._owners[value]

    def text(self, s):
        if s.startswith("data:image/"):
            return s
        for value in sorted(self.values, key=len, reverse=True):
            regex, placeholder = self.values[value]
            s = regex.sub(lambda _match: placeholder, s)
        return s

    def redact(self, obj):
        if isinstance(obj, str):
            return self.text(obj)
        if isinstance(obj, list):
            return [self.redact(item) for item in obj]
        if isinstance(obj, dict):
            return {key: self.redact(value) for key, value in obj.items()}
        return obj
