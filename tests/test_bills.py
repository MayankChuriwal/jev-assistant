"""A bill's payment in the worker: the page model (scripted here) acts, money.py and the gate decide about money. No
Chrome, no models."""

import json
from unittest.mock import Mock

import pytest
from fakes import fake_page, jev_checks, store, worker_with

from assistant import actor, money, planner
from assistant import worker as module
from assistant.errors import Retry

BOARD = "https://bescom.co.in/bescom/main/quick-payment"
GOAL = money.goal_for("BESCOM", "bescom_account")
BILL_TEXT = ("Quick Payment\nAccount ID [profile:bescom_account]\nConsumer Name M K CHURIWAL\n"
             "Bill Amount ₹ 1,240.00\nDue Date 20-10-2026\nPay Now")
LINK = "upi://pay?pa=bescom.billdesk@hdfcbank&pn=BESCOM&am=1240.00&tr=TXN1&cu=INR"


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(module, "CONFIRM_GAP", 0)
    monkeypatch.setattr(planner, "turn", lambda *a, **k: pytest.fail("the planner was asked"))


def bill_page(text=BILL_TEXT, url=BOARD, extra=()):
    return fake_page(url=url, text=text, actions=[
        {"id": "a1", "kind": "fill", "label": "Account ID", "role": "textbox", "value": "", "node": 1,
         "constraints": {}},
        {"id": "a2", "kind": "click", "label": "Pay Now", "role": "button", "node": 2},
        *extra,
        {"id": "wait", "kind": "wait", "label": "Wait", "node": None},
        {"id": "open_url", "kind": "open_url", "label": "Open a web address", "node": None}])


def answer(actions=(), end="continue", say=None, options=(), **facts):
    return {"actions": [{"op": op, "target": target, "text": text} for op, target, text in actions],
            "facts": {name: facts.get(name) for name in money.FACTS}, "end": end, "say": say,
            "options": list(options)}


def script(monkeypatch, *answers):
    """The page model's answers in order (the last one repeats). Returns the payloads it was shown."""
    queue, seen = list(answers), []

    def turn(body, image=None):
        seen.append(body)
        return (queue.pop(0) if len(queue) > 1 else queue[0]), {"model": "fake", "latency_ms": 1}

    monkeypatch.setattr(actor, "turn", turn)
    return seen


FACTS = {"name": "M K [profile:name]", "amount_due": "₹ 1,240.00", "due_date": "20-10-2026"}


def bill_worker(tmp_path, page=None, replies=(), memory=None, goal=GOAL):
    return worker_with(tmp_path, page=page or bill_page(), goal=goal, replies=replies, memory=memory,
                       profile={"bescom_account": "1234567890"})


def test_a_bills_task_is_driven_by_the_page_model_and_types_the_saved_account(tmp_path, monkeypatch):
    page = bill_page(text="Quick Payment\nAccount ID\nSubmit")
    w = bill_worker(tmp_path, page=page)
    seen = script(monkeypatch, answer([("type", "1", "[profile:bescom_account]")]))
    w.command("tick")
    assert w.browser.act.call_args.args[0] == "TYPE_TEXT" and w.browser.act.call_args.kwargs["text"] == "1234567890"
    assert "1234567890" not in json.dumps(seen)  # the model saw the placeholder only
    assert seen[0]["goal"] == GOAL and seen[0]["page"]["elements"]


def test_a_fact_the_page_doesnt_show_is_refused(tmp_path, monkeypatch):
    w = bill_worker(tmp_path)
    script(monkeypatch, answer(name="M K [profile:name]", amount_due="₹ 1,420", due_date="20-10-2026"))
    w.command("tick")
    assert "amount_due" not in w.state["bill_facts"] and w.state["bill_facts"]["name"] == "M K [profile:name]"
    assert any("isn't on the page" in f for f in w.state["feedback"])


def test_nothing_due_ends_the_task_without_paying(tmp_path, monkeypatch):
    w = bill_worker(tmp_path, page=bill_page(text=BILL_TEXT.replace("₹ 1,240.00", "₹ 0.00")))
    script(monkeypatch, answer(**{**FACTS, "amount_due": "₹ 0.00"}))
    w.command("tick")
    assert w.state["status"] == "done" and w.state["outcome"] == "nothing_due"
    assert not w.browser.act.called


