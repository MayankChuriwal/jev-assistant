"""LOOK: find by sight what the element list can't name. Cells come from the model; points come from code."""

import base64
import json as jsonlib
import struct
from unittest.mock import Mock

import pytest
from fakes import FakeVault, act, agent_with, decision, fake_page
from helpers import find, log, until

from assistant import agent as loop
from assistant import jobs, model, questions, rules
from assistant.browser import StalePage, fingerprint
from assistant.profile import Profile

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


def test_look_clicks_the_centre_of_the_cell_it_narrowed_down_to(tmp_path, monkeypatch):
    a = looking(tmp_path, monkeypatch, {"cell": "C4", "label": "Blue button"})
    a.secrets.nodes.add((None, 8))
    act(a, decision("look", "LOOK"))
    operation, _action, _page = a.browser.act.call_args.args
    assert operation == "LOOK" and a.browser.act.call_args.kwargs == {"point": [237.5, 337.5]}
    first, closer = a.browser.grid_screenshot.call_args_list
    assert first.kwargs["masked"] == {(None, 8)} and closer.kwargs["region"] == rules.grid(VIEW, 8, 6)["C4"]
    assert a.state["history"][-1]["action"] == "Blue button" and a.state["history"][-1]["text"] == "Blue button"
    assert [c["field"] for c in a.state["text_calls"]] == ["LOOK", "LOOK closer"]


def test_looking_and_finding_nothing_leaves_a_note(tmp_path, monkeypatch):
    a = looking(tmp_path, monkeypatch, None)
    act(a, decision("look", "LOOK"))
    a.browser.act.assert_not_called()
    assert a.state["memory"][-1] == "Looked at the screen: nothing there fits the next step."
    assert a.state["status"] == "ready" and a.browser.grid_screenshot.call_count == 1


def test_a_payment_found_by_looking_needs_approval(tmp_path, monkeypatch):
    monkeypatch.setattr(loop.jobs, "confirm_card", lambda context, page, label, elements=(): (dict(CARD), META))
    a = looking(tmp_path, monkeypatch, {"cell": "C4", "label": "Pay ₹79"},
                replies=[{"option": "no"}, {"option": "items"}])
    act(a, decision("look", "LOOK"))
    a.browser.act.assert_not_called()
    assert a.asked[0]["kind"] == "confirm" and "change the items" in a.state["memory"][-1]


def test_a_drag_can_drop_where_looking_finds_room(tmp_path, monkeypatch):
    pin = {"id": "e1", "kind": "drag", "node": 1, "label": "Pin", "role": "listitem"}
    a = looking(tmp_path, monkeypatch, {"cell": "C4", "label": "Empty spot"}, actions=[pin])
    act(a, decision("e1", "DRAG", drop="look"))
    drop = a.browser.act.call_args.kwargs["drop"]
    assert drop["point"] == [237.5, 337.5] and drop["label"] == "Empty spot" and drop["id"] == "look"


@pytest.mark.browser
def test_the_agent_clicks_a_canvas_button_it_can_only_see(chrome, site, tmp_path, monkeypatch):
    sent, where = [], {}

    class Response:
        status_code, is_error = 200, False

        def __init__(self, data):
            self.data = data

        def json(self):
            return self.data

    def containing(cells, point):
        return next(name for name, c in cells.items()
                    if c["x"] <= point[0] < c["x"] + c["w"] and c["y"] <= point[1] < c["y"] + c["h"])

    def post(url, json=None, headers=None):  # the keyword httpx uses; the module is jsonlib here
        sent.append(json)
        if "typesafe" in url:
            operations = json["questions"]["operation"]["criteria"]
            chosen = "DONE" if "Picked Blue" in json["state"]["page"]["text"] else "LOOK"
            return Response({"model": "fake-jev", "answers": {"operation": {
                "choice": chosen, "confidence": 1.0, "probabilities": {op: float(op == chosen) for op in operations}}}})
        system = json["messages"][0]["content"]
        if system == questions.LOOK:
            where["cell"] = containing(rules.grid(where["view"], 8, 6), where["blue"])
            answer = {"cell": where["cell"], "label": "Blue button"}
        elif system == questions.LOOK_CLOSER:
            region = rules.grid(where["view"], 8, 6)[where["cell"]]
            answer = {"cell": containing(rules.grid(region, 4, 4), where["blue"])}
        elif system == questions.RESULT:
            answer = {"text": "Picked the blue colour.", "evidence": "Picked Blue", "achieved": True}
        else:
            raise AssertionError(f"unexpected job: {system[:40]}")
        return Response({"choices": [{"message": {"content": jsonlib.dumps(answer)}}]})

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(model.CLIENT, "post", post)
    with loop.Agent(site["a"] + "/look.html", "Pick the blue colour", profile=Profile(tmp_path / "profile.json"),
                    vault=FakeVault()) as agent:
        left, top = agent.browser.evaluate("(() => { const r = document.getElementById('board')"
                                           ".getBoundingClientRect(); return [r.x, r.y]; })()")
        width, height = agent.browser.evaluate("[innerWidth, innerHeight]")
        where.update(blue=(left + 460, top + 200), view={"x": 0, "y": 0, "w": width, "h": height})
        for state in agent.run():
            assert len(state["decisions"]) < 6
        clicks = agent.browser.evaluate("window.log")
    assert agent.state["status"] == "done" and clicks == ["Blue"]
    pictures = [b["messages"][1]["content"] for b in sent
                if "messages" in b and isinstance(b["messages"][1]["content"], list)]
    assert len(pictures) == 2 and pictures[0][1]["image_url"]["url"].startswith("data:image/png;base64,")


