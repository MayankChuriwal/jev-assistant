"""LOOK: find by sight what the element list can't name. Cells come from the model; points come from code."""

import base64
import struct
from unittest.mock import Mock

import pytest
from fakes import act, agent_with, decision, fake_page
from helpers import find, log, until

from assistant import agent as loop
from assistant import jobs, model, questions, rules
from assistant.browser import StalePage, fingerprint

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
    assert found == {"cell": "C4", "label": "Blue button", "text": "", "commits": False}
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
    assert "drag_target" not in heads


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


CARD = {"action_label": "Pay ₹79", "amount": "₹79", "lines": ["Diet Coke 300 ml", "Home"], "element": ""}


def looking(tmp_path, monkeypatch, *answers, actions=None, replies=()):
    """An agent over a mock browser with an 800×600 screen, whose LOOK answers are scripted."""
    page = fake_page(actions=actions)
    page["actions"].append(dict(LOOK))
    page["fingerprint"] = fingerprint(page)
    a = agent_with(tmp_path, page=page, replies=replies)

    def shot(region=None, columns=8, rows=6, masked=(), scale=1, hide=()):
        return ("CLOSE" if region else "IMAGE"), rules.grid(region or VIEW, columns, rows)

    a.browser.grid_screenshot = Mock(side_effect=shot)
    queue = list(answers)
    monkeypatch.setattr(loop.jobs, "look", lambda context, page, purpose, image, cells: (queue.pop(0), META))
    monkeypatch.setattr(loop.jobs, "look_closer", lambda label, image, cells: ("B2", META))
    return a












def outcome_success(monkeypatch):
    monkeypatch.setattr(loop.jobs, "outcome", lambda context, page, label: ("success", "Order placed #A1B2", META))
    monkeypatch.setattr(loop.jobs, "confirm_card", lambda context, page, label, elements=(): (dict(CARD), META))














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
    assert len(clips) == 7  # each grid capture checks ungridded pixels before and after
    assert clips[0]["y"] == 300 and clips[3]["y"] == cells["C4"]["y"] + 300
    pin_top = b.evaluate("document.getElementById('pin').getBoundingClientRect().top + scrollY")
    assert abs(clips[6]["y"] + 16 - pin_top) <= 2  # the crop sits on the element, not a scroll's height above it


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
    assert seen == ["blur(12px)"] * 3 and b.evaluate("document.getElementById('out').style.filter") == ""


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

@pytest.mark.browser
def test_visual_target_is_stale_after_screenshot_change_and_rejects_occlusion(browser_at):
    b = browser_at("/look.html")
    page = b.observe()
    _image, cells = b.grid_screenshot()
    target = b.visual_candidates(page, [{"cell": "C4", "label": "Board", "text": ""}])[0]
    b.evaluate("const cover=document.createElement('div'); cover.style.cssText="
               "'position:fixed;inset:0;z-index:999;background:white';document.body.append(cover)")
    with pytest.raises(StalePage):
        b.act("CLICK", target, page)
    b.evaluate("document.querySelector('div[style*=z-index]').remove()")
    b.grid_screenshot()
    with pytest.raises(StalePage):
        b.act("CLICK", target, page)
    assert cells["C4"]["w"] > 0


def test_visual_candidates_join_jev_indexed_targets():
    visual = {"id": "v1", "kind": "click", "node": None, "label": "Blue button",
              "visual_ref": "shot-1", "observation_ref": "page-1"}
    elements, heads, _controls = model.action_space([visual])
    assert elements == [{"index": "1", "label": "Blue button", "operations": [
        "CLICK", "DOUBLE_CLICK", "RIGHT_CLICK", "HOVER"], "visual": True}]
    assert heads["click_target"]["1"] is visual


@pytest.mark.browser
def test_canvas_repaint_invalidates_visual_candidate_without_dom_change(browser_at):
    b = browser_at("/look.html")
    page = b.observe()
    rect = b.evaluate("(() => {const r=document.getElementById('board').getBoundingClientRect();"
                      "return {x:r.x,y:r.y,w:r.width,h:r.height}})()")
    b.grid_screenshot(region=rect, columns=1, rows=1)
    target = b.visual_candidates(page, [{"cell": "A1", "label": "Board", "text": ""}])[0]
    b.evaluate("(() => {const ctx2=document.getElementById('board').getContext('2d');"
               "ctx2.fillStyle='yellow';ctx2.fillRect(0,0,600,300)})()")
    assert b.evaluate("document.getElementById('board').isConnected")
    with pytest.raises(StalePage):
        b.act("CLICK", target, page)
    assert log(b) == []


