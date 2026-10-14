"""money.py: a bill payment's rules in code. No browser, no models."""

from decimal import Decimal

import pytest

from assistant import money


def test_a_bills_goal_carries_its_intent_and_reads_back():
    goal = money.goal_for("BESCOM", "bescom_account")
    assert goal == "Pay the BESCOM bill for account [profile:bescom_account]: the amount due."
    assert money.bill_intent(goal) == {"biller": "BESCOM", "account": "bescom_account", "amount": None,
                                       "method": None}
    exact = money.goal_for("Tata Play", "tata_play_account", 300, "UPI")
    assert exact == "Pay the Tata Play bill for account [profile:tata_play_account]: exactly ₹300, by UPI."
    assert money.bill_intent(exact)["amount"] == 300 and money.bill_intent(exact)["method"] == "UPI"
    assert money.bill_intent("Buy ₹500 of Swiggy gift vouchers on SmartBuy: 1 × ₹500.") is None


def test_an_accounts_key_comes_from_the_billers_name():
    assert money.account_key("BESCOM") == "bescom_account"
    assert money.account_key("Tata Power-DDL") == "tata_power_ddl_account"
    assert money.account_key("1st Power Co") == "b_1st_power_co_account"
    assert len(money.account_key("x" * 80)) <= 40


@pytest.mark.parametrize("text, value", [("₹ 1,240", Decimal("1240")), ("Rs. 1240.50", Decimal("1240.50")),
                                         ("1,23,456", Decimal("123456")), ("INR 99", Decimal("99")),
                                         ("due soon", None)])
def test_rupees_read_the_indian_ways_of_writing_them(text, value):
    assert money.rupees(text) == value


def test_a_fact_counts_only_when_the_page_shows_it():
    page = "Consumer Name: M K CHURIWAL\nNet Amount Payable  ₹ 1,240.00\nDue Date 20-10-2026"
    assert money.grounded("₹1,240", page)
    assert money.grounded("1240", page)
    assert not money.grounded("₹1,420", page)
    assert money.grounded("m k churiwal", page)
    assert not money.grounded("RAJESH CHURIWAL", page)
    assert not money.grounded("", page)


@pytest.mark.parametrize("on_bill, verdict", [
    ("M K CHURIWAL", "match"), ("MAYANK CHURIWAL", "match"), ("Mr. Mayank Kumar Churiwal", "match"),
    ("[profile:name]", "match"), ("M K [profile:name]", "match"), ("[profile:name] [profile:name]", "match"),
    ("RAJESH [profile:name]", "mismatch"), ("RAJESH CHURIWAL", "mismatch"), ("SUNITA SHARMA", "mismatch"),
    ("", None)])
def test_the_name_on_a_bill_matches_by_words_and_initials(on_bill, verdict):
    assert money.name_match(on_bill, "Mayank Churiwal") == verdict


def test_the_approval_names_the_bill_in_code():
    intent = money.bill_intent(money.goal_for("BESCOM", "bescom_account", method="UPI"))
    facts = {"name": "M K CHURIWAL", "amount_due": "₹1,240", "due_date": "20 Oct 2026"}
    assert money.approval_lines(intent, facts, account="1234567890") == [
        "Pay ₹1,240 BESCOM bill", "Account ••7890", "Name on the bill: M K CHURIWAL", "Due 20 Oct 2026", "By UPI"]
    hidden = money.approval_lines(intent, {**facts, "name": "M K [profile:name]"}, account="1234567890")
    assert "Name on the bill: matches yours" in hidden
    fee = money.approval_lines(intent, {**facts, "payable": "₹1,263"}, account="1234567890")
    assert "Payable with the site's fee: ₹1,263" in fee


def test_an_exact_amount_is_what_the_approval_pays():
    intent = money.bill_intent(money.goal_for("Tata Play", "tata_play_account", 300))
    assert money.approval_lines(intent, {}, account="")[0] == "Pay ₹300 Tata Play bill"
    assert money.to_pay(intent, {"amount_due": "₹549"}) == Decimal("300")
    assert money.to_pay(money.bill_intent(money.goal_for("BESCOM", "bescom_account")),
                        {"amount_due": "₹549"}) == Decimal("549")


LINK = "upi://pay?pa=bescom.billdesk@hdfcbank&pn=BESCOM&am=1240.00&tr=TXN1&cu=INR"


def test_a_upi_link_is_found_in_page_text_and_read():
    assert money.upi_link(f'<a href="{LINK}">Pay</a>') == LINK
    assert money.upi_link("no link here") is None
    assert money.upi_params(LINK)["pn"] == "BESCOM" and money.upi_params(LINK)["am"] == "1240.00"


def test_a_qr_is_sent_only_for_the_approved_amount():
    assert money.qr_problem(LINK, Decimal("1240")) is None
    assert "₹1,400" in money.qr_problem(LINK.replace("1240.00", "1400"), Decimal("1240"))
    assert "no amount" in money.qr_problem("upi://pay?pa=x@y&pn=X", Decimal("1240"))
    assert money.qr_problem("upi://pay?pn=X&am=1240", Decimal("1240"))  # no payee address


def test_app_links_open_the_same_payment():
    links = dict((label, url) for label, url in money.app_links(LINK))
    assert links["UPI app"] == LINK
    assert links["GPay"].startswith("gpay://upi/pay?") and "am=1240.00" in links["GPay"]
    assert links["PhonePe"].startswith("phonepe://pay?") and links["Paytm"].startswith("paytmmp://pay?")


def test_paid_before_means_a_payment_in_the_window_or_one_still_open():
    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 10, 14, tzinfo=timezone.utc)
    old = {"status": "paid", "created_at": now - timedelta(days=40), "amount": Decimal("900"), "reference": "R1"}
    recent = {"status": "paid", "created_at": now - timedelta(days=3), "amount": Decimal("1240"), "reference": "R2"}
    open_ = {"status": "unclear", "created_at": now - timedelta(days=60), "amount": Decimal("1240"), "reference": ""}
    failed = {"status": "failed", "created_at": now - timedelta(days=1), "amount": Decimal("1240"), "reference": ""}
    assert money.paid_before([old, failed], now) is None
    assert money.paid_before([old, recent], now) is recent
    assert money.paid_before([open_], now) is open_
