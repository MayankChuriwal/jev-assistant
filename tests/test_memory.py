"""The Postgres store: its schema, facts, encrypted IDs, standing approvals, notes, habits, undo, purge, and the
profile import. Each test gets its own schema in the session's throwaway database (tests/conftest.py)."""

import json
import threading
import uuid

import psycopg
import pytest
from conftest import TEST_KEY

from assistant import memory as memories
from assistant.memory import Memory, StoreError, Unstorable

OTHER_KEY = "ICEiIyQlJicoKSorLC0uLzAxMjM0NTY3ODk6Ozw9Pj8="
HABIT = {"host": "swiggy.com", "skill": "groceries", "title": "Order one Diet Coke on Instamart",
         "goal": "Order one Diet Coke on Instamart", "start": "https://www.swiggy.com/instamart",
         "plan": [{"do": "add Diet Coke", "done_when": "in the cart", "kind": "act", "values": []}],
         "steps": [[{"op": "CLICK", "label": "add diet coke 300 ml"}]],
         "cart": [{"item": "diet coke 300 ml", "qty": 1}]}


def test_open_makes_the_schema_once_and_opening_again_is_harmless(memory, database_url):
    tables = memory.run(lambda cur: [r["table_name"] for r in cur.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = %s ORDER BY table_name",
        (memory.schema,)).fetchall()])
    assert tables == ["approvals", "events", "facts", "habits", "ids", "notes", "schema_version"]
    again = Memory.open(database_url, schema=memory.schema, key=TEST_KEY)
    try:
        assert again.run(lambda cur: cur.execute("SELECT version FROM schema_version").fetchall()) == [{"version": 1}]
    finally:
        again.close()


def test_facts_are_redacted_kept_once_and_superseded(memory):
    memory.set_id("phone", "phone number", "+91 98765 43210")
    fact = memory.remember("fact", "Call 98765 43210 at the gate", label="Gate", source="my number is 9876543210")
    assert memory.facts()[0]["text"] == "Call [profile:phone] at the gate"
    assert memory.remember("fact", "Call 98765 43210 at the gate", label="Gate") == fact  # the same fact once
    source = memory.run(lambda cur: cur.execute("SELECT source FROM facts WHERE id = %s", (fact,)).fetchone())
    assert source == {"source": "my number is [profile:phone]"}
    memory.remember("default_app", "Swiggy", label="groceries", tags=["groceries"], supersede=True)
    memory.remember("default_app", "Zepto", label="groceries", tags=["groceries"], supersede=True)
    assert [f["text"] for f in memory.facts("default_app")] == ["Zepto"] and memory.default_app("groceries") == "Zepto"


def test_what_is_never_kept_is_refused_and_a_bad_source_is_dropped(memory):
    with pytest.raises(Unstorable):
        memory.remember("fact", "My card is 4111 1111 1111 1111")
    with pytest.raises(Unstorable):
        memory.set_id("otp", "OTP", "482913")
    fact = memory.remember("preference", "Vegetarian food only", tags=["general"], source="my CVV is 123")
    assert memory.run(lambda cur: cur.execute("SELECT source FROM facts WHERE id = %s", (fact,)).fetchone()) == \
        {"source": ""}


def test_ids_are_encrypted_and_never_in_clear(memory):
    memory.set_id("phone", "phone number", "+91 98765 43210")
    memory.set_id("bescom_account", "BESCOM account number", "1234567890")
    assert memory.id_value("bescom_account") == "1234567890" and memory.ids() == [
        {"key": "bescom_account", "label": "BESCOM account number"}, {"key": "phone", "label": "phone number"}]
    assert ("9876543210", "[profile:phone]") in memory.hidden_values()
    dump = memory.run(lambda cur: json.dumps([r["row"] for r in cur.execute(
        "SELECT to_jsonb(t) AS row FROM ids t UNION ALL SELECT to_jsonb(e) FROM events e").fetchall()]))
    assert "+91 98765 43210" not in dump and "9876543210" not in dump and "1234567890" not in dump
    assert "bescom_account" in dump
    with pytest.raises(KeyError):
        memory.id_value("email")


def test_ids_under_another_key_are_missing_and_can_be_set_again(memory, database_url):
    memory.set_id("phone", "phone number", "+91 98765 43210")
    other = Memory.open(database_url, schema=memory.schema, key=OTHER_KEY)
    try:
        with pytest.raises(KeyError):
            other.id_value("phone")
        assert other.ids() == [] and other.unreadable == ["phone"] and other.hidden_values() == []
        other.set_id("phone", "phone number", "+91 90000 00001")  # asked again, kept under the new key
        assert other.id_value("phone") == "+91 90000 00001"
        with pytest.raises(KeyError):
            memory.id_value("phone")  # the old key can no longer open it
    finally:
        other.close()