def test_a_bill_in_another_name_asks_first(tmp_path, monkeypatch):
    page = bill_page(text=BILL_TEXT.replace("M K CHURIWAL", "RAJESH CHURIWAL"))
    w = bill_worker(tmp_path, page=page, replies=[{"option": "o2"}])
    script(monkeypatch, answer(**{**FACTS, "name": "RAJESH [profile:name]"}))
    w.command("tick")
    assert "RAJESH" in w.asked[0]["text"] and w.state["outcome"] == "not_approved"
    assert not w.browser.act.called


def test_a_bill_paid_this_month_asks_before_paying_again(tmp_path, monkeypatch):
    memory = store()
    memory.payment_ended(memory.payment_started("BESCOM", "bescom_account", "1240", "UPI"), "paid", "BD1")
    w = bill_worker(tmp_path, memory=memory, replies=[{"option": "o2"}])
    script(monkeypatch, answer(**FACTS))
    w.command("tick")
    assert "You paid ₹1,240 to BESCOM" in w.asked[0]["text"] and "BD1" in w.asked[0]["text"]
    assert w.state["outcome"] == "already_paid"


def test_no_pay_button_is_pressed_before_the_bill_is_read(tmp_path, monkeypatch):
    w = bill_worker(tmp_path)
    jev_checks(monkeypatch)
    script(monkeypatch, answer([("click", "2", None)]))
    w.command("tick")
    assert any("Read the bill first" in f for f in w.state["feedback"]) and not w.browser.act.called


def test_the_bills_approval_is_written_in_code_and_asked_once(tmp_path, monkeypatch):
    w = bill_worker(tmp_path, replies=[{"option": "o1"}])
    jev_checks(monkeypatch, matches="no")  # never asked: the lines are the bill's own
    monkeypatch.setattr(planner, "summary", lambda payload: pytest.fail("the summary model was asked"))
    script(monkeypatch, answer([("click", "2", None)], **FACTS))
    w.command("tick")
    question = w.asked[0]["text"]
    assert w.asked[0]["approval"] and question.startswith("Pay ₹1,240 BESCOM bill\n• Account ••7890")
    assert "• Name on the bill: matches yours" in question and "• Due 20-10-2026" in question
    assert "couldn't read the total" not in question
    assert not w.browser.act.called and w.state["approval"]["by"] == "user"
    w.command("tick")  # the yes is acted on at once, without the model
    assert w.browser.act.call_args.args[1]["label"] == "Pay Now"
    assert w.memory.payments("BESCOM", "bescom_account")[0]["status"] == "started"


def paid(w):
    w.state["history"].append({"operation": "CLICK", "action": "Pay Now", "committed": True, "approved": "user"})
    w.state.update(bill_facts=dict(FACTS), bill_checked=True, approved_pay="1240", offer="1240")


GATEWAY = "https://pgi.billdesk.com/pgi/ve1_2/pay"


def gateway(text="Pay ₹1,240.00 to BESCOM\nScan the QR code with any UPI app"):
    return fake_page(url=GATEWAY, text=text, actions=[
        {"id": "g1", "kind": "click", "label": "UPI", "role": "tab", "node": 1},
        {"id": "g2", "kind": "click", "label": "Cards", "role": "tab", "node": 2}])


def test_a_upi_qr_for_the_approved_amount_goes_to_the_user_with_app_links(tmp_path, monkeypatch):
    w = bill_worker(tmp_path, page=gateway(), replies=[{"option": "o1"}])
    paid(w)
    w.browser.evaluate = Mock(return_value=LINK)
    w.browser.qr_screenshot = Mock(return_value="cXI=")
    script(monkeypatch, answer())
    w.command("tick")
    sent = w.asked[0]
    assert sent["image"] == "cXI=" and "pay ₹1,240 to BESCOM" in sent["text"]
    assert [link["label"] for link in sent["links"]] == ["UPI app", "GPay", "PhonePe", "Paytm"]
    assert w.state["qr_sent"] and not w.browser.act.called


