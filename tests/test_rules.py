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


@pytest.mark.parametrize("amount, text", [
    ("₹50", "Diet Coke ₹50.00"), ("₹1,299", "To pay ₹1,299.00"), ("Rs. 79", "Total Rs. 79.00"),
    ("₹ 79", "Total ₹ 79.0"), ("₹50.00", "Diet Coke ₹50"), ("₹1,299.00", "To pay ₹1,299"), ("₹50.0", "Item ₹50.00"),
    ("€50", "Total €50,00"), ("₹121", "To pay ₹121"), ("₹79.50", "Total ₹79.50")])
def test_a_zero_fraction_on_either_side_is_the_same_amount(amount, text):
    assert rules.amount_shown(amount, text)


@pytest.mark.parametrize("amount, text", [
    ("₹79", "Total ₹79.50"), ("₹79", "Total ₹79.05"), ("₹79", "Total ₹790.00"), ("₹79", "Total ₹179.00"),
    ("₹79", "Total ₹79.001"), ("₹1", "Total ₹1,000"), ("₹1", "Total ₹1,00,000"), ("₹50", "Total ₹50,000.00"),
    ("₹79.00", "Total ₹79.50"), ("₹79.00", "Total ₹790"), ("₹1,000", "Total ₹1"), ("₹1,000.00", "Total ₹1"),
    ("79.00", "Total 179"), ("€1", "Total €1.000"), ("€1", "Total €1,000"), ("₹50", "Total ₹50,0")])
def test_only_a_zero_fraction_is_ignored_never_digits_or_a_thousands_group(amount, text):
    assert not rules.amount_shown(amount, text)


def test_an_approved_amount_holds_while_the_page_shows_the_same_value_with_or_without_zeros():
    approval = {"host": "shop.test", "step": 0, "amount": "₹121"}
    page = {"url": "https://shop.test/pay", "text": "Item total ₹91.00\nTo pay ₹121.00"}
    assert rules.approval_valid(approval, page, 1)
    assert rules.approval_valid({**approval, "amount": "₹121.00"}, {**page, "text": "To pay ₹121"}, 1)
    assert not rules.approval_valid(approval, {**page, "text": "To pay ₹121.50"}, 1)


def test_codes_are_secret_but_a_phone_number_for_a_code_is_not():
    for text in ("Please enter the OTP you received.", "Enter the 4-digit verification code sent via SMS",
                 "Enter the code we sent to your email", "Enter your UPI PIN", "Your Uber account password"):
        assert rules.asks_for_secret(text), text
    for text in ("Please provide your phone number or email to receive the one-time code.", "Enter your PIN code",
                 "Which address?"):
        assert not rules.asks_for_secret(text), text


def test_checkout_steps_are_not_commits_but_final_buttons_are():
    page = {"text": ""}
    click = lambda label: rules.commit_label("CLICK", {"label": label}, page)  # noqa: E731
    maybe = lambda label: rules.maybe_commit_label("CLICK", {"label": label}, page)  # noqa: E731
    assert click("Proceed to Buy (1 item)") is None and click("Proceed to Pay") is None
    # not outright commits, but maybe ones: Jev's final check decides (spec 8.1), and only "step" ≥ 0.8 clears them
    assert maybe("Proceed to Buy (1 item)") and maybe("Proceed to Pay") and maybe("Proceed to checkout")
    assert click("Place your order") and click("Pay ₹79") and click("Complete purchase") and click("Buy Now")


# The final controls of the bench sites: a commit word the rule matches outright, which Jev can never clear.
OUTRIGHT = ["Book Bike", "Book Ride", "Book Cab", "Book Auto", "Book a table", "Book tickets", "End membership",
            "End my subscription", "Cancel Membership", "Cancel my membership", "Make Payment", "Make a payment",
            "Submit order", "Submit the order", "Order Now", "Recharge Now", "Complete payment",
            "Complete the booking", "Request Uber Go AC", "Pay ₹79", "Place order", "Pay now"]