def test_a_dropped_connection_is_opened_again_once(memory, database_url, monkeypatch):
    memory.remember("preference", "Window seats", tags=["travel"])
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute("SELECT pg_terminate_backend(%s)", (memory.conn.info.backend_pid,))
    assert [f["text"] for f in memory.facts()] == ["Window seats"]  # a new connection, once
    memory.close()

    def down(*_args, **_kwargs):
        raise psycopg.OperationalError("connection refused")

    with monkeypatch.context() as patch:  # undone before the fixture's teardown reconnects to drop the schema
        patch.setattr(memories.psycopg, "connect", down)
        with pytest.raises(StoreError, match="brew services start postgresql@18"):
            memory.facts()


def test_two_stores_on_one_schema_keep_one_live_default_and_one_id(memory, database_url):
    other = Memory.open(database_url, schema=memory.schema, key=TEST_KEY)

    errors = []

    def save(store, app):
        try:
            store.remember("default_app", app, label="groceries", tags=["groceries"], supersede=True)
            store.set_id("phone", "phone number", "+91 98765 43210")
        except Exception as error:  # a thread's failure must fail the test, not vanish with the thread
            errors.append(error)

    try:
        threads = [threading.Thread(target=save, args=pair) for pair in ((memory, "Zepto"), (other, "Blinkit"))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert errors == []
        assert len(memory.facts("default_app")) == 1 and len(memory.ids()) == 1
    finally:
        other.close()


def test_recall_builds_the_planner_block_within_its_caps(memory):
    memory.set_id("phone", "phone number", "+91 98765 43210")
    memory.remember("address", "12, 4th Cross, Koramangala", label="Home", tags=["address"])
    memory.remember("default_app", "Zepto", label="groceries", tags=["groceries"], supersede=True)
    memory.remember("preference", "Window seats on flights", tags=["travel"])
    memory.remember("fact", "Milk orders go to the office", tags=[])
    memory.remember("preference", "Amul Taaza 500 ml for milk", tags=["groceries"])
    memory.remember("preference", "I'm vegetarian", tags=["general"])
    memory.approve("swiggy.com", [{"item": "diet coke 300 ml", "qty": 1}], 100, "Don't ask next time (up to ₹100)")
    block = memory.recall("groceries", "swiggy.com", "Order milk to the office")
    assert block["ids"] == [{"key": "phone", "label": "phone number"}]
    assert block["addresses"] == [{"label": "Home", "text": "12, 4th Cross, Koramangala"}]
    assert block["defaults"] == ["groceries: Zepto"]
    assert block["approvals"][0].startswith("swiggy.com · diet coke 300 ml × 1 · up to ₹100 · until ")
    assert block["facts"] == ["I'm vegetarian", "Amul Taaza 500 ml for milk", "Milk orders go to the office"]
    for n in range(20):
        memory.remember("preference", f"Brand {n} " + "x" * 150, tags=["general"])
    capped = memory.recall("groceries", "swiggy.com", "Order milk")["facts"]
    assert len(capped) <= 12 and sum(map(len, capped)) <= 1500


def test_forget_undo_and_a_day_later_gone(memory):
    fact = memory.remember("address", "12 MG Road", label="Office", tags=["address"])
    memory.set_id("email", "email address", "me@example.com")
    assert memory.forget(str(fact)) == "Office: 12 MG Road" and memory.facts() == []
    assert memory.undo().startswith("forget") and [f["text"] for f in memory.facts()] == ["12 MG Road"]
    assert memory.forget("id:email") == "your email address"
    with pytest.raises(KeyError):
        memory.id_value("email")
    memory.undo()
    assert memory.id_value("email") == "me@example.com"
    with pytest.raises(KeyError):
        memory.forget("note:999")
    memory.forget(str(fact))
    memory.run(lambda cur: cur.execute("UPDATE events SET at = now() - interval '25 hours'"))
    memory.purge()
    kept = memory.run(lambda cur: cur.execute(
        "SELECT count(*) AS n FROM events WHERE ref = %s AND (before IS NOT NULL OR after IS NOT NULL)",
        (f"facts:{fact}",)).fetchone())
    assert kept == {"n": 0}
    with pytest.raises(KeyError, match="more than a day ago"):
        memory.undo()


def test_standing_approvals_live_expire_and_revoke(memory):
    rule = memory.approve("swiggy.com", [{"item": "diet coke 300 ml", "qty": 1}], 100,
                          "Don't ask next time (up to ₹100)")
    assert [r["id"] for r in memory.rules_for("instamart.swiggy.com")] == [rule] and memory.rules_for("zepto.com") == []
    memory.used(rule)
    assert memory.rules_for("swiggy.com")[0]["uses"] == 1 and memory.rules_for("swiggy.com")[0]["cap"] == 100.0
    memory.forget(f"rule:{rule}")
    assert memory.rules_for("swiggy.com") == []
    memory.undo()
    assert memory.rules_for("swiggy.com")[0]["id"] == rule
    memory.run(lambda cur: cur.execute("UPDATE approvals SET expires_at = now() - interval '1 minute'"))
    assert memory.rules_for("swiggy.com") == []
    with pytest.raises(ValueError):
        memory.approve("swiggy.com", [], 100, "no cart")


def test_notes_wait_for_a_decision_and_a_site_keeps_its_thirty_newest(memory):
    drafted = memory.draft_notes("zeptonow.com", ["Search appears after the location pop-up closes"], "Order milk")
    assert memory.notes("zeptonow.com") == []
    memory.decide_notes(drafted, save=True)
    assert memory.notes("zeptonow.com") == ["Search appears after the location pop-up closes"]
    memory.decide_notes(memory.draft_notes("zeptonow.com", ["A line you skip"], "Order milk"), save=False)
    for n in range(31):
        memory.decide_notes(memory.draft_notes("blinkit.com", [f"Line {n}"]), save=True)
    kept = memory.notes("blinkit.com")
    assert len(kept) == 30 and kept[0] == "Line 1" and kept[-1] == "Line 30"
    assert memory.learned() == {"blinkit.com": kept,
                                "zeptonow.com": ["Search appears after the location pop-up closes"]}


def test_habits_keep_versions_count_replays_and_retire(memory):
    first = memory.record_habit(HABIT)
    again = memory.record_habit({**HABIT, "goal": "  order ONE diet coke on   Instamart ", "cart": []})
    other = memory.record_habit({**HABIT, "goal": "Order one Pepsi on Instamart", "title": "Order one Pepsi"})
    assert again == first and other != first
    habit = memory.habit(first)
    assert habit["version"] == 2 and habit["cart"] == HABIT["cart"]  # an empty cart never replaces a known one
    assert [h["id"] for h in memory.habits("groceries", ["swiggy.com"])] == [other, first]
    assert memory.last_cart("swiggy.com") == HABIT["cart"]
    memory.replayed(first, clean=True, verified=True)
    for _ in range(3):
        memory.replayed(first, clean=False, verified=False)
    assert memory.habit(first)["retired_at"] is not None and memory.habit(first)["clean_replays"] == 1
    assert [h["id"] for h in memory.habits("groceries")] == [other]


def test_listing_names_everything_but_never_an_id_value(memory):
    memory.set_id("phone", "phone number", "+91 98765 43210")
    memory.remember("preference", "I'm vegetarian", tags=["general"])
    rule = memory.approve("swiggy.com", [{"item": "diet coke 300 ml", "qty": 1}], 100, "words")
    memory.decide_notes(memory.draft_notes("zeptonow.com", ["A note"]), save=True)
    habit = memory.record_habit(HABIT)
    listing = memory.listing()
    assert listing["ids"] == [{"ref": "id:phone", "label": "phone number"}]
    assert listing["facts"][0]["text"] == "I'm vegetarian" and listing["rules"][0]["ref"] == f"rule:{rule}"
    assert listing["notes"] == {"zeptonow.com": 1} and listing["habits"][0]["ref"] == f"habit:{habit}"
    assert "98765" not in json.dumps(listing, default=str)


def test_listing_names_each_saved_note_by_its_ref_and_never_a_draft(memory):
    kept = memory.draft_notes("zeptonow.com", ["Search appears after the pop-up closes"])
    memory.decide_notes(kept, save=True)
    memory.decide_notes(memory.draft_notes("zeptonow.com", ["A skipped line"]), save=False)
    memory.draft_notes("blinkit.com", ["A draft line"])
    listing = memory.listing()
    assert listing["notes_list"] == [{"ref": f"note:{kept[0]}", "host": "zeptonow.com",
                                      "text": "Search appears after the pop-up closes"}]
    assert listing["notes"] == {"zeptonow.com": 1}
    memory.forget(f"note:{kept[0]}")
    assert memory.listing()["notes_list"] == []


def test_profile_json_is_imported_once(memory, tmp_path):
    path = tmp_path / "profile.json"
    path.write_text(json.dumps({
        "name": "Mayank Churiwal", "phone": "+91 98765 43210", "email": "", "upi_id": "m@okhdfc",
        "addresses": [{"label": "Home", "text": "12 MG Road", "lat": 12.97, "lng": 77.6}],
        "preferences": [{"text": "Default app for groceries: Zepto", "from_task": "Order milk"},
                        {"text": "Window seats", "from_task": "Book a flight"}], "cards": [], "logins": []}))
    assert memory.import_profile(path) is True
    assert not path.exists() and path.with_name("profile.json.imported").exists()
    assert memory.id_value("name") == "Mayank Churiwal" and memory.id_value("upi_id") == "m@okhdfc"
    assert memory.default_app("groceries") == "Zepto" and memory.geolocation() == {"latitude": 12.97, "longitude": 77.6}
    assert [f["text"] for f in memory.facts("preference")] == ["Window seats"]
    assert memory.import_profile(path) is False


def test_open_explains_an_unreachable_postgres():
    with pytest.raises(StoreError, match="brew services start postgresql@18"):
        Memory.open("postgresql://nobody@127.0.0.1:1/jev", schema="jev", key=TEST_KEY)


def test_without_a_key_ids_are_refused_but_facts_work(database_url):
    store = Memory.open(database_url, schema=f"t_{uuid.uuid4().hex[:12]}", key="")
    try:
        with pytest.raises(StoreError, match="JEV_MEMORY_KEY"):
            store.set_id("phone", "phone number", "+91 98765 43210")
        store.remember("preference", "Window seats", tags=["travel"])
        assert store.has_ids() is False and store.ids() == []
    finally:
        store.drop()


def test_a_malformed_key_is_a_clear_error(database_url):
    with pytest.raises(StoreError, match="JEV_MEMORY_KEY"):
        Memory.open(database_url, schema="t_badkey", key="not-a-key")


def age_events(store, hours=25):
    store.run(lambda cur: cur.execute("UPDATE events SET at = now() - make_interval(hours => %s)", (hours,)))


def test_an_undone_forget_never_lets_a_later_purge_turn_undo_into_a_delete(memory, database_url):
    memory.set_id("phone", "phone number", "+91 98765 43210")
    memory.forget("id:phone")
    memory.undo()
    age_events(memory)
    memory.set_id("phone", "phone number", "+91 90000 00001")
    again = Memory.open(database_url, schema=memory.schema, key=TEST_KEY)  # opening purges
    try:
        assert again.undo().startswith("set_id")
        assert again.id_value("phone") == "+91 98765 43210"  # the old value is back, the ID is not deleted
    finally:
        again.close()


def test_an_undone_forget_never_lets_a_later_purge_turn_undo_of_a_retire_into_a_delete(memory):
    habit = memory.record_habit(HABIT)
    memory.forget(f"habit:{habit}")
    memory.undo()
    habit = memory.habits()[0]["id"]
    age_events(memory)
    for _ in range(3):
        memory.replayed(habit, clean=False, verified=False)
    assert memory.habit(habit)["retired_at"] is not None
    memory.purge()
    memory.undo()
    assert memory.habit(habit) is not None and memory.habit(habit)["retired_at"] is None


def test_undo_refuses_an_event_whose_copies_were_purged(memory):
    memory.set_id("phone", "phone number", "+91 98765 43210")
    memory.forget("id:phone")
    age_events(memory)
    memory.purge()
    memory.set_id("phone", "phone number", "+91 90000 00001")
    memory.run(lambda cur: cur.execute("UPDATE events SET purged = true WHERE action = 'set_id' AND before IS NULL"))
    with pytest.raises(KeyError, match="more than a day ago"):
        memory.undo()  # the newest change has no copy left: refused, never turned into a delete
    assert memory.id_value("phone") == "+91 90000 00001"


def test_undoing_the_forget_of_a_superseding_fact_leaves_one_live_default(memory):
    memory.remember("default_app", "Swiggy", label="food", tags=["food"], supersede=True)
    zomato = memory.remember("default_app", "Zomato", label="food", tags=["food"], supersede=True)
    memory.forget(str(zomato))
    assert [f["text"] for f in memory.facts("default_app")] == ["Swiggy"]  # forgetting it brings the old one back
    memory.undo()
    assert [f["text"] for f in memory.facts("default_app")] == ["Zomato"]
    assert memory.recall("food", "", "")["defaults"] == ["food: Zomato"]


def test_undoing_a_forget_does_not_supersede_unrelated_facts_with_the_same_label(memory):
    memory.remember("preference", "Window seats", tags=["travel"])
    aisle = memory.remember("preference", "Aisle seats", tags=["travel"])
    memory.forget(str(aisle))
    memory.undo()
    assert sorted(f["text"] for f in memory.facts("preference")) == ["Aisle seats", "Window seats"]


def test_a_short_id_cannot_hide_a_card_number_from_the_check(memory):
    memory.set_id("code", "code", "4111")
    assert memory.hide("card 4111 1111 1111 1111") == "card [profile:code] 1111 1111 1111"  # the digits are split
    with pytest.raises(Unstorable):
        memory.remember("fact", "card 4111 1111 1111 1111")
    assert memory.facts() == []


def test_ids_are_stamped_with_the_clock_so_a_change_in_a_running_transaction_is_noticed(memory):
    default = memory.run(lambda cur: cur.execute(
        "SELECT column_default FROM information_schema.columns WHERE table_schema = %s AND table_name = 'ids' "
        "AND column_name = 'updated_at'", (memory.schema,)).fetchone())
    assert "clock_timestamp" in default["column_default"]


def test_import_skips_what_memory_refuses_and_still_finishes(memory, tmp_path):
    path = tmp_path / "profile.json"
    path.write_text(json.dumps({
        "phone": "919876543210", "upi_id": "123456",
        "addresses": [{"label": "Card", "text": "Deliver to 4111 1111 1111 1111"},
                      {"label": "Home", "text": "12 MG Road"}],
        "preferences": [{"text": "My card is 4111 1111 1111 1111"}, {"text": "Window seats"}]}))
    assert memory.import_profile(path) is True
    assert not path.exists() and path.with_name("profile.json.imported").exists()
    assert [f["text"] for f in memory.facts()] == ["Window seats", "12 MG Road"]
    assert memory.id_value("phone") == "919876543210"


def test_only_a_missing_database_is_created_and_connecting_has_a_timeout(database_url, monkeypatch):
    seen, made = [], []

    def refuse(*_args, **kwargs):
        seen.append(kwargs.get("connect_timeout"))
        raise psycopg.OperationalError('FATAL:  role "nobody" does not exist')

    monkeypatch.setattr(memories.psycopg, "connect", refuse)
    monkeypatch.setattr(Memory, "create_database", lambda self: made.append(1))
    with pytest.raises(StoreError, match="isn't reachable"):
        Memory.open(database_url, schema="t_norole", key=TEST_KEY)
    assert made == [] and seen == [10]


def test_the_task_words_and_notes_and_habits_never_hold_a_plain_id(memory):
    memory.set_id("phone", "phone number", "9876543210")
    memory.remember("preference", "Likes it cold", task="recharge 9876543210")
    memory.draft_notes("zeptonow.com", ["Use 9876543210 to sign in"], task="recharge 9876543210")
    memory.record_habit({**HABIT, "goal": "Recharge 9876543210", "title": "Recharge 9876543210"})
    dump = memory.run(lambda cur: json.dumps([r["row"] for r in cur.execute(
        "SELECT to_jsonb(t) AS row FROM facts t UNION ALL SELECT to_jsonb(n) FROM notes n "
        "UNION ALL SELECT to_jsonb(h) FROM habits h UNION ALL SELECT to_jsonb(e) FROM events e "
        "WHERE e.ref NOT LIKE 'ids:%'").fetchall()]))
    assert "9876543210" not in dump and "recharge [profile:phone]" in dump


def test_a_standing_approval_is_counted_only_while_it_is_live(memory):
    rule = memory.approve("swiggy.com", [{"item": "diet coke 300 ml", "qty": 1}], 100, "Don't ask next time")
    assert memory.used(rule) is True and memory.live_rules()[0]["uses"] == 1
    memory.run(lambda cur: cur.execute("UPDATE approvals SET expires_at = now() - interval '1 minute'"))
    assert memory.used(rule) is False
    assert memory.run(lambda cur: cur.execute("SELECT uses FROM approvals").fetchone())["uses"] == 1
    other = memory.approve("swiggy.com", [{"item": "atta 1 kg", "qty": 1}], 100, "Don't ask next time")
    memory.forget(f"rule:{other}")
    assert memory.used(other) is False and memory.used(10**6) is False


def test_a_memory_on_the_default_schema_refuses_to_drop_it():
    never = Memory("postgresql://never-the-real-memory@127.0.0.1:1/none", memories.SCHEMA, None)  # never connects
    with pytest.raises(ValueError, match="Refusing"):
        never.drop()
