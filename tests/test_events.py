"""Dialogs, pickers, downloads, PDFs, pop-ups, tabs, navigation, permissions. Run: uv run pytest -m browser"""

import time
from pathlib import Path

import pytest
from helpers import find, log, observe_until, until

pytestmark = pytest.mark.browser


def test_alerts_are_accepted_and_reported(browser_at):
    b = browser_at("/events.html")
    page = b.observe()
    b.act("CLICK", find(page, "Save"), page)
    page = b.observe()
    assert "Alert: Saved!" in page["text"] and log(b) == ["alerted"]


def test_confirm_dialogs_become_the_only_choices(browser_at):
    b = browser_at("/events.html")
    page = b.observe()
    b.act("CLICK", find(page, "Cancel order"), page)
    page = b.observe()
    assert page["dialog"] == {"type": "confirm", "message": "Cancel order?"}
    assert [a["kind"] for a in page["actions"]] == ["accept_dialog", "dismiss_dialog"]
    b.act("DISMISS_DIALOG", find(page, "Cancel", "dismiss_dialog"), page)
    b.observe()
    assert log(b) == ["confirm:false"]


def test_enter_that_opens_a_confirm_returns_quickly(browser_at):
    b = browser_at("/events.html")
    page = b.observe()
    b.act("TYPE_TEXT", find(page, "Complaint", "fill"), page, text="Late order")
    page = b.observe()
    started = time.monotonic()
    b.act("PRESS_KEY", find(page, "Complaint", "fill"), page, key="Enter")
    assert time.monotonic() - started < 3.5
    page = b.observe()
    assert page["dialog"]["message"] == "Send complaint?"
    b.act("ACCEPT_DIALOG", find(page, "OK", "accept_dialog"), page)
    b.observe()
    assert log(b) == ["submit:true"]


def test_file_pickers_opened_by_buttons_become_uploads(browser_at, tmp_path):
    statement = tmp_path / "statement.pdf"
    statement.write_bytes(b"%PDF-1.4 statement")
    b = browser_at("/upload.html")
    page = b.observe()
    b.act("CLICK", find(page, "Choose statement"), page)
    page = observe_until(b, lambda p: any(a.get("chooser") for a in p["actions"]))
    b.act("UPLOAD", find(page, "Open file picker", "upload"), page, files=[str(statement)])
    assert b.evaluate("document.getElementById('statement-name').textContent") == "statement.pdf · 18 B"
    assert not any(a.get("chooser") for a in b.observe()["actions"])


def test_downloads_arrive_with_safe_names(browser_at):
    b = browser_at("/events.html")
    page = b.observe()
    b.act("CLICK", find(page, "Download invoice"), page)
    files = until(lambda: b.observe()["downloads"])
    assert [Path(f).name for f in files] == ["invoice.txt"] and Path(files[0]).read_bytes() == b"Invoice 123"
    b.evaluate("document.body.insertAdjacentHTML('beforeend', "
               "'<a href=\"/download/..%2F..%2Fevil.txt\">Hostile file</a>')")
    page = b.observe()
    b.act("CLICK", find(page, "Hostile file"), page)
    files = until(lambda: b.observe()["downloads"])
    assert Path(files[0]).parent == b.downloads_dir and Path(files[0]).name.endswith("evil.txt")


def test_save_pdf_writes_a_pdf(browser_at):
    b = browser_at("/events.html")
    page = b.observe()
    result = b.act("SAVE_PDF", find(page, "Save page as PDF", "save_pdf"), page)
    assert Path(result["file"]).read_bytes().startswith(b"%PDF")
    assert result["file"] in b.observe()["downloads"]


def test_popups_are_followed_and_tabs_switch_and_close(browser_at):
    b = browser_at("/events.html")
    page = b.observe()
    b.act("CLICK", find(page, "Open help"), page)
    page = observe_until(b, lambda p: p["title"] == "Help center")
    tabs = [a for a in page["actions"] if a["kind"] == "tab"]
    assert len(tabs) == 2 and [t["current"] for t in tabs] == [False, True]
    b.act("SWITCH_TAB", tabs[0], page)
    page = b.observe()
    assert page["title"] == "Events fixture"
    popup = next(a for a in page["actions"] if a["kind"] == "tab" and not a["current"])
    b.act("CLOSE_TAB", popup, page)
    page = b.observe()
    assert page["title"] == "Events fixture" and not any(a["kind"] == "tab" for a in page["actions"])


def test_a_popup_that_closes_itself_returns_to_its_opener(browser_at):
    b = browser_at("/events.html")
    page = b.observe()
    b.act("CLICK", find(page, "Open help"), page)
    page = observe_until(b, lambda p: p["title"] == "Help center")
    b.act("CLICK", find(page, "Close help"), page)
    assert until(lambda: b.observe()["title"] == "Events fixture")


def test_history_reload_and_open_url(browser_at, site):
    b = browser_at("/ops.html")
    page = b.observe()
    assert not any(a["kind"] in {"go_back", "go_forward"} for a in page["actions"])
    b.act("CLICK", find(page, "Next page"), page)
    page = observe_until(b, lambda p: p["title"] == "Next page")
    b.act("GO_BACK", find(page, "Back to", "go_back"), page)
    page = b.observe()
    assert page["title"] == "Ops fixture"
    b.act("GO_FORWARD", find(page, "Forward to", "go_forward"), page)
    page = b.observe()
    assert page["title"] == "Next page"
    b.act("RELOAD", find(page, "Reload", "reload"), page)
    page = b.observe()
    assert page["title"] == "Next page"
    b.act("OPEN_URL", find(page, "Open a web address", "open_url"), page, url=site["b"] + "/events.html")
    page = b.observe()
    assert page["url"].startswith(site["b"]) and page["title"] == "Events fixture"
    with pytest.raises(ValueError, match="http"):
        b.act("OPEN_URL", find(page, "Open a web address", "open_url"), page, url="javascript:alert(1)")


def test_permission_prompts_are_denied_without_asking(browser_at):
    b = browser_at("/events.html")
    states = b.evaluate("Promise.all(['geolocation', 'notifications'].map(n => "
                        "navigator.permissions.query({name: n}).then(p => p.state)))")
    assert states == ["denied", "denied"]


def test_downloads_still_arrive_after_a_popup_closes(browser_at):
    b = browser_at("/events.html")
    page = b.observe()
    b.act("CLICK", find(page, "Open help"), page)
    page = observe_until(b, lambda p: p["title"] == "Help center")
    b.act("CLOSE_TAB", next(a for a in page["actions"] if a["kind"] == "tab" and a["current"]), page)
    page = observe_until(b, lambda p: p["title"] == "Events fixture")
    b.act("CLICK", find(page, "Download invoice"), page)
    files = until(lambda: b.observe()["downloads"])
    assert files and Path(files[0]).name.startswith("invoice")


def test_leave_page_prompts_surface_as_dialogs_instead_of_hanging(browser_at, site):
    b = browser_at("/draft.html")
    page = b.observe()
    b.act("TYPE_TEXT", find(page, "Message", "fill"), page, text="unsent")
    page = b.observe()
    started = time.monotonic()
    b.act("OPEN_URL", find(page, "Open a web address", "open_url"), page, url=site["a"] + "/next.html")
    assert time.monotonic() - started < 6
    page = b.observe()
    assert page["dialog"]["type"] == "beforeunload"
    b.act("ACCEPT_DIALOG", find(page, "OK", "accept_dialog"), page)
    assert observe_until(b, lambda p: p["title"] == "Next page")
