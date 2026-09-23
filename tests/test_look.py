"""LOOK: find by sight what the element list can't name. Cells come from the model; points come from code."""

import base64
import struct

import pytest
from helpers import find, log, until

from assistant import jobs, model, questions, rules

META = {"model": "fake", "latency_ms": 1}
VIEW = {"x": 0, "y": 0, "w": 800, "h": 600}


def test_grid_cells_cover_the_region_left_to_right_and_top_to_bottom():
    cells = rules.grid(VIEW, 8, 6)
    assert len(cells) == 48 and cells["A1"] == {"x": 0, "y": 0, "w": 100, "h": 100}
    assert cells["H6"] == {"x": 700, "y": 500, "w": 100, "h": 100}
    assert (cells["C4"]["x"], cells["C4"]["y"]) == (200, 300)
    finer = rules.grid(cells["C4"], 4, 4)
    assert finer["D4"] == {"x": 275, "y": 375, "w": 25, "h": 25} and rules.centre(finer["A1"]) == (212.5, 312.5)


def test_look_answers_are_cells_or_nothing(monkeypatch):
    calls = []
    replies = [{"cell": "C4", "label": "Blue button"}, {"cell": None}, {"cell": "Z9", "label": "Far away"},
               {"cell": "C4"}, {"cell": "B2"}, {"cell": "E1"}]

    def fake(system, payload, **options):
        calls.append((system, payload, options))
        return replies.pop(0), META

    monkeypatch.setattr(jobs, "text_json", fake)
    cells, finer = rules.grid(VIEW, 8, 6), rules.grid({"x": 0, "y": 0, "w": 100, "h": 100}, 4, 4)
    page = {"url": "https://shop.test/", "title": "Shop", "text": "Pick a colour"}
    found, _ = jobs.look("context", page, "the control the task needs next", "IMAGE", cells)
    assert found == {"cell": "C4", "label": "Blue button"}
    assert calls[0][2]["images"] == ["IMAGE"] and calls[0][1]["purpose"] == "the control the task needs next"
    assert calls[0][1]["cells"][:2] == ["A1", "A2"] and len(calls[0][1]["cells"]) == 48
    assert jobs.look("context", page, "the control the task needs next", "IMAGE", cells)[0] is None
    for _ in range(2):  # a cell outside the grid, then a cell with no name
        with pytest.raises(ValueError, match="invalid look"):
            jobs.look("context", page, "the control the task needs next", "IMAGE", cells)
    assert jobs.look_closer("Blue button", "CLOSE", finer)[0] == "B2"
    assert calls[-1][1] == {"control": "Blue button", "cells": sorted(finer)} and calls[-1][2]["images"] == ["CLOSE"]
    with pytest.raises(ValueError, match="invalid look"):
        jobs.look_closer("Blue button", "CLOSE", finer)  # E1 is not on a 4×4 grid


LOOK = {"id": "look", "kind": "look", "node": None, "label": "Look at the screen"}


def test_look_is_always_offered_and_can_pick_a_drop_spot():
    actions = [{"id": "e1", "kind": "drag", "node": 1, "label": "Pin", "role": "listitem"}, dict(LOOK)]
    _elements, heads, controls = model.action_space(actions)
    assert controls["LOOK"]["id"] == "look" and "LOOK" in questions.OPERATIONS
    assert heads["drag_target"] == {"look": {**LOOK, "label": "A free spot on the screen, found by looking"}}


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