def test_a_qr_for_another_amount_is_never_sent(tmp_path, monkeypatch):
    w = bill_worker(tmp_path, page=gateway())
    paid(w)
    w.state["payment_id"] = w.memory.payment_started("BESCOM", "bescom_account", "1240", "UPI")
    w.browser.evaluate = Mock(return_value=LINK.replace("1240.00", "12400.00"))
    w.browser.qr_screenshot = Mock(return_value="cXI=")
    w.command("tick")
    assert w.state["status"] == "blocked" and w.asked == []
    assert "₹12,400" in w.told[-1]["text"] and "Nothing was paid" in w.told[-1]["text"]
    assert w.memory.payments("BESCOM", "bescom_account")[0]["status"] == "failed"


def test_a_paid_bill_is_reported_in_code_with_its_reference(tmp_path, monkeypatch):
    thanks = fake_page(url=BOARD, text="Payment Successful\nTransaction Reference BD7H2K99\nAmount ₹ 1,240.00",
                       actions=[{"id": "t1", "kind": "click", "label": "Print Receipt", "role": "button", "node": 1}])
    w = bill_worker(tmp_path, page=thanks)
    paid(w)
    w.state.update(qr_sent=True, payment_id=w.memory.payment_started("BESCOM", "bescom_account", "1240", "UPI"))
    w.browser.evaluate, w.browser.qr_screenshot = Mock(return_value=None), Mock(return_value=None)
    script(monkeypatch, answer(end="done", say="Payment successful", **{**FACTS, "reference": "BD7H2K99"}))
    w.command("tick")
    assert w.state["outcome"] == "paid" and w.told[-1]["text"] == (
        "Paid ₹1,240 to BESCOM for account ••7890. Reference BD7H2K99. It may take a few days to show on BESCOM's "
        "site.")
    row = w.memory.payments("BESCOM", "bescom_account")[0]
    assert (row["status"], row["reference"]) == ("paid", "BD7H2K99")


def test_done_before_any_payment_is_refused(tmp_path, monkeypatch):
    w = bill_worker(tmp_path)
    script(monkeypatch, answer(end="done", say="All good", **FACTS))
    w.command("tick")
    assert w.state["status"] == "ready" and any("DONE refused" in f for f in w.state["feedback"])


def test_a_stop_after_the_yes_says_dont_pay_again(tmp_path, monkeypatch):
    w = bill_worker(tmp_path, page=gateway())
    paid(w)
    w.browser.evaluate, w.browser.qr_screenshot = Mock(return_value=None), Mock(return_value=None)
    script(monkeypatch, answer(end="stop", say="The gateway shows an error."))
    w.command("tick")
    assert w.state["status"] == "blocked" and w.state["outcome"] == "unclear"
    assert "don't pay again" in w.told[-1]["text"]


def test_a_page_opened_by_typing_its_address_never_takes_a_payment(tmp_path, monkeypatch):
    w = bill_worker(tmp_path, page=bill_page(url="https://bescom-pay.example/bill"))
    w.state["typed_hosts"].append("bescom-pay.example")
    w.state.update(bill_facts=dict(FACTS))
    jev_checks(monkeypatch)
    script(monkeypatch, answer([("click", "2", None)], **FACTS))
    w.command("tick")
    assert any("typing its address" in f for f in w.state["feedback"]) and not w.browser.act.called


def test_a_card_box_is_asked_through_the_hidden_field(tmp_path, monkeypatch):
    card = fake_page(url=GATEWAY, text="Card Number\nExpiry\nCVV\nPay ₹1,240.00", actions=[
        {"id": "c1", "kind": "fill", "label": "Card Number", "role": "textbox", "value": "", "node": 1,
         "constraints": {}},
        {"id": "c2", "kind": "click", "label": "Pay ₹1,240.00", "role": "button", "node": 2}])
    w = bill_worker(tmp_path, page=card, replies=[{"text": "4111 1111 1111 1111"}],
                    goal=money.goal_for("BESCOM", "bescom_account", method="card"))
    paid(w)
    w.browser.evaluate, w.browser.qr_screenshot = Mock(return_value=None), Mock(return_value=None)
    seen = script(monkeypatch, answer([("type", "1", "[ask]")]))
    w.command("tick")
    assert w.asked[0]["kind"] == "secret" and "Card Number" in w.asked[0]["text"]
    assert w.browser.act.call_args.kwargs["text"].replace(" ", "") == "4111111111111111"
    assert "4111" not in json.dumps([seen, w.state["history"], w.trace()])


