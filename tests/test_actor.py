"""The page model's contract: its instructions name no job, and its facts schema is the job's own."""

from assistant import actor, money


def test_the_schema_holds_the_jobs_own_facts():
    assert actor.schema("bill")["properties"]["facts"]["required"] == list(money.BILL_FACTS)
    voucher = actor.schema("voucher")["properties"]["facts"]["required"]
    assert {"amounts", "reward", "cart", "payable", "order_id"} <= set(voucher)


def test_the_instructions_name_no_job():
    for words in ("amount due", "bill", "biller"):
        assert words not in actor.INSTRUCTIONS.lower()


def test_a_sign_up_form_ends_the_turn_with_stop_never_a_close_and_retry():
    # live, SBI Card's GyFTR store: the number had no account, so Submit opened a sign-up form (name, e-mail, terms);
    # the page model closed it as a pop-up and pressed ADD again for 40 turns
    rule = actor.INSTRUCTIONS.split("a sign-up form")
    assert len(rule) == 2 and "end with stop" in rule[1][:200]
