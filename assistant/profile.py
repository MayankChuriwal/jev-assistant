"""The user's profile (profile.json), secrets in the macOS Keychain, and per-task redaction of both."""

import contextvars
import json
import os
import re
import subprocess
from pathlib import Path

HOME = Path(os.environ.get("JEV_HOME") or Path.home() / ".jev")
HIDDEN = ("name", "phone", "email", "upi_id")  # reach models only as [profile:<key>]
CARD_PARTS = ("number", "expiry", "name", "cvv")


class Profile:
    """Non-secret facts about the user. Hidden fields reach models only as keys."""

    def __init__(self, path=None):
        self.path = Path(path) if path else HOME / "profile.json"
        stored = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.data = {"name": "", "phone": "", "email": "", "upi_id": "", "addresses": [], "preferences": [],
                     "cards": [], "logins": [], **stored}

    def save(self):
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
        result = subprocess.run(args + ([self.keychain] if self.keychain else []), capture_output=True, text=True)
        if result.returncode:
            raise KeyError(key)
        return result.stdout.removesuffix("\n")

    def set(self, key, value):
        if not value or "\n" in value:
            raise ValueError("A secret must be one non-empty line.")
        command = f"add-generic-password -U -s {self.SERVICE} -a {quote(key)} -w {quote(value)}"
        if self.keychain:
            command += " " + quote(self.keychain)
        subprocess.run(["security", "-i"], input=command + "\n", capture_output=True, text=True)
        # `security -i` exits 0 even when a command fails: read the value back.
        try:
            stored = self.get(key)
        except KeyError:
            stored = None
        if stored != value:
            raise RuntimeError(f"The Keychain did not store {key}.")


SECRETS = contextvars.ContextVar("jev_secrets", default=None)


class Secrets:
    """One task's registry of values no model, log, or message may contain, and the fields that hold them."""

    def __init__(self, pairs=()):
        self.values = {}
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
        self.values[value] = (re.compile(pattern, re.IGNORECASE), placeholder)

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
