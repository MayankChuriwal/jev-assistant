"""Redaction (Secrets) and what is never stored."""

import pytest

from assistant import privacy
from assistant.privacy import Secrets


def test_phone_numbers_are_hidden_in_local_formats():
    s = Secrets(privacy.variants("phone", "+91 98765 43210"))
    for shown in ("(98765) 43210", "098765 43210", "+91-98765-43210", "98765.43210"):
        assert "98765" not in s.text(f"call {shown} now"), shown


def test_digit_secrets_are_hidden_in_any_spacing():
    s = Secrets([("4111 1111 1111 1111", "[secret:card.number]")])
    for shown in ("4111111111111111", "4111-1111-1111-1111", "card 4111 1111 1111 1111 ok"):
        assert "4111" not in s.text(shown) and "[secret:card.number]" in s.text(shown)
    assert s.text("order 94111111111111119") == "order 94111111111111119"  # longer digit runs are other numbers


def test_words_are_hidden_whole_and_case_insensitively():
    s = Secrets([("Mayank", "[profile:name]"), ("m@example.com", "[profile:email]"), ("ab", "[ignored]")])
    assert s.text("Hi mayank, mail m@example.com") == "Hi [profile:name], mail [profile:email]"
    assert s.text("Mayankpur") == "Mayankpur" and "ab" in s.text("abc ab")


def test_redaction_is_deep_and_leaves_images_alone():
    s = Secrets([("482913", "[secret:otp]")])
    body = {"a": ["code 482913", {"b": "482913"}], "n": 482913, "img": "data:image/png;base64,482913AAAA"}
    assert s.redact(body) == {"a": ["code [secret:otp]", {"b": "[secret:otp]"}], "n": 482913,
                              "img": "data:image/png;base64,482913AAAA"}


def test_redaction_reaches_dictionary_keys_and_a_leak_is_seen():
    secrets = Secrets([("9876543210", "[profile:phone]")])
    body = {"Call 98765 43210": ["ok"], "shot": "data:image/png;base64,98765 43210"}
    assert secrets.leaks(body)
    clean = secrets.redact(body)
    assert "Call [profile:phone]" in clean and not secrets.leaks(clean)


def test_a_short_digit_secret_never_matches_inside_a_larger_number():
    s = Secrets([("499", "[secret:answer1]")])
    for text in ("To pay ₹1,499", "₹1499", "₹499.00", "Total 2,499.50", "Pay ₹1,499.00 now", "1.499"):
        assert s.text(text) == text and not s.leaks(text), text
    for text, shown in (("CVV 499", "CVV [secret:answer1]"), ("499", "[secret:answer1]"),
                        ("code: 4 9 9.", "code: [secret:answer1]."), ("(499)", "([secret:answer1])")):
        assert s.text(text) == shown and s.leaks(text), text


def test_a_long_digit_secret_keeps_its_spacing_tolerance_and_its_number_boundaries():
    s = Secrets([("482913", "[secret:otp]")])
    assert s.text("OTP 482 913.") == "OTP [secret:otp]." and s.text("OTP 482-913") == "OTP [secret:otp]"
    assert s.text("Order 1,482913") == "Order 1,482913" and s.text("₹482913.50") == "₹482913.50"


@pytest.mark.parametrize("shown", ["Mob.9876543210", "Mayank,9876543210,Bengaluru", "9876543210,560001",
                                   "Call:9876543210.", "+91 98765 43210", "+91-98765-43210", "(98765) 43210",
                                   "98765.43210", "ph.98765 43210,x"])
def test_a_phone_number_is_redacted_beside_a_dot_or_a_comma(shown):
    s = Secrets([("9876543210", "[profile:phone]")])
    assert s.leaks(shown) and "98765" not in s.text(shown) and "[profile:phone]" in s.text(shown), shown
    assert not s.leaks(s.text(shown))


def test_a_phone_number_is_still_not_part_of_a_longer_digit_run():
    s = Secrets([("9876543210", "[profile:phone]")])
    for text in ("order 19876543210", "98765432101", "ref 98765432100.5"):
        assert not s.leaks(text), text


@pytest.mark.parametrize("shown", ["Card.4111111111111111", "Card,4111111111111111,exp", "4111111111111111.",
                                   "4111 1111 1111 1111,x"])
def test_a_card_number_is_redacted_beside_a_dot_or_a_comma(shown):
    s = Secrets([("4111111111111111", "[secret:card.number]")])
    assert s.leaks(shown) and "4111" not in s.text(shown) and "[secret:card.number]" in s.text(shown), shown


