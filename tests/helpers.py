"""Lookups shared by the browser tests."""

import time


def find(page, label, kind=None):
    """The first observed action whose label contains `label`, and whose kind matches when given."""
    return next(a for a in page["actions"] if label in a["label"] and (kind is None or a["kind"] == kind))


def log(browser):
    return browser.evaluate("window.log")


def until(check, timeout=3.0):
    """Poll `check` until it returns something truthy; return that value, or the last falsy one."""
    deadline = time.monotonic() + timeout
    while True:
        value = check()
        if value or time.monotonic() > deadline:
            return value
        time.sleep(0.05)


def observe_until(browser, check, timeout=3.0):
    """Observe until `check(page)` holds; return that page, or None on timeout."""

    def attempt():
        page = browser.observe()
        return page if check(page) else None

    return until(attempt, timeout)
