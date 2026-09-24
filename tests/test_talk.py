"""The agent talking to its user: typing resolution, questions, choices, notes, results. Fakes only."""

import json
import threading
from copy import deepcopy
from unittest.mock import Mock

import pytest
from fakes import FakeVault, act, agent_with, decision, fake_page

from assistant import agent as loop
from assistant.browser import StalePage
from assistant.profile import Profile

META = {"model": "fake", "latency_ms": 1}


def field(monkeypatch, *forms):
    queue, calls = list(forms), []

    def fake(context):
        calls.append(context)
        return queue.pop(0), META

    monkeypatch.setattr(loop, "field_value", fake)
    return calls


def typed(a):
    return a.browser.act.call_args.kwargs["text"]


def choice(monkeypatch, *options, kind="choice", preference=True):
    question = {"kind": kind, "text": "Deliver to which address?", "preference": preference,
                "options": [{"label": label, "description": "", "element": element} for label, element in options]}
    monkeypatch.setattr(loop.jobs, "compose", lambda context, page, elements: (question, META))


def test_profile_values_are_typed_but_logged_as_placeholders(tmp_path, monkeypatch):
    a = agent_with(tmp_path)
    field(monkeypatch, ("profile", "phone"))
    act(a, decision("e1", "TYPE_TEXT"))
    assert typed(a) == "+91 98765 43210"
    assert a.state["history"][-1]["text"] == a.state["text_calls"][-1]["value"] == "[profile:phone]"
































def test_pages_are_redacted_before_anyone_sees_them(tmp_path):
    a = agent_with(tmp_path)
    a.browser.observe.return_value = fake_page(text="Thanks Mayank Churiwal, call 98765 43210")
    page = a.observe()
    assert page["text"] == "Thanks [profile:name], call [profile:phone]"
    assert "98765" not in json.dumps(a.snapshot(), ensure_ascii=False, default=str)


def test_screenshots_blur_fields_that_hold_secrets(tmp_path):
    a = agent_with(tmp_path)
    a.screenshots = True
    a.secrets.nodes.add((None, 8))
    page = a.observe()
    assert page["screenshot"] == "iVBORw0KGgo="
    assert a.browser.screenshot.call_args.kwargs == {"masked": {(None, 8)}, "hide": a.hidden_details()}
    a.browser.observe.assert_called_with(screenshot=False)






def test_a_pick_is_never_guessed_between_identical_labels(tmp_path, monkeypatch):
    page = fake_page()
    for x in page["actions"]:
        if x["id"] in ("e4", "e5"):
            x["label"] = "Deliver here"
    moved = deepcopy(page)
    for x in moved["actions"]:
        if x["id"] in ("e4", "e5"):
            x["node"] += 100
    a = agent_with(tmp_path, page=page, replies=[{"option": "o1"}])
    a.browser.observe.return_value = moved
    choice(monkeypatch, ("Home", "4"), ("Office", "5"))
    act(a, decision("ASK_USER", "ASK_USER"))
    a.browser.act.assert_not_called()


def test_a_given_title_is_kept(tmp_path):
    a = loop.Agent("https://www.swiggy.com/instamart", "Order Diet Coke", title="Swiggy · Diet Coke",
                   profile=Profile(tmp_path / "p.json"), vault=FakeVault(),
                   browser=Mock(observe=Mock(return_value=fake_page())))
    assert a.state["title"] == "Swiggy · Diet Coke"


def test_an_interrupted_step_is_chosen_again_instead_of_run(tmp_path):
    a = agent_with(tmp_path)
    a.interrupted = lambda: True  # the user cancelled or changed the task while this step was being decided
    with pytest.raises(StalePage):
        act(a, decision("e4", "CLICK"))
    a.browser.act.assert_not_called()


def test_settle_keeps_downloads_that_finish_late(tmp_path):
    a = agent_with(tmp_path)
    a.browser.pending_downloads, a.browser.downloads = {"g1": "invoice.pdf"}, []

    def finish():
        a.browser.downloads.append("/downloads/invoice.pdf")
        a.browser.pending_downloads.clear()

    threading.Timer(0.2, finish).start()
    a.settle(timeout=3)
    assert a.state["files"][-1] == "/downloads/invoice.pdf" and a.browser.downloads == []






def test_typing_can_use_values_the_goal_does_not_hold():
    from assistant import questions

    assert "asking the user" in questions.OPERATIONS["TYPE_TEXT"]
    assert "even by asking the user" in questions.OPERATIONS["BLOCKED"]


SEARCH = {"id": "e1", "kind": "fill", "label": "Search for restaurants and food", "role": "searchbox", "value": "",
          "node": 1, "constraints": {"type": "text"}}












def help_with(monkeypatch, text):
    step = {"rung": "guide", "text": text, "options": []}
    monkeypatch.setattr(loop.jobs, "unblock", lambda context, page, elements, must_stop=False: (step, META))
    monkeypatch.setattr(loop.jobs, "hint", lambda context, page, text=None, image=None: ("Scroll down", META))










HOME = {"id": "e1", "kind": "click", "label": "HOME 1122, Sobha Royal Pavilion, Sarjapur Road, Bengaluru",
        "role": "button", "node": 1}
WORK = {"id": "e2", "kind": "click", "label": "WORK 4, Residency Road, Bengaluru 560025", "role": "button", "node": 2}
HERE = {"id": "e3", "kind": "click", "label": "Use current location", "role": "button", "node": 3}




