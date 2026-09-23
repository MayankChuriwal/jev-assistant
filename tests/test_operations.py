"""Pointer, keyboard, scroll, drag, and upload operations in a real headless Chrome. Run: uv run pytest -m browser"""

import json

import pytest
from helpers import find, log

pytestmark = pytest.mark.browser


def test_cards_hover_menus_and_mouse_variants(browser_at):
    b = browser_at("/ops.html")
    page = b.observe()
    b.act("CLICK", find(page, "Coke Zero"), page)
    page = b.observe()
    b.act("DOUBLE_CLICK", find(page, "Rename list"), page)
    page = b.observe()
    b.act("RIGHT_CLICK", find(page, "Options"), page)
    page = b.observe()
    assert not any(a["label"] == "Orders" for a in page["actions"])
    b.act("HOVER", find(page, "Account"), page)
    page = b.observe()
    b.act("CLICK", find(page, "Orders"), page)
    assert log(b) == ["card:Coke Zero", "dblclick", "contextmenu", "menu:Orders"]


def test_passwords_codes_dates_keys_and_dropdowns(browser_at):
    b = browser_at("/ops.html")
    page = b.observe()
    b.act("TYPE_TEXT", find(page, "Password", "fill"), page, text="hunter2")
    page = b.observe()
    assert find(page, "Password", "fill")["value"] == "[filled]" and "hunter2" not in json.dumps(page)
    b.act("TYPE_TEXT", find(page, "One-time code", "fill"), page, text="482913")
    page = b.observe()
    b.act("TYPE_TEXT", find(page, "Date", "fill"), page, text="2026-09-20")
    page = b.observe()
    b.act("TYPE_TEXT", find(page, "Search", "fill"), page, text="coke")
    page = b.observe()
    b.act("PRESS_KEY", find(page, "Search", "fill"), page, key="Enter")
    page = b.observe()
    b.act("PRESS_KEY", find(page, "Quantity"), page, key="ArrowRight")
    page = b.observe()
    b.act("SELECT", find(page, "Drinks", "select"), page)
    assert b.evaluate("[password.value, date.value, quantity.value, category.value]") == [
        "hunter2", "2026-09-20", "2", "Drinks"]
    assert log(b) == ["otp:482913", "search:coke", "quantity:2"]


def test_wrong_length_code_types_nothing(browser_at):
    b = browser_at("/ops.html")
    page = b.observe()
    with pytest.raises(ValueError, match="6 characters"):
        b.act("TYPE_TEXT", find(page, "One-time code", "fill"), page, text="4829")
    assert b.evaluate("[...document.querySelectorAll('.otp input')].map(i => i.value).join('')") == ""


def test_panels_scroll_and_lists_drag(browser_at):
    b = browser_at("/ops.html")
    page = b.observe()
    b.act("SCROLL_DOWN", find(page, "Order history", "scroll_down"), page)
    page = b.observe()
    b.act("SCROLL_RIGHT", find(page, "Offers", "scroll_right"), page)
    page = b.observe()
    b.act("DRAG", find(page, "Alpha", "drag"), page, drop=find(page, "Gamma", "drag"))
    assert b.evaluate("[panel.scrollTop > 0, carousel.scrollLeft > 0]") == [True, True]
    assert log(b) == ["order:Beta,Gamma,Alpha"]


@pytest.fixture
def documents(tmp_path):
    statement = tmp_path / "statement.pdf"
    statement.write_bytes(b"%PDF-1.4 statement")
    receipt = tmp_path / "receipt.png"
    receipt.write_bytes(b"\x89PNG\r\n\x1a\n receipt")
    return statement, receipt


def test_uploads_reach_hidden_inputs_multi_inputs_and_drop_zones(browser_at, documents):
    statement, receipt = documents
    b = browser_at("/upload.html")
    page = b.observe()
    b.act("UPLOAD", find(page, "Card statement", "upload"), page, files=[str(statement)])
    page = b.observe()
    b.act("UPLOAD", find(page, "Drop zone", "upload"), page, files=[str(receipt)])
    page = b.observe()
    b.act("UPLOAD", find(page, "Other documents", "upload"), page, files=[str(statement), str(receipt)])
    assert b.evaluate("document.getElementById('statement-name').textContent") == "statement.pdf · 18 B"
    assert b.evaluate("document.getElementById('receipt-name').textContent") == "receipt.png · 16 B"
    assert b.evaluate("[...document.getElementById('extra').files].map(f => f.name)") == [
        "statement.pdf", "receipt.png"]


def test_upload_refuses_missing_files(browser_at, tmp_path):
    b = browser_at("/upload.html")
    page = b.observe()
    with pytest.raises(ValueError, match="does not exist"):
        b.act("UPLOAD", find(page, "Card statement", "upload"), page, files=[str(tmp_path / "missing.pdf")])