# Words that may commit: Jev's final check clears them only at "step" ≥ 0.8.
MAYBE = ["Book Ola Mini", "Confirm UberGo", "Continue to cancel", "Submit", "Send message", "Send", "Reserve",
         "Transfer ₹500", "Recharge", "Subscribe", "End trip", "Cancel request", "Proceed to Recharge",
         "Proceed to Pay", "Proceed to payment", "Make the final payment", "Complete", "Place bid",
         "Request a ride", "[secret:answer1] ₹79", "Hi [profile:name]"]
NEITHER = ["Add to cart", "Search", "Continue", "Verify", "Send OTP", "Send code", "Resend", "Resend OTP",
           "Send verification code", "Home", "Cancel", "Payment options"]


@pytest.mark.parametrize("label", OUTRIGHT)
def test_final_controls_of_the_bench_sites_are_outright_commits(label):
    assert rules.commit_label("CLICK", {"label": label}, {}) == label


@pytest.mark.parametrize("label", ["Continue Booking", "Continue booking", "Continue to booking"])
def test_continuing_a_booking_is_an_outright_commit(label):  # live rapido: the last control under the fares and "Cash"
    assert rules.commit_label("CLICK", {"label": label}, {}) == label


@pytest.mark.parametrize("label", MAYBE)
def test_words_that_may_commit_are_maybe_commits_not_outright_ones(label):
    assert rules.commit_label("CLICK", {"label": label}, {}) is None
    assert rules.maybe_commit_label("CLICK", {"label": label}, {}) == label


@pytest.mark.parametrize("label", NEITHER)
def test_ordinary_controls_and_code_sends_are_neither(label):
    assert rules.commit_label("CLICK", {"label": label}, {}) is None
    assert rules.maybe_commit_label("CLICK", {"label": label}, {}) is None


def test_maybe_commits_read_the_same_words_as_commits_for_each_operation():
    assert rules.maybe_commit_label("DOUBLE_CLICK", {"label": "Reserve"}, {}) == "Reserve"
    field = {"kind": "fill", "label": "Message", "node": 4, "submit": "Send message"}
    assert rules.maybe_commit_label("PRESS_KEY", field, {}, key="Enter") == "Send message"
    assert rules.maybe_commit_label("PRESS_KEY", field, {}, key="Tab") is None
    page_key = {"kind": "key", "node": None, "label": "The page"}
    confirm = rules.maybe_commit_label("PRESS_KEY", page_key, {}, key="Space", focused="Confirm UberGo")
    assert confirm == "Confirm UberGo"
    dialog = {"text": "Browser confirm dialog: Confirm UberGo for ₹212?"}
    assert rules.maybe_commit_label("ACCEPT_DIALOG", {"label": "OK"}, dialog) == dialog["text"]
    spot = {"label": "Green button", "text": "Book Ola Mini"}
    assert rules.maybe_commit_label("LOOK", spot, {}) == "Green button"
    assert rules.maybe_commit_label("TYPE_TEXT", {"label": "Send a note"}, {}) is None


@pytest.mark.parametrize("label", ["CVV", "card number", "Card Number", "card no", "Card No.", "card details",
                                   "Expiration date", "expiry", "Expiry date", "UPI PIN"])
def test_card_detail_labels_are_recognised(label):
    assert rules.CARD_DETAIL.search(label), label


@pytest.mark.parametrize("label", ["promo code", "card holder name", "gift card code", "Phone number", "Notes"])
def test_other_labels_are_not_card_details(label):
    assert not rules.CARD_DETAIL.search(label), label


def test_a_captcha_challenge_is_told_from_a_badge():
    challenge = "Verify\nSelect all images with traffic lights\nClick verify once there are none left"
    assert rules.captcha_challenge({"text": challenge})
    assert rules.captcha_challenge({"text": "Before you continue\n☐ I'm not a robot"})
    assert not rules.captcha_challenge({"text": "Sign in\nThis site is protected by reCAPTCHA and the Google Terms"})
    assert not rules.captcha_challenge({"text": ""}) and not rules.captcha_challenge({})


