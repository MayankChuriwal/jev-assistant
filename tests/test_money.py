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


def test_job_intent_reads_a_bill_goal_as_a_bill():
    assert money.job_intent(money.goal_for("BESCOM", "bescom_account"))["kind"] == "bill"
    assert money.job_intent("Order Diet Coke on Instamart") is None


INFINIA = {"name": "Infinia", "bank": "HDFC", "earn": "5 per 150", "point": "1.00"}


def test_the_purchase_and_read_goals_round_trip():
    goal = money.voucher_goal("SmartBuy", "Swiggy", 5000, {2000: 2, 1000: 1}, "Infinia", compared="5X reward points")
    assert goal == ("Buy ₹5,000 of Swiggy vouchers on SmartBuy: 2 × ₹2,000 + 1 × ₹1,000, paying with Infinia. "
                    "Compared: 5X reward points.")
    assert money.job_intent(goal) == {"kind": "voucher", "portal": "SmartBuy", "brand": "Swiggy", "amount": 5000,
                                      "split": {2000: 2, 1000: 1}, "card": "Infinia",
                                      "compared": "5X reward points"}
    any_goal = money.voucher_goal("ShopWise", "Swiggy", 5000, None, "Platinum Travel")
    assert "any vouchers that make ₹5,000" in any_goal and money.job_intent(any_goal)["split"] is None
    assert money.job_intent(any_goal)["compared"] is None
    read = money.read_goal("iShop", "Swiggy", 5000)
    assert read.endswith("Buy nothing.")
    assert money.job_intent(read) == {"kind": "voucher_read", "portal": "iShop", "brand": "Swiggy", "amount": 5000}


def test_split_makes_the_fewest_vouchers_and_nearest_offers_totals():
    assert money.split([250, 500, 1000, 2000], 5000) == {2000: 2, 1000: 1}
    assert money.split([1000, 2000], 1500) is None
    assert money.nearest(1200, [250, 500], None) == [1250, 1000]
    assert money.counted({2000: 2, 1000: 1}) == "2 × ₹2,000 + 1 × ₹1,000"


def test_rewards_are_read_from_the_offers_words():
    assert money.reward("Upto 5X Reward Points / 5% Cashback") == {"kind": "multiplier", "value": Decimal(5)}
    assert money.reward("Earn upto 10% GV COINS") == {"kind": "back", "value": Decimal(10)}
    assert money.reward("Great deals") == {"kind": None, "value": None}
    assert money.reward(None) == {"kind": None, "value": None}


def test_value_turns_an_offer_into_an_effective_price():
    got = money.value({"reward": "5X Reward Points"}, INFINIA, 5000)
    assert (got["points"], got["back"], got["effective"], got["ranked"]) == (833, Decimal("833.00"),
                                                                             Decimal("4167.00"), True)
    capped = money.value({"reward": "5X Reward Points"}, INFINIA, 50000, cap="3000 points")
    assert capped["points"] == 3000
    fee = money.value({"reward": "6X", "fee": "2.5%"}, {**INFINIA, "earn": "2 per 100", "point": "0.25"}, 4000)
    assert fee["fee"] == Decimal("100.00") and fee["payable"] == Decimal("4100.00")
    off = money.value({"reward": "", "discount": "2%"}, INFINIA, 5000)
    assert off["ranked"] and off["effective"] == Decimal("4900.00")
    back = money.value({"reward": "5% cashback"}, INFINIA, 30000, cap="₹1,000")
    assert back["back"] == Decimal("1000")


def test_an_offer_with_no_known_value_is_unranked_and_ranks_last():
    unknown = money.value({"reward": "5X Reward Points"}, {"name": "Kotak League", "bank": "Kotak"}, 5000)
    assert unknown["ranked"] is False and unknown["effective"] is None
    known = money.value({"reward": "5X Reward Points"}, INFINIA, 5000)
    assert money.rank([unknown, known]) == [known, unknown]


def test_a_cart_is_read_as_counts_of_amounts():
    assert money.cart_split("2 × ₹2,000 + 1 × ₹1,000") == {2000: 2, 1000: 1}
    assert money.cart_split("Swiggy ₹2,000 x 2, Swiggy ₹1,000 x 1") == {2000: 2, 1000: 1}
    assert money.cart_split("nothing here") is None


def test_the_approval_says_what_is_bought_and_flags_a_worse_reward():
    intent = money.job_intent(money.voucher_goal("SmartBuy", "Swiggy", 5000, {2000: 2, 1000: 1}, "Infinia",
                                                 compared="10X reward points"))
    lines = money.voucher_lines(intent, {"payable": "₹5,000", "reward": "5X Reward Points"})
    assert lines[:4] == ["Buy ₹5,000 of Swiggy vouchers on SmartBuy", "2 × ₹2,000 + 1 × ₹1,000",
                         "Paying with Infinia", "Payable: ₹5,000"]
    assert "Reward shown now: 5X Reward Points" in lines
    assert any(line.startswith("Lower than when compared") for line in lines)
    fee = money.voucher_lines(intent, {"payable": "₹5,125", "reward": "10X reward points"})
    assert "Payable with the site's fee: ₹5,125" in fee and not any("Lower" in line for line in fee)


def test_codes_and_pins_are_masked_and_the_order_id_is_kept():
    page = ("Order ID: GV7Q2M44K1\nVoucher Code: ABCD-EFGH-1234-5678\nPIN: 482913\nVoucher Code\nQWER7890TYUI\n"
            "PIN\n551177\nOrder number\nHSB922376724-2892849964\nSwiggy ₹2,000 × 2\nGift card XK29PLM00Q7Z1")
    masked = money.mask_codes(page)
    for code in ("ABCD-EFGH-1234-5678", "482913", "QWER7890TYUI", "551177", "XK29PLM00Q7Z1"):
        assert code not in masked
    assert "GV7Q2M44K1" in masked and "HSB922376724-2892849964" in masked and "₹2,000 × 2" in masked


def test_a_voucher_bought_today_is_asked_about_again():
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    row = {"status": "paid", "created_at": now - timedelta(hours=3), "amount": 5000, "reference": "GV1"}
    assert money.paid_before([row], now, money.VOUCHER_WINDOW) == row
    assert money.paid_before([{**row, "created_at": now - timedelta(hours=30)}], now, money.VOUCHER_WINDOW) is None
    assert money.voucher_key("Swiggy Money") == "voucher_swiggy_money"


def test_a_compared_reward_with_its_own_full_stops_still_reads_back():
    goal = money.voucher_goal("iShop", "Swiggy", 2000, {2000: 1}, "Sapphiro", compared="6X Reward Points. T&C apply.")
    intent = money.job_intent(goal)
    assert intent["card"] == "Sapphiro" and intent["compared"] == "6X Reward Points T&C apply"
    assert money.job_intent(money.voucher_goal("iShop", "Swiggy", 2000, {2000: 1}, "Sapphiro",
                                               compared="2.5% back"))["compared"] == "2.5% back"