def test_a_saved_address_preference_needs_no_question(tmp_path):
    a = agent_with(tmp_path, page=fake_page(text="Select delivery address", actions=[HOME, WORK]))
    a.profile.data["preferences"].append({"text": "Default address: Home, Sobha Royal Pavilion", "from_task": "x"})
    act(a, decision("e1", "CLICK"))
    assert a.asked == [] and [c.args[1]["node"] for c in a.browser.act.call_args_list] == [1]






RIDES = [{"id": "e1", "kind": "click", "role": "option", "node": 1,
          "label": "Uber Go AC Person 4 3 mins away • 4:56 AM Affordable compact rides Tag ₹884.18 ₹944.18"},
         {"id": "e2", "kind": "click", "role": "option", "node": 2,
          "label": "Go Non AC Person 4 5 mins away • 4:58 AM Everyday affordable rides Tag ₹860.03 ₹920.03"},
         {"id": "e3", "kind": "click", "role": "option", "node": 3,
          "label": "Premier AC Person 4 2 mins away • 4:55 AM Comfortable sedans, top-quality drivers ₹1,211.66"},
         {"id": "e4", "kind": "click", "role": "button", "node": 4, "label": "Request Uber Go AC"}]


def riding(tmp_path, goal="book an uber", replies=()):
    a = agent_with(tmp_path, page=fake_page(text="Choose a ride", url="https://m.uber.com/go/product-selection",
                                            actions=RIDES), replies=replies)
    a.state["goal"] = goal
    return a






def test_an_alternative_the_goal_names_needs_no_question(tmp_path):
    a = riding(tmp_path, goal="book an uber premier to the airport")
    act(a, decision("e3", "CLICK"))
    assert a.asked == [] and [c.args[1]["node"] for c in a.browser.act.call_args_list] == [3]


def need(a, key='address', kind='text', options=None):
    from assistant.interaction import require
    return require(a.task, dict(id=key, kind=kind, purpose='task', keys=[key], scope='task',
                               depends_on=[], options=options or [], reason='Task needs this detail'))


def test_answer_is_fact_and_does_not_choose_target(tmp_path):
    a = agent_with(tmp_path, replies=[{'text': 'Home'}])
    need(a)
    act(a, decision('ASK_USER', 'ASK_USER'))
    assert a.task.facts['address']['value'] == 'Home'
    a.browser.act.assert_not_called()
    assert a.state['status'] == 'ready'


def test_answered_requirement_is_not_asked_twice(tmp_path):
    a = agent_with(tmp_path, replies=[{'text': 'Home'}])
    need(a)
    act(a, decision('ASK_USER', 'ASK_USER'))
    act(a, decision('ASK_USER', 'ASK_USER'))
    assert len(a.asked) == 1
    a.browser.act.assert_not_called()


def test_option_reply_cannot_trigger_direct_click(tmp_path):
    a = agent_with(tmp_path, replies=[{'option': 'home'}])
    eid = next(iter(a.task.evidence))
    need(a, kind='choice', options=[dict(id='home', label='Home', description='', evidence_ids=[eid])])
    act(a, decision('ASK_USER', 'ASK_USER'))
    assert a.task.facts['address']['value']['label'] == 'Home'
    a.browser.act.assert_not_called()


@pytest.mark.parametrize('reply,status', [({'cancel': True}, 'cancelled'), (None, 'blocked')])
def test_missing_asker_or_cancel_stops_before_mutation(tmp_path, reply, status):
    a = agent_with(tmp_path, replies=[reply] if reply else [])
    need(a)
    if reply is None:
        a.asker = None
    act(a, decision('ASK_USER', 'ASK_USER'))
    assert a.task.status == status
    a.browser.act.assert_not_called()


def test_upload_keeps_code_owned_file_path(tmp_path):
    path = tmp_path / 'statement.pdf'
    path.write_bytes(b'%PDF-1.4')
    a = agent_with(tmp_path, replies=[{'file': str(path)}])
    act(a, decision('e7', 'UPLOAD'))
    assert a.browser.act.call_args.kwargs == {'files': [str(path)]}
    assert a.asked[0]['kind'] == 'file'


def test_model_field_message_cannot_be_typed(tmp_path, monkeypatch):
    a = agent_with(tmp_path)
    field(monkeypatch, ('text', 'Please enter your phone number'))
    with pytest.raises(ValueError):
        act(a, decision('e1', 'TYPE_TEXT'))
    a.browser.act.assert_not_called()


def test_amendment_redacts_and_invalidates_approval(tmp_path):
    a = agent_with(tmp_path)
    a.secrets.add('654321', '[secret:code]')
    a.task.approval = {'old': True}
    a.amend('Use code 654321 for my address', {'address'})
    assert '654321' not in str(a.task) and a.task.approval is None
    assert a.task.revision == 1 and a.state['decision'] is None


def test_unverified_done_does_not_use_commerce_heuristic(tmp_path):
    a = agent_with(tmp_path)
    a.state['goal'] = 'Read the article'
    act(a, decision('DONE', 'DONE'))
    assert a.task.status == 'blocked'
    assert 'unverified' in a.told[-1]['text']


def test_current_location_remains_a_chat_requirement(tmp_path):
    a = agent_with(tmp_path, replies=[{'text': 'Use my current location'}])
    need(a, 'location')
    act(a, decision('ASK_USER', 'ASK_USER'))
    assert not a.task.facts.get('location', {}).get('valid')
    a.browser.act.assert_not_called()
