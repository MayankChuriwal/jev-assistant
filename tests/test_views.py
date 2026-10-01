"""The page as mini reads it: numbered elements, new ones starred, and one line saying what changed."""

from fakes import fake_page

from assistant import views

A = {"id": "e1", "kind": "click", "label": "Pickup location", "role": "combobox", "node": 1}
B = {"id": "e2", "kind": "fill", "label": "Where to?", "role": "textbox", "value": "", "node": 2}
T1 = {"id": "e3", "kind": "click", "label": "Terminal 1, Kempegowda Airport", "role": "option", "node": 3}


def test_elements_are_numbered_and_new_ones_starred():
    before = fake_page(text="Book a ride", actions=[A, B])
    after = fake_page(text="Book a ride\n3 results", actions=[A, B, T1])
    seen = views.view(after, before)
    assert seen["elements"] == ['[1] combobox "Pickup location"', '[2] textbox "Where to?"',
                                '*[3] option "Terminal 1, Kempegowda Airport"']
    assert seen["changed"] == 'new: option "Terminal 1, Kempegowda Airport"; new text: 3 results'


def test_first_look_and_no_change_are_said_plainly():
    page = fake_page(text="Book a ride", actions=[A])
    assert views.view(page, None)["changed"] == "first look at this page"
    assert views.change(page, page) == "nothing changed"


def test_a_new_address_and_vanished_elements_are_reported():
    before = fake_page(url="https://x.test/a", actions=[A, B])
    after = fake_page(url="https://x.test/b", actions=[A])
    assert views.change(before, after) == 'now at https://x.test/b; gone: textbox "Where to?"'


def test_long_pages_are_trimmed():
    page = fake_page(text="x" * 10000, actions=[{**A, "id": f"e{n}", "node": n, "label": f"Item {n}"}
                                                for n in range(1, 200)])
    seen = views.view(page, None)
    assert len(seen["text"]) == views.TEXT and len(seen["elements"]) == views.ELEMENTS


def test_the_loop_signature_reads_the_elements_and_the_path_not_the_text():
    page = fake_page(text="Search “milk”", url="https://x.test/home?ref=1", actions=[A, B])
    rotated = fake_page(text="Search “bread”\nDelivery in 9 minutes", url="https://x.test/home?ref=2", actions=[A, B])
    assert views.signature(page) == views.signature(rotated)  # text that rotates or ticks is no new page
    opened = fake_page(text="Search “milk”", url="https://x.test/home", actions=[A, B, T1])
    elsewhere = fake_page(text="Search “milk”", url="https://x.test/cart", actions=[A, B])
    typed = fake_page(text="Search “milk”", url="https://x.test/home", actions=[A, {**B, "value": "Indiranagar"}])
    assert len({views.signature(p) for p in (page, opened, elsewhere, typed)}) == 4


def test_the_loop_signature_ignores_counters_and_rotating_placeholders_in_labels():
    def search(words, minutes):
        return fake_page(actions=[{**A, "label": f'Search for "{words}"'},
                                  {**T1, "label": f"Delivery in {minutes} minutes (1 of {minutes})"}])

    assert views.signature(search("Fruits", 8)) == views.signature(search("Atta", 9))
    assert views.signature(search("Fruits", 8)) != views.signature(fake_page(actions=[A, T1]))