def test_an_error_page_is_short_or_titled_so_and_an_error_word_far_down_a_long_page_is_not():
    assert rules.error_page({"title": "Shop", "text": "404\nPage not found\nGo to the home page"})
    assert rules.error_page({"title": "Oops", "text": "Something went wrong. Please try again."})
    assert rules.error_page({"title": "404: This page could not be found.", "text": "x" * 3000})
    ordinary = "Diet Coke 300 ml ₹40\n" + "Restaurants near you\n" * 100 + "If you see an error, page not found, retry"
    assert len(ordinary) > 1500 and not rules.error_page({"title": "Shop", "text": ordinary})
    short = "Restaurants near you\n" * 20 + "Report an error: page not found"  # short, but the words come late
    assert 300 < len(short) < 1500 and not rules.error_page({"title": "Shop", "text": short})



def test_a_captcha_challenge_drawn_as_a_control_counts_but_a_badge_never_does():
    box = {"text": "Log in", "actions": [{"kind": "click", "label": "I'm not a robot", "role": "checkbox"}]}
    assert rules.captcha_challenge(box)
    badge = {"text": "Log in", "actions": [{"kind": "click", "label": "reCAPTCHA", "role": "link"}]}
    assert not rules.captcha_challenge(badge)


# Live bench B: a one-time code in a tel box is a code, not a phone number.

OTP_TEL = {"kind": "fill", "label": "Enter 4 digit OTP", "constraints": {"type": "tel", "maxlength": 4}}


@pytest.mark.parametrize("value", ["4829", " 48 29 ", "48-29"])
def test_a_code_in_a_tel_box_is_typed_as_its_digits(value):
    assert rules.format_error(rules.clean(value, OTP_TEL), OTP_TEL) is None
    assert rules.clean(value, OTP_TEL) == "4829"


@pytest.mark.parametrize("value, problem", [("48a9", "digits"), ("48291", "at most 4")])
def test_a_code_in_a_tel_box_is_still_held_to_digits_and_its_length(value, problem):
    assert problem in rules.format_error(rules.clean(value, OTP_TEL), OTP_TEL)


def test_a_cvv_in_a_tel_box_is_a_code_and_a_phone_box_is_still_a_phone():
    cvv = {"kind": "fill", "label": "CVV", "constraints": {"type": "tel", "maxlength": 3}}
    assert rules.format_error("123", cvv) is None
    phone = {"kind": "fill", "label": "Mobile number", "constraints": {"type": "tel"}}
    assert "phone" in rules.format_error("call me", phone) and rules.format_error("+91 98765 43210", phone) is None
    assert rules.clean("+91 98765 43210", phone) == "+91 98765 43210"  # a phone number keeps its spaces


ORDERS = ["Order one 500 ml pack of Amul Taaza milk on Zepto", "Order one Diet Coke 300 ml can on Swiggy Instamart",
          "Order one pack of Britannia white bread on Blinkit", "Buy one boAt Airdopes 141 on Amazon.in",
          "Order one Margherita pizza from Domino's on Swiggy", "Order a pack of 6 eggs", "order 2 cokes",
          "Order the cheapest atta", "Book 2 tickets on BookMyShow", "Pay my BESCOM electricity bill on Amazon.in",
          "Reorder my last Swiggy order", "Fill the pizza order form at https://httpbin.org/forms/post and submit it"]
LOOKUPS = ["Download the invoice for my latest Amazon.in order", "Where is my latest Flipkart order?",
           "What did I order in my last Swiggy order and how much did it cost?", "Show my orders",
           "Check the order status of my last order", "Is my latest Amazon.in order eligible for return?"]


@pytest.mark.parametrize("goal", ORDERS)
def test_an_order_or_booking_goal_is_commerce(goal):
    assert rules.commerce_goal(goal)


@pytest.mark.parametrize("goal", LOOKUPS)
def test_an_order_named_as_a_thing_the_user_has_is_not_commerce(goal):
    assert not rules.commerce_goal(goal)


