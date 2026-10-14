"""The page model's contract: its instructions name no job, and its facts schema is the job's own."""

from assistant import actor, money


def test_the_schema_holds_the_jobs_own_facts():
    assert actor.schema("bill")["properties"]["facts"]["required"] == list(money.BILL_FACTS)
    voucher = actor.schema("voucher")["properties"]["facts"]["required"]
    assert {"amounts", "reward", "cart", "payable", "order_id"} <= set(voucher)


def test_the_instructions_name_no_job():
    for words in ("amount due", "bill", "biller"):
        assert words not in actor.INSTRUCTIONS.lower()