def test_a_cvv_is_not_part_of_a_price_but_is_a_standalone_code():
    s = Secrets([("499", "[secret:answer1]")])
    for text in ("₹1,499", "₹1499", "₹499.00", "₹1 499", "Rs 1 499", "₹1\u202f499"):
        assert s.text(text) == text and not s.leaks(text), text
    for text, shown in (("CVV 499", "CVV [secret:answer1]"), ("code: 499", "code: [secret:answer1]")):
        assert s.text(text) == shown and s.leaks(text), text


@pytest.mark.parametrize("gap", [" ", "\u202f", "\u2009", "\u2007", "\u00a0"])
@pytest.mark.parametrize("prefix", ["To pay ₹1", "Rs 1", "Total ₹12"])
def test_a_cvv_equal_to_the_last_group_of_a_space_grouped_amount_stays_in_the_amount(prefix, gap):
    s = Secrets([("299", "[secret:answer1]")])
    text = f"{prefix}{gap}299"
    assert s.text(text) == text and not s.leaks(text), repr(text)
    assert s.text("CVV 299") == "CVV [secret:answer1]" and s.text("299") == "[secret:answer1]"


def test_a_four_digit_otp_follows_the_short_secret_rule():
    s = Secrets([("4829", "[secret:otp]")])
    for text in ("₹14,829", "₹4829.50", "₹1 4829", "Total 14.829"):
        assert s.text(text) == text and not s.leaks(text), text
    for text, shown in (("OTP 4829", "OTP [secret:otp]"), ("code: 4 8 2 9.", "code: [secret:otp].")):
        assert s.text(text) == shown and s.leaks(text), text


def test_a_short_digit_secret_on_its_own_line_is_redacted_after_a_line_ending_in_a_digit():
    s = Secrets([("299", "[secret:answer1]")])
    for text, shown in (("Order 4521\n299", "Order 4521\n[secret:answer1]"),
                        ("Order 4521\r\n299", "Order 4521\r\n[secret:answer1]"),
                        ("Order ref\n299", "Order ref\n[secret:answer1]"),
                        ("Order 4521\n\n299", "Order 4521\n\n[secret:answer1]")):
        assert s.text(text) == text.replace("299", "[secret:answer1]") == shown and s.leaks(text), repr(text)


@pytest.mark.parametrize("gap", [" ", " ", " ", " ", " "])
def test_a_spaced_amount_stays_whole_for_every_space_like_character(gap):
    s = Secrets([("299", "[secret:answer1]")])
    text = f"To pay ₹1{gap}299\nPay"
    assert s.text(text) == text and not s.leaks(text), repr(text)


def test_card_numbers_aadhaar_pan_and_named_secrets_are_never_storable():
    for text in ("4111 1111 1111 1111", "card 4111-1111-1111-1111 exp", "2345 6789 0124", "ABCPE1234F",
                 "my OTP is 4829", "password hunter2", "CVV 123", "UPI PIN 1234", "my aadhaar is pending",
                 "PAN card copy", "card number on file", "expiry date 12/29"):
        assert not privacy.storable(text), text


def test_everyday_facts_and_ids_are_storable():
    for text in ("Home: 12, 4th Cross, Koramangala 560034", "I'm vegetarian", "Default app for groceries: Zepto",
                 "BESCOM account 1234567890", "+91 98765 43210", "919876543210", "Pay by card on Amazon",
                 "Pincode 560034", "I like pan pizza", "4111111111111112", ""):
        assert privacy.storable(text), text


def test_the_checksums():
    assert privacy.luhn("4111111111111111") and not privacy.luhn("4111111111111112")
    assert privacy.secret_number("card 4111 1111 1111 1111") and privacy.secret_number("ABCPE1234F")
    assert not privacy.secret_number("Enter the OTP sent to [profile:phone]")  # naming a secret holds no number
    assert privacy.verhoeff("2363") and privacy.verhoeff("234567890124") and not privacy.verhoeff("919876543210")


def test_variants_cover_phone_formats_and_name_parts():
    assert privacy.variants("phone", "+91 98765 43210") == [
        ("+91 98765 43210", "[profile:phone]"), ("919876543210", "[profile:phone]"),
        ("9876543210", "[profile:phone]"), ("09876543210", "[profile:phone]")]
    assert privacy.variants("name", "Mayank Churiwal") == [
        ("Mayank Churiwal", "[profile:name]"), ("Mayank", "[profile:name]"), ("Churiwal", "[profile:name]")]
    assert privacy.variants("bescom_account", " 1234567890 ") == [("1234567890", "[profile:bescom_account]")]
    assert privacy.variants("email", "") == [] and privacy.variants("email", None) == []
