"""LOOK: find by sight what the element list can't name. Cells come from the model; points come from code."""

import base64
import struct

import pytest
from helpers import find, log, until

from assistant import rules

VIEW = {"x": 0, "y": 0, "w": 800, "h": 600}


def test_grid_cells_cover_the_region_left_to_right_and_top_to_bottom():
    cells = rules.grid(VIEW, 8, 6)
    assert len(cells) == 48 and cells["A1"] == {"x": 0, "y": 0, "w": 100, "h": 100}
    assert cells["H6"] == {"x": 700, "y": 500, "w": 100, "h": 100}
    assert (cells["C4"]["x"], cells["C4"]["y"]) == (200, 300)
    finer = rules.grid(cells["C4"], 4, 4)
    assert finer["D4"] == {"x": 275, "y": 375, "w": 25, "h": 25} and rules.centre(finer["A1"]) == (212.5, 312.5)


@pytest.mark.browser
def test_the_grid_leaves_the_page_as_it_was(browser_at):
    b = browser_at("/look.html")
    page = b.observe()
    width, height = b.evaluate("[innerWidth, innerHeight]")
    image, cells = b.grid_screenshot()
    assert base64.b64decode(image)[:8] == b"\x89PNG\r\n\x1a\n" and len(cells) == 48
    assert cells["H6"]["x"] + cells["H6"]["w"] == pytest.approx(width)
    assert cells["H6"]["y"] + cells["H6"]["h"] == pytest.approx(height)
    assert b.fresh(page) and b.evaluate("document.getElementById('__jev_grid') === null")
    close, finer = b.grid_screenshot(region=cells["C4"], columns=4, rows=4, scale=2)
    assert len(finer) == 16 and (finer["A1"]["x"], finer["A1"]["y"]) == (cells["C4"]["x"], cells["C4"]["y"])
    size = struct.unpack(">II", base64.b64decode(close)[16:24])  # the PNG header's width and height
    assert abs(size[0] - cells["C4"]["w"] * 2) <= 1 and abs(size[1] - cells["C4"]["h"] * 2) <= 1


@pytest.mark.browser
def test_look_clicks_a_point_and_drag_drops_on_one(browser_at):
    b = browser_at("/look.html")
    left, top = b.evaluate("(() => { const r = document.getElementById('board').getBoundingClientRect();"
                           " return [r.x, r.y]; })()")
    page = b.observe()
    b.act("LOOK", find(page, "Look at the screen", "look"), page, point=[left + 460, top + 200])
    assert until(lambda: log(b) == ["Blue"])
    page = b.observe()
    spot = {**find(page, "Look at the screen", "look"), "point": [left + 100, top + 250]}
    b.act("DRAG", find(page, "Pin", "drag"), page, drop=spot)
    assert until(lambda: log(b)[-1:] == ["drop:100,250"])


@pytest.mark.browser
def test_captures_follow_a_scrolled_page(browser_at, monkeypatch):
    b = browser_at("/look.html")
    b.evaluate("document.body.style.height = '3000px'; scrollTo(0, 300)")
    page = b.observe()
    clips, real = [], b.call

    def spy(method, session=None, **params):
        if method == "Page.captureScreenshot":
            clips.append(params.get("clip"))
        return real(method, session, **params)

    monkeypatch.setattr(b, "call", spy)
    _image, cells = b.grid_screenshot()
    b.grid_screenshot(region=cells["C4"], columns=4, rows=4, scale=2)
    b.screenshot(find(page, "Pin", "drag"))
    assert clips[0]["y"] == 300 and clips[1]["y"] == cells["C4"]["y"] + 300
    pin_top = b.evaluate("document.getElementById('pin').getBoundingClientRect().top + scrollY")
    assert abs(clips[2]["y"] + 16 - pin_top) <= 2  # the crop sits on the element, not a scroll's height above it


@pytest.mark.browser
def test_captures_hide_the_users_details_shown_as_text(browser_at, monkeypatch):
    b = browser_at("/look.html")
    b.evaluate("document.getElementById('out').textContent = 'Deliver to Mayank Churiwal, 98765 43210'")
    b.observe()
    seen, real = [], b.call

    def spy(method, session=None, **params):
        if method == "Page.captureScreenshot":
            seen.append(b.evaluate("document.getElementById('out').style.filter"))
        return real(method, session, **params)

    monkeypatch.setattr(b, "call", spy)
    b.grid_screenshot(hide=["Mayank Churiwal", "+91 98765 43210", "9876543210"])
    assert seen == ["blur(12px)"] and b.evaluate("document.getElementById('out').style.filter") == ""


@pytest.mark.browser
def test_a_screenshot_is_never_taken_unmasked(browser_at, monkeypatch):
    from assistant import browser as browsers

    b = browser_at("/look.html")
    b.observe()
    methods, evaluate, call = [], b.evaluate, b.call

    def busy(expression, session=None):
        if expression.startswith(f"({browsers.MASK})") and ", true" in expression:
            raise TimeoutError("the page is busy")
        return evaluate(expression, session)

    monkeypatch.setattr(b, "evaluate", busy)
    monkeypatch.setattr(b, "call", lambda method, session=None, **params: methods.append(method) or call(
        method, session, **params))
    with pytest.raises(RuntimeError, match="nothing was captured"):
        b.screenshot()
    assert "Page.captureScreenshot" not in methods


@pytest.mark.browser
def test_numbers_are_drawn_for_one_capture_only(browser_at, monkeypatch):
    b = browser_at("/look.html")
    page = b.observe()
    seen, real = [], b.call

    def spy(method, session=None, **params):
        if method == "Page.captureScreenshot":
            seen.append(b.evaluate("document.getElementById('__jev_marks') !== null"))
        return real(method, session, **params)

    monkeypatch.setattr(b, "call", spy)
    image = b.marked_screenshot([find(page, "Pin", "drag")])
    assert base64.b64decode(image)[:8] == b"\x89PNG\r\n\x1a\n" and seen == [True]
    assert b.fresh(page) and b.evaluate("document.getElementById('__jev_marks') === null")
