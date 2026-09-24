"""Only fake native handles: no access to any real Keychain."""
import ctypes as ct

import pytest

from assistant import keychain as kc


class Native:
    def __init__(self):
        self.events = []
        self.values = {b"login.a.test": "秘密password".encode()}
        self.suppress_status = 0
        self.find_status = 0
        self.buffer = None

    def SecKeychainGetUserInteractionAllowed(self, output):
        output._obj.value = True
        self.events.append("previous")
        return 0

    def SecKeychainSetUserInteractionAllowed(self, value):
        self.events.append(("interaction", value))
        return self.suppress_status if not value else 0

    def SecKeychainFindGenericPassword(self, chain, sl, service, al, account, length, data, item):
        assert chain is None and service == b"jev" and sl == 3 and al == len(account)
        self.events.append(("find", account))
        self.account = account
        if self.find_status:
            return self.find_status
        if account not in self.values:
            return -25300
        raw = self.values[account]
        self.buffer = ct.create_string_buffer(raw)
        length._obj.value = len(raw)
        data._obj.value = ct.addressof(self.buffer)
        item._obj.value = 42
        return 0

    def SecKeychainAddGenericPassword(self, chain, sl, service, al, account, length, data, item):
        assert service == b"jev"
        self.events.append(("add", account))
        self.values[account] = ct.string_at(data, length)
        item._obj.value = 42
        return 0

    def SecKeychainItemModifyAttributesAndData(self, item, attrs, length, data):
        self.events.append("modify")
        self.values[self.account] = ct.string_at(data, length)
        return 0

    def SecKeychainItemDelete(self, item):
        self.events.append("delete")
        del self.values[self.account]
        return 0

    def SecKeychainItemFreeContent(self, attrs, data):
        self.events.append("free")
        return 0

    def CFRelease(self, item):
        self.events.append("release")


@pytest.fixture
def native(monkeypatch):
    fake = Native()
    monkeypatch.setattr(kc, "security", fake)
    monkeypatch.setattr(kc, "corefoundation", fake)
    return fake


@pytest.mark.parametrize("operation", ["get", "set", "delete"])
def test_suppression_restoration_and_resource_release(native, operation):
    result = kc.password(operation, "login.a.test", "new秘密" if operation == "set" else None)
    if operation == "get":
        assert result == "秘密password"
    assert native.events[:3] == ["previous", ("interaction", False), ("find", b"login.a.test")]
    assert native.events[-3:] == ["free", "release", ("interaction", True)]


def test_add_missing_account_and_exact_delete(native):
    kc.password("set", "login.b.test", "CVV123秘密")
    kc.password("delete", "login.b.test")
    assert list(native.values) == [b"login.a.test"]


def test_suppression_failure_prevents_access(native):
    native.suppress_status = -1
    with pytest.raises(RuntimeError, match="unavailable"):
        kc.password("get", "login.a.test")
    assert native.events == ["previous", ("interaction", False)]


@pytest.mark.parametrize("status,exception", [(-25300, KeyError), (-25308, RuntimeError)])
def test_failures_restore_interaction_without_secret_errors(native, status, exception):
    native.find_status = status
    with pytest.raises(exception) as error:
        kc.password("get", "login.a.test")
    assert "秘密" not in str(error.value)
    assert native.events[-1] == ("interaction", True)


def test_native_exception_still_restores_and_releases(native, monkeypatch):
    def fails(*args):
        raise RuntimeError("native unavailable")
    monkeypatch.setattr(native, "SecKeychainItemDelete", fails)
    with pytest.raises(RuntimeError):
        kc.password("delete", "login.a.test")
    assert native.events[-3:] == ["free", "release", ("interaction", True)]