def outcome_success(monkeypatch):
    monkeypatch.setattr(loop.jobs, "outcome", lambda context, page, label: ("success", "Order placed #A1B2", META))
    monkeypatch.setattr(loop.jobs, "confirm_card", lambda context, page, label, elements=(): (dict(CARD), META))


def test_a_control_that_commits_needs_approval_whatever_it_is_called(tmp_path, monkeypatch):
    outcome_success(monkeypatch)
    for answer in ({"cell": "C4", "label": "Orange button", "text": "", "commits": True},
                   {"cell": "C4", "label": "Orange button", "text": "Place order", "commits": False}):
        a = looking(tmp_path, monkeypatch, answer, replies=[{"option": "no"}, {"option": "items"}])
        act(a, decision("look", "LOOK"))
        a.browser.act.assert_not_called()
        assert a.asked[0]["kind"] == "confirm", answer


def test_an_approved_look_clicks_the_spot_the_user_saw(tmp_path, monkeypatch):
    outcome_success(monkeypatch)
    a = looking(tmp_path, monkeypatch, {"cell": "C4", "label": "Pay ₹79", "text": "Pay ₹79", "commits": True},
                {"cell": "A1", "label": "Green button", "text": "", "commits": False}, replies=[{"option": "yes"}])
    with pytest.raises(StalePage):
        act(a, decision("look", "LOOK"))
    act(a, decision("look", "LOOK"))  # the page is unchanged: the approved spot is clicked, nothing is looked up again
    assert a.browser.act.call_count == 1 and a.browser.act.call_args.kwargs == {"point": [237.5, 337.5]}
    assert a.browser.grid_screenshot.call_count == 2 and a.state["approval"] is None
    assert a.state["memory"][-1] == "Done: Pay ₹79 (Order placed #A1B2)"


def test_a_look_approval_never_covers_a_listed_control(tmp_path, monkeypatch):
    outcome_success(monkeypatch)
    a = looking(tmp_path, monkeypatch, {"cell": "C4", "label": "Pay ₹79", "text": "Pay ₹79", "commits": True},
                replies=[{"option": "yes"}, {"option": "no"}, {"option": "items"}])
    with pytest.raises(StalePage):
        act(a, decision("look", "LOOK"))
    act(a, decision("e6", "CLICK"))  # the page's own "Pay ₹79" button is another control: it needs its own yes
    a.browser.act.assert_not_called()
    assert [m["kind"] for m in a.asked] == ["confirm", "confirm", "choice"]


def test_looking_hides_the_users_details(tmp_path, monkeypatch):
    a = looking(tmp_path, monkeypatch, {"cell": "C4", "label": "Blue button"})
    act(a, decision("look", "LOOK"))
    hidden = a.browser.grid_screenshot.call_args_list[0].kwargs["hide"]
    assert "Mayank Churiwal" in hidden and any("98765" in value for value in hidden)


def test_looking_twice_at_nothing_stops_instead_of_looping(tmp_path, monkeypatch):
    a = looking(tmp_path, monkeypatch, None, None)
    act(a, decision("look", "LOOK"))
    assert a.state["status"] == "ready"
    act(a, decision("look", "LOOK"))
    assert a.state["status"] == "blocked" and "couldn't find" in a.told[-1]["text"]


def test_clicks_on_a_canvas_are_not_mistaken_for_no_progress(tmp_path, monkeypatch):
    spot = {"cell": "C4", "label": "Seat 12A"}
    a = looking(tmp_path, monkeypatch, spot, dict(spot), dict(spot))
    for _ in range(3):  # a canvas changes no text the page reports
        act(a, decision("look", "LOOK"))
    assert a.state["status"] == "ready" and a.browser.act.call_count == 3


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

    b = browser_at("/pay.html")
    page = observe_until(b, lambda p: any(a.get("frame") for a in p["actions"]))
    fid = next(a["frame"] for a in page["actions"] if a.get("frame"))
    rect = b.evaluate("(() => {const r=document.querySelector('iframe').getBoundingClientRect();"
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

    b = browser_at("/pay.html")
    page = observe_until(b, lambda p: any(a.get("frame") for a in p["actions"]))
    fid = next(a["frame"] for a in page["actions"] if a.get("frame"))
    rect = b.evaluate("(() => {const r=document.querySelector('iframe').getBoundingClientRect();"
                      "return {x:r.x,y:r.y,w:r.width,h:r.height}})()")
    b.grid_screenshot(region=rect, columns=1, rows=1)
    target = b.visual_candidates(page, [{"cell": "A1", "label": "Payment frame", "text": ""}])[0]
    b.evaluate("document.querySelector('button').setAttribute('aria-label','Different action')",
               b.frames[fid]["session"])
    assert not b.fresh(page, target)
    assert log(b) == []
