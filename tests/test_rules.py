"""Pure rules the agent applies before acting."""

import pytest

from assistant import rules


@pytest.mark.parametrize("label", ["Pay ₹79", "Place order", "Confirm booking", "Request UberGo", "Cancel order",
                                   "Submit complaint", "Delete account", "Buy now", "Pay later"])
def test_commit_clicks_are_recognised(label):
    assert rules.commit_label("CLICK", {"label": label}, {}) == label


@pytest.mark.parametrize("label", ["Send OTP", "Continue", "Verify", "Book a demo", "Payment options", "Paytm wallet",
                                   "Proceed to checkout"])
def test_ordinary_clicks_are_not_commits(label):
    assert rules.commit_label("CLICK", {"label": label}, {}) is None


def test_enter_commits_only_through_its_forms_submit_button():
    field = {"kind": "fill", "label": "Card number", "submit": "Place order"}
    assert rules.commit_label("PRESS_KEY", field, {}, key="Enter") == "Place order"
    assert rules.commit_label("PRESS_KEY", field, {}, key="Tab") is None
    assert rules.commit_label("PRESS_KEY", {"kind": "fill", "label": "Search"}, {}, key="Enter") is None
    dialog = {"text": "Browser confirm dialog: Delete this address?"}
    assert rules.commit_label("ACCEPT_DIALOG", {"label": "OK"}, dialog)
    assert rules.commit_label("TYPE_TEXT", {"label": "Pay note"}, {}) is None


def test_verbatim_ignores_whitespace_but_not_content():
    assert rules.verbatim("Order  placed", "Thanks!\nOrder placed #A1")
    assert not rules.verbatim("₹790", "Total ₹79") and not rules.verbatim("", "anything")


def test_approvals_expire_on_host_steps_or_a_changed_total():
    page = {"url": "https://www.swiggy.com/checkout", "text": "Total ₹79 · Pay ₹79"}
    approval = {"action_label": "Pay ₹79", "amount": "₹79", "host": "www.swiggy.com", "step": 4}
    assert rules.approval_valid(approval, page, 23)
    assert not rules.approval_valid(approval, page, 24)
    assert not rules.approval_valid({**approval, "host": "evil.test"}, page, 5)
    assert not rules.approval_valid(approval, {**page, "text": "Total ₹99"}, 5)
    assert rules.approval_valid({**approval, "amount": ""}, {**page, "text": "Submit complaint"}, 5)
    assert not rules.approval_valid(None, page, 5)


@pytest.mark.parametrize("value, action, problem", [
    ("4829", {"group": [1, 2, 3, 4, 5, 6], "constraints": {"inputmode": "numeric"}}, "exactly 6"),
    ("48291a", {"group": [1, 2, 3, 4, 5, 6], "constraints": {"inputmode": "numeric"}}, "6 digits"),
    ("12a", {"constraints": {"inputmode": "numeric", "maxlength": 3}}, "numbers only"),
    ("1234", {"constraints": {"inputmode": "numeric", "maxlength": 3}}, "at most 3"),
    ("m@x", {"constraints": {"type": "email"}}, "email"),
    ("20/09/2026", {"constraints": {"type": "date"}}, "YYYY-MM-DD"),
    ("call me", {"constraints": {"type": "tel"}}, "phone"),
    ("AB12", {"constraints": {"pattern": "[0-9]{4}"}}, "format"),
])
def test_bad_answers_are_caught_before_typing(value, action, problem):
    assert problem in rules.format_error(rules.clean(value, action), action)


@pytest.mark.parametrize("value, action", [
    ("482 913", {"group": [1, 2, 3, 4, 5, 6], "constraints": {"inputmode": "numeric"}}),
    ("123", {"constraints": {"inputmode": "numeric", "maxlength": 3}}),
    ("+91 98765-43210", {"constraints": {"type": "tel"}}),
    ("2026-09-20", {"constraints": {"type": "date"}}),
    ("ABCD", {"constraints": {"pattern": "(?=[A-Z])\\p{Lu}+"}}),  # JavaScript-only syntax: the site judges
    ("anything", {}),
])
def test_good_answers_pass(value, action):
    assert rules.format_error(rules.clean(value, action), action) is None


def test_codes_lose_their_spaces_and_cvv_fields_allow_two_tries():
    assert rules.clean(" 482 913 ", {"group": [1, 2, 3, 4, 5, 6]}) == "482913"
    assert rules.clean(" Home ", {}) == "Home"
    assert rules.attempt_limit("card.hdfc.cvv") == 2 and rules.attempt_limit(None) == 3


def test_a_code_group_is_judged_as_a_whole_not_by_each_boxs_maxlength():
    group = {"group": [1, 2, 3, 4, 5, 6], "constraints": {"inputmode": "numeric", "maxlength": 1}}
    assert rules.format_error(rules.clean("482 913", group), group) is None


def test_enter_or_space_on_the_page_uses_the_focused_control():
    page_key = {"kind": "key", "node": None, "label": "The page"}
    assert rules.commit_label("PRESS_KEY", page_key, {}, key="Space", focused="Pay ₹79") == "Pay ₹79"
    assert rules.commit_label("PRESS_KEY", page_key, {}, key="Enter", focused="Search") is None
    assert rules.commit_label("PRESS_KEY", page_key, {}, key="Tab", focused="Pay ₹79") is None


def test_approved_labels_match_by_their_words():
    assert rules.overlap("Place your order", "Place order")
    assert not rules.overlap("Pay ₹79", "Submit complaint") and not rules.overlap("Pay ₹79", "Delete address")


def test_an_approved_amount_must_appear_whole():
    approval = {"host": "shop.test", "step": 0, "amount": "₹79"}

    def page(text):
        return {"url": "https://shop.test/pay", "text": text}

    assert rules.approval_valid(approval, page("Total ₹79. Pay now"), 1)
    assert rules.approval_valid(approval, page("Total ₹79, delivery free"), 1)
    for text in ("Total ₹790", "Total ₹79.50"):
        assert not rules.approval_valid(approval, page(text), 1), text
    assert not rules.approval_valid({**approval, "amount": "79"}, page("Total 179"), 1)


def test_codes_are_secret_but_a_phone_number_for_a_code_is_not():
    for text in ("Please enter the OTP you received.", "Enter the 4-digit verification code sent via SMS",
                 "Enter the code we sent to your email", "Enter your UPI PIN", "Your Uber account password"):
        assert rules.asks_for_secret(text), text
    for text in ("Please provide your phone number or email to receive the one-time code.", "Enter your PIN code",
                 "Which address?"):
        assert not rules.asks_for_secret(text), text
