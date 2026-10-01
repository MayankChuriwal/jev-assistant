"""The user's profile (profile.json), secrets in the macOS Keychain, and per-task redaction of both."""

import json
import os
import subprocess
import threading
from pathlib import Path

from .privacy import variants

HOME = Path(os.environ.get("JEV_HOME") or Path.home() / ".jev")
HIDDEN = ("name", "phone", "email", "upi_id")  # reach models only as [profile:<key>]
CARD_PARTS = ("number", "expiry", "name", "cvv")
KEYCHAIN_TIMEOUT = 60  # seconds for the user to answer a Keychain unlock prompt
SAVING = threading.Lock()  # parallel tasks share one profile


class Profile:
    """Non-secret facts about the user. Hidden fields reach models only as keys."""

    def __init__(self, path=None):
        self.path = Path(path) if path else HOME / "profile.json"
        stored = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.data = {"name": "", "phone": "", "email": "", "upi_id": "", "addresses": [], "preferences": [],
                     "cards": [], "logins": [], **stored}

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
        keys = [f"card.{c['id']}.{part}" for c in self.data["cards"] for part in CARD_PARTS]
        return keys + [f"login.{host}" for host in self.data["logins"]]

    def summary(self):
        """Everything models may see about the user."""
        return {
            "addresses": [{k: a[k] for k in ("label", "text") if k in a} for a in self.data["addresses"]],
            "preferences": [p["text"] for p in self.data["preferences"]],
            "cards": [{k: c[k] for k in ("id", "label", "last4")} for c in self.data["cards"]],
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

    def add_card(self, card_id, label, last4):
        self.data["cards"] = [c for c in self.data["cards"] if c["id"] != card_id]
        self.data["cards"].append({"id": card_id, "label": label, "last4": last4})
        self.save()

    def hidden_values(self):
        """(value, placeholder) pairs a task's redaction registry hides, with common reformattings."""
        return [pair for key in HIDDEN for pair in variants(key, self.data.get(key))]


def quote(text):
    """A double-quoted word for `security -i`, which splits its input like a shell."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


class Vault:
    """Card and login secrets in the macOS Keychain (service "jev"). Secrets travel on stdin, never in argv."""

    SERVICE = "jev"

    def __init__(self, keychain=None):
        self.keychain = str(keychain) if keychain else None

    def get(self, key):
        args = ["security", "find-generic-password", "-s", self.SERVICE, "-a", key, "-w"]
        try:
            result = subprocess.run(args + ([self.keychain] if self.keychain else []), capture_output=True,
                                    text=True, timeout=KEYCHAIN_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise KeyError(key) from None  # a locked Keychain: the agent asks for the value in chat instead
        if result.returncode:
            raise KeyError(key)
        return result.stdout.removesuffix("\n")

    def set(self, key, value):
        if not value or "\n" in value:
            raise ValueError("A secret must be one non-empty line.")
        command = f"add-generic-password -U -s {self.SERVICE} -a {quote(key)} -w {quote(value)}"
        if self.keychain:
            command += " " + quote(self.keychain)
        try:
            subprocess.run(["security", "-i"], input=command + "\n", capture_output=True, text=True,
                           timeout=KEYCHAIN_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"The Keychain did not answer (is it locked?); {key} was not stored.") from None
        # `security -i` exits 0 even when a command fails: read the value back.
        try:
            stored = self.get(key)
        except KeyError:
            stored = None
        if stored != value:
            raise RuntimeError(f"The Keychain did not store {key}.")