@pytest.mark.parametrize("goal, skill", [
    ("Find the cheapest hotel in Goa rated above 4 for 2 adults next Saturday to Sunday on MakeMyTrip", "travel"),
    ("Compare the price of the Apple iPhone 16 128 GB on Flipkart and Amazon.in and tell me which is cheaper", None),
    ("Find the cheaper 1 kg pack of Aashirvaad atta between Zepto and Blinkit", "groceries"),
    ("What is the price of the Apple iPhone 16 128 GB on Flipkart?", "shopping"),
    ("Where is my latest Flipkart order?", "account"), ("Prices of atta on Zepto and Blinkit", "lookup"),
    ("What all autopay I have right now?", "account"), ("what subscriptions am I paying for?", "account"),
    ("how much do I pay for ChatGPT every month?", "account"), ("Show my autopays", "account"),
    ("List my UPI mandates", "account")])
def test_a_goal_that_only_finds_or_compares_is_a_lookup(goal, skill):
    assert rules.lookup_goal(goal, skill)
    assert not rules.commerce_goal(goal)  # “do I pay”, “am I paying”: a question about payments, none to make


@pytest.mark.parametrize("goal, skill", [
    ("Order one Diet Coke 300 ml can on Swiggy Instamart", "groceries"), ("Book an Uber to the airport", "lookup"),
    ("Fill the web form at https://www.selenium.dev/selenium/web/web-form.html with the text Hello and submit it",
     "forms"), ("Cancel my latest undelivered Flipkart order", "account"), ("Cancel my Amazon Prime membership", None),
    ("Find the feedback form on shop.test and send it", "lookup"), ("Download the invoice for my last order", None),
    ("Recharge my Jio number with the ₹299 plan on Paytm", "bills-recharges"), ("Pay my BESCOM bill", None),
    ("cancel my ChatGPT subscription", "account"), ("Show me how to pay my BESCOM bill and pay it", None)])
def test_a_goal_that_orders_submits_or_cancels_is_no_lookup(goal, skill):
    assert not rules.lookup_goal(goal, skill)


def test_an_error_page_by_its_opening_words_must_be_shorter_than_1500_characters():
    opening = "404 not found\n"
    assert rules.error_page({"title": "Shop", "text": opening + "x" * (1499 - len(opening))})
    long = opening + "x" * 1600
    assert len(long) > 1500 and not rules.error_page({"title": "Shop", "text": long})
    assert rules.error_page({"title": "404 not found", "text": long})  # the title still says so


@pytest.mark.parametrize("goal, wanted", [
    ("Download the invoice for my latest Amazon.in order", True), ("Save my BESCOM bill receipt as a PDF", True),
    ("Export my bank statement from HDFC", True), ("Where is my latest Flipkart order?", False),
    ("Save my address as Home on Swiggy", False), ("How do I download my Aadhaar card?", False)])
def test_a_goal_that_downloads_or_saves_a_document_needs_a_file(goal, wanted):
    assert rules.download_goal(goal) is wanted


@pytest.mark.parametrize("label", ["Track, return, or buy things again", "Buy it again", "Buy again"])
def test_buying_again_opens_a_list_and_is_no_commit(label):
    assert rules.commit_label("CLICK", {"label": label}, {}) is None


def test_buy_is_still_a_commit_when_it_is_not_buying_again():
    assert rules.commit_label("CLICK", {"label": "Buy"}, {}) == "Buy"
    assert rules.commit_label("CLICK", {"label": "Buy now"}, {}) == "Buy now"


# A click reads the control's own name and the words it shows: a commit word in either is a commit. The context
# added to a label to tell same-named controls apart is never read.


def shows(name, shown, context=""):
    return {"kind": "click", "name": name, "shown": shown,
            "label": f"{name} (shows “{shown}”)" + (f" ({context})" if context else "")}


def test_a_commit_word_the_control_shows_under_another_name_is_a_commit():
    probe = shows("Cart summary", "Place order ₹121")  # the review's probe: it ran with no approval
    assert rules.commit_label("CLICK", probe, {}) == "Place order ₹121"
    assert rules.commit_label("DOUBLE_CLICK", probe, {}) == "Place order ₹121"
    assert rules.commit_label("CLICK", shows("Make payment, tap to go to payment", "Proceed to Pay"), {}) == \
        "Make payment, tap to go to payment"  # the name's own commit word: named as before
    step = shows("Continue", "Proceed to Pay")
    assert rules.commit_label("CLICK", step, {}) is None and rules.maybe_commit_label("CLICK", step, {}) == \
        "Proceed to Pay"  # a checkout step on show: a maybe-commit, for Jev's final check