@pytest.mark.browser
def test_frame_repaint_invalidates_visual_candidate_without_main_dom_change(browser_at):
    from helpers import observe_until

    b = browser_at("/reliability.html")
    page = observe_until(b, lambda p: any(a.get("frame") for a in p["actions"]))
    fid = next(a["frame"] for a in page["actions"] if a.get("frame"))
    rect = b.evaluate("(() => {const r=document.getElementById('remote').getBoundingClientRect();"
                      "return {x:r.x,y:r.y,w:r.width,h:r.height}})()")
    b.grid_screenshot(region=rect, columns=1, rows=1)
    target = b.visual_candidates(page, [{"cell": "A1", "label": "Payment frame", "text": ""}])[0]
    b.evaluate("document.documentElement.style.filter='invert(1)'", b.frames[fid]["session"])
    with pytest.raises(StalePage):
        b.act("CLICK", target, page)
    assert log(b) == []


@pytest.mark.browser
def test_frame_semantics_invalidate_visual_candidate_even_when_pixels_match(browser_at):
    from helpers import observe_until

    b = browser_at("/reliability.html")
    page = observe_until(b, lambda p: any(a.get("frame") for a in p["actions"]))
    fid = next(a["frame"] for a in page["actions"] if a.get("frame"))
    rect = b.evaluate("(() => {const r=document.getElementById('remote').getBoundingClientRect();"
                      "return {x:r.x,y:r.y,w:r.width,h:r.height}})()")
    b.grid_screenshot(region=rect, columns=1, rows=1)
    target = b.visual_candidates(page, [{"cell": "A1", "label": "Payment frame", "text": ""}])[0]
    b.evaluate("document.querySelector('button').setAttribute('aria-label','Different action')",
               b.frames[fid]["session"])
    assert not b.fresh(page, target)
    assert log(b) == []


@pytest.mark.browser
def test_frame_semantics_changed_before_first_annotation_are_rejected(browser_at):
    from helpers import observe_until

    b = browser_at("/reliability.html")
    page = observe_until(b, lambda p: any(a.get("frame") for a in p["actions"]))
    fid = next(a["frame"] for a in page["actions"] if a.get("frame"))
    rect = b.evaluate("(() => {const r=document.getElementById('remote').getBoundingClientRect();"
                      "return {x:r.x,y:r.y,w:r.width,h:r.height}})()")
    b.grid_screenshot(region=rect, columns=1, rows=1)
    b.evaluate("document.querySelector('button').setAttribute('aria-label','Different action')",
               b.frames[fid]["session"])
    with pytest.raises(StalePage, match="frame"):
        b.visual_candidates(page, [{"cell": "A1", "label": "Payment frame", "text": ""}])
    assert log(b) == []


def test_look_only_adds_candidates_and_waits_for_jev(tmp_path, monkeypatch):
    a = looking(tmp_path, monkeypatch)
    candidate = dict(id='v1', kind='click', label='Blue', node=None, visual_ref='shot')
    monkeypatch.setattr(loop.jobs, 'visual_annotations', lambda *args: [{'cell': 'C4', 'label': 'Blue', 'text': ''}])
    a.browser.visual_candidates.return_value = [candidate]
    act(a, decision('look', 'LOOK'))
    a.browser.act.assert_not_called()
    assert candidate in a.state['page']['actions'] and a.state['status'] == 'ready'
    act(a, decision('v1', 'CLICK'))
    assert a.browser.act.call_args.args[1] == candidate
    assert 'point' not in a.browser.act.call_args.kwargs


def test_drag_point_is_not_offered_or_accepted(tmp_path):
    a = agent_with(tmp_path)
    a.state['page']['actions'].append(dict(id='drag', kind='drag', label='Pin', node=90))
    a.retain_observation(a.state['page'])
    with pytest.raises(ValueError):
        act(a, decision('drag', 'DRAG', drop='look'))
    a.browser.act.assert_not_called()


@pytest.mark.browser
def test_agent_visual_click_uses_a_fresh_jev_selection(browser_at, tmp_path, monkeypatch):
    b = browser_at('/look.html')
    a = agent_with(tmp_path, page=b.observe())
    a.browser = b
    a.state['browser'] = b
    left, top = b.evaluate("(() => { const r = document.getElementById('board').getBoundingClientRect();"
                           " return [r.x, r.y]; })()")
    def annotate(state, page, image, cells):
        cell = next(name for name, r in cells.items() if r['x'] <= left + 460 < r['x'] + r['w']
                    and r['y'] <= top + 200 < r['y'] + r['h'])
        return [{'cell': cell, 'label': 'Blue', 'text': ''}]
    monkeypatch.setattr(loop.jobs, 'visual_annotations', annotate)
    act(a, decision('look', 'LOOK'))
    assert log(b) == []
    visual = next(x for x in a.state['page']['actions'] if x.get('visual_ref') and x['kind'] == 'click')
    act(a, decision(visual['id'], 'CLICK'))
    assert until(lambda: log(b) == ['Blue']), (log(b), a.state['history'], a.state['messages'])
