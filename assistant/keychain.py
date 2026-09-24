"""Noninteractive, process-local macOS generic-password boundary.

Deprecated Security APIs are isolated here; unavailable platforms fail closed.
No subprocess, unlock operation, or fallback to an interactive command is allowed.
"""
import ctypes as ct
import sys
import threading

KEYCHAIN_LOCK = threading.Lock()
security = corefoundation = None


def _load():
    global security, corefoundation
    if security is not None:
        return
    if sys.platform != "darwin":
        raise RuntimeError("Keychain unavailable")
    try:
        sec = ct.CDLL("/System/Library/Frameworks/Security.framework/Security")
        cf = ct.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        signatures = {
            "SecKeychainGetUserInteractionAllowed": [ct.POINTER(ct.c_bool)],
            "SecKeychainSetUserInteractionAllowed": [ct.c_bool],
            "SecKeychainFindGenericPassword": [ct.c_void_p, ct.c_uint32, ct.c_char_p, ct.c_uint32,
                                               ct.c_char_p, ct.POINTER(ct.c_uint32),
                                               ct.POINTER(ct.c_void_p), ct.POINTER(ct.c_void_p)],
            "SecKeychainAddGenericPassword": [ct.c_void_p, ct.c_uint32, ct.c_char_p, ct.c_uint32,
                                              ct.c_char_p, ct.c_uint32, ct.c_void_p,
                                              ct.POINTER(ct.c_void_p)],
            "SecKeychainItemModifyAttributesAndData": [ct.c_void_p, ct.c_void_p, ct.c_uint32, ct.c_void_p],
            "SecKeychainItemDelete": [ct.c_void_p],
            "SecKeychainItemFreeContent": [ct.c_void_p, ct.c_void_p],
        }
        for name, args in signatures.items():
            function = getattr(sec, name)
            function.argtypes, function.restype = args, ct.c_int32
        cf.CFRelease.argtypes, cf.CFRelease.restype = [ct.c_void_p], None
        security, corefoundation = sec, cf
    except (OSError, AttributeError):
        raise RuntimeError("Keychain unavailable") from None


def checked(status: int) -> None:
    if status == -25300:
        raise KeyError("password unavailable")
    if status:
        raise RuntimeError("Keychain unavailable")


def perform_password_operation(operation, key, value):
    service, account = b"jev", key.encode("utf-8")
    length, data, item = ct.c_uint32(), ct.c_void_p(), ct.c_void_p()
    try:
        status = security.SecKeychainFindGenericPassword(
            None, len(service), service, len(account), account,
            ct.byref(length), ct.byref(data), ct.byref(item))
        if status == -25300 and operation == "set":
            raw = value.encode("utf-8")
            checked(security.SecKeychainAddGenericPassword(
                None, len(service), service, len(account), account, len(raw), raw, ct.byref(item)))
            return None
        checked(status)
        if operation == "get":
            try:
                return ct.string_at(data, length.value).decode("utf-8")
            except UnicodeError:
                raise RuntimeError("Keychain value unavailable") from None
        if operation == "set":
            raw = value.encode("utf-8")
            checked(security.SecKeychainItemModifyAttributesAndData(item, None, len(raw), raw))
        else:
            checked(security.SecKeychainItemDelete(item))
        return None
    finally:
        try:
            if data.value:
                checked(security.SecKeychainItemFreeContent(None, data))
        finally:
            if item.value:
                corefoundation.CFRelease(item)


def password(operation: str, key: str, value: str | None = None) -> str | None:
    if operation not in {"get", "set", "delete"} or not isinstance(key, str) or not key:
        raise RuntimeError("invalid Keychain operation")
    if operation == "set" and (not isinstance(value, str) or not value):
        raise RuntimeError("invalid Keychain value")
    with KEYCHAIN_LOCK:
        _load()
        previous = ct.c_bool()
        checked(security.SecKeychainGetUserInteractionAllowed(ct.byref(previous)))
        checked(security.SecKeychainSetUserInteractionAllowed(False))
        try:
            return perform_password_operation(operation, key, value)
        finally:
            checked(security.SecKeychainSetUserInteractionAllowed(previous.value))