def test_the_context_added_to_tell_controls_apart_is_never_read_as_what_the_control_does():
    add = {"kind": "click", "name": "ADD", "label": "ADD (Pay ₹79 combo: 1 of 2)"}
    assert rules.commit_label("CLICK", add, {}) is None and rules.maybe_commit_label("CLICK", add, {}) is None
    named = {**shows("Sizes", "300 ml", context="in “Place order”"), "name": "Sizes"}
    assert rules.commit_label("CLICK", named, {}) is None and rules.maybe_commit_label("CLICK", named, {}) is None


# ---- Demo flows: NPCI mandates and Experian disputes


def test_a_text_captcha_with_a_refresh_button_is_a_captcha():
    page = {"text": "Please enter a mobile number\nSecurity Verification\nContinue",
            "actions": [{"label": "Refresh CAPTCHA"}, {"label": "Security Verification"}]}
    assert rules.captcha_challenge(page)
    assert not rules.captcha_challenge({"text": "This site is protected by reCAPTCHA.", "actions": []})


@pytest.mark.parametrize("label", ["Revoke", "Revoke mandate", "Cancel mandate", "Cancel AutoPay", "Pause autopay",
                                   "De-link UPI Number", "Delink"])  # MyUPI: payments to the number stop
def test_revoking_or_cancelling_a_mandate_always_asks(label):
    assert rules.commit_label("CLICK", {"label": label, "name": label}, {"text": ""}) == label


def test_a_tab_listing_revoked_mandates_is_no_commit():
    assert rules.commit_label("CLICK", {"label": "Revoked", "name": "Revoked"}, {"text": ""}) is None


@pytest.mark.parametrize("goal, mandates", [
    ("Cancel my Netflix subscription", True), ("stop my Spotify autopay", True), ("cancel my netflix", True),
    ("Revoke the YouTube Premium UPI mandate", True), ("What all autopay I have right now?", True),
    ("what subscriptions am I paying for?", True), ("pause my Google Play autopay", True),
    ("how much do I pay for ChatGPT every month?", True), ("resume my Spotify", True),
    ("Show my netflix ubscriptions", True),  # a typo: the brand still names it
    ("Cancel my Amazon Prime membership", False), ("Order one Diet Coke", False), ("find a movie on Netflix", False)])
def test_a_goal_about_the_users_upi_mandates_is_recognised(goal, mandates):
    assert bool(rules.MANDATE_GOAL.search(goal)) is mandates


@pytest.mark.parametrize("goal, label, wrong", [
    ("cancel my netflix subscription", "Cancel (in “GOOGLE PLAY”)", "GOOGLE PLAY"),  # guessed: Netflix via the store
    ("cancel my netflix subscription", "Cancel (in “AISLE”)", "AISLE"),
    ("cancel my ChatGPT subscription", "Cancel (in “OPENAI LLC”)", None),  # the company that bills for it
    ("pause my Google Play autopay", "Pause (in “GOOGLE PLAY”)", None),
    ("cancel my aisle", "Cancel (in “AISLE”)", None),
    ("cancel my netflix subscription", "Back", None),  # no card's action
    ("cancel my latest Flipkart order", "Cancel (in “Order #OD123”)", None)])  # not about a mandate
def test_a_mandates_action_on_a_payee_the_goal_doesnt_name_is_caught(goal, label, wrong):
    assert rules.wrong_card(goal, {"label": label}) == wrong


TEN_DIGITS = {"constraints": {"type": "tel", "maxlength": 10, "pattern": "[0-9]{10}", "autocomplete": "tel-national"}}


@pytest.mark.parametrize("value, typed", [("+91 98765 43210", "9876543210"), ("+919876543210", "9876543210"),
                                         ("98765 43210", "9876543210"), ("+1 415 555 0100", "+1 415 555 0100")])
def test_an_indian_mobile_fits_a_ten_digit_phone_box(value, typed):
    assert rules.fit_phone(value, TEN_DIGITS) == typed
    assert rules.fit_phone("+91 98765 43210", {"constraints": {"type": "tel"}}) == "+91 98765 43210"  # no limit