def test_the_gateways_pay_button_rides_on_the_users_yes(tmp_path, monkeypatch):
    card = fake_page(url=GATEWAY, text="Card Number\nPay ₹1,240.00", actions=[
        {"id": "c2", "kind": "click", "label": "Pay ₹1,240.00", "role": "button", "node": 2}])
    w = bill_worker(tmp_path, page=card)
    paid(w)
    w.browser.evaluate, w.browser.qr_screenshot = Mock(return_value=None), Mock(return_value=None)
    jev_checks(monkeypatch, after_commit="processing")
    w.processing_ms = 0
    script(monkeypatch, answer([("click", "1", None)]))
    w.command("tick")
    assert w.browser.act.call_args.args[1]["label"] == "Pay ₹1,240.00" and w.asked == []  # no second yes
    assert w.state["history"][-1]["committed"]


def test_a_higher_payable_asks_a_new_yes(tmp_path, monkeypatch):
    card = fake_page(url=GATEWAY, text="Convenience fee ₹ 23.00\nTotal ₹1,263.00\nPay ₹1,263.00", actions=[
        {"id": "c2", "kind": "click", "label": "Pay ₹1,263.00", "role": "button", "node": 2}])
    w = bill_worker(tmp_path, page=card, replies=[{"option": "o2"}])
    paid(w)
    w.browser.evaluate, w.browser.qr_screenshot = Mock(return_value=None), Mock(return_value=None)
    jev_checks(monkeypatch)
    script(monkeypatch, answer([("click", "1", None)], **{**FACTS, "payable": "₹1,263.00"}))
    w.command("tick")
    assert w.asked[0]["approval"] and "Payable with the site's fee: ₹1,263" in w.asked[0]["text"]
    assert w.state["outcome"] == "not_approved" and not w.browser.act.called


def test_actions_on_elements_the_page_lacks_are_refused_then_the_task_stops(tmp_path, monkeypatch):
    w = bill_worker(tmp_path)
    script(monkeypatch, answer([("click", "99", None)]))
    for _ in range(3):
        w.command("tick")
    assert w.state["status"] == "blocked" and "Nothing was paid" in w.told[-1]["text"]


def test_a_method_the_user_picks_joins_the_goal(tmp_path, monkeypatch):
    w = bill_worker(tmp_path, page=gateway(), replies=[{"option": "o1"}])
    script(monkeypatch, answer(end="ask", say="Pay by UPI or card?", options=["UPI", "Card"]))
    w.command("tick")
    assert w.state["goal"].endswith(", by UPI.") and w.bill["method"] == "UPI"


def test_decode_qr_reads_a_upi_link_from_a_picture():
    import base64
    import io

    zxingcpp = pytest.importorskip("zxingcpp")
    from PIL import Image

    drawn = zxingcpp.create_barcode(LINK, zxingcpp.BarcodeFormat.QRCode).to_image(scale=4)
    buffer = io.BytesIO()
    Image.frombuffer("L", (drawn.shape[1], drawn.shape[0]), bytes(drawn), "raw", "L", 0, 1).save(buffer, "PNG")
    assert module.decode_qr(base64.b64encode(buffer.getvalue()).decode()) == LINK
    assert module.decode_qr("bm90IGFuIGltYWdl") is None


def test_steps_check_every_action_against_the_page():
    page = bill_page()
    plan = actor.steps(answer([("type", "1", "[profile:bescom_account]"), ("click", "2", None), ("wait", None, None),
                               ("check", None, "Bill Amount")]), page)
    assert [p[0] for p in plan][:4] == ["TYPE_TEXT", "CLICK", "WAIT", "CHECK"]
    with pytest.raises(ValueError, match="isn't a box"):
        actor.steps(answer([("type", "2", "x")]), page)
    with pytest.raises(ValueError, match="full web address"):
        actor.steps(answer([("open", None, "bescom.co.in")]), page)
    assert Retry  # imported for the gate's refusals in other tests
