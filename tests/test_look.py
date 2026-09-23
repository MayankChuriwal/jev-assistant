"""LOOK: find by sight what the element list can't name. Cells come from the model; points come from code."""

import pytest

from assistant import jobs, rules

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
