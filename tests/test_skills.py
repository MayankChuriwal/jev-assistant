"""Skill and site-note files: the format, the shipped set, and how a task finds its site."""

import pytest

from assistant import skills

KINDS = {"groceries", "food-delivery", "shopping", "rides", "tickets", "travel", "bills-recharges", "account",
         "forms", "lookup"}


def test_frontmatter_lists_and_body():
    meta, body = skills.parse("---\nname: rides\ninputs: pickup, drop, when?\nstart: https://m.uber.com/go\n---\n"
                              "## Plan\n1. Go.")
    assert meta == {"name": "rides", "inputs": ["pickup", "drop", "when?"], "start": "https://m.uber.com/go"}
    assert body == "## Plan\n1. Go."
    with pytest.raises(ValueError):
        skills.parse("no frontmatter")


def test_the_shipped_skills_load_with_every_kind_and_situation():
    loaded = skills.load()
    assert set(skills.kinds(loaded)) == KINDS
    assert {n for n, s in loaded.items() if s["situation"]} == {"sign-in", "payment", "location"}
    for skill in loaded.values():
        assert skill["description"] and skill["body"], skill["name"]
        assert set(skill["uses"]) <= skills.SITUATIONS, skill["name"]


def test_every_site_a_skill_names_has_a_note_with_a_start():
    loaded, sites = skills.load(), skills.load_sites()
    for skill in loaded.values():
        for host in skill["sites"]:
            assert host in sites and sites[host]["start"].startswith("https://"), host


def test_a_url_finds_the_longest_matching_note():
    sites = skills.load_sites()
    assert skills.site_for("https://www.swiggy.com/instamart/item/1", sites)["name"] == "Swiggy"
    assert skills.site_for("https://m.uber.com/go/home", sites)["name"] == "Uber"
    assert skills.site_for("https://in.bookmyshow.com/explore", sites)["name"] == "BookMyShow"
    assert skills.site_for("https://www.google.com/travel/flights/search", sites)["name"] == "Google Flights"
    assert skills.site_for("https://www.google.com/search?q=x", sites) is None


def test_a_site_that_moved_to_another_address_finds_its_note_by_name():
    sites = skills.load_sites()
    assert sites["zeptonow.com"]["name"] == "Zepto"  # the note's host is where the site used to live
    for url in ("https://www.zepto.com/search?query=milk", "https://zepto.com/", "https://m.zepto.co.in/cart"):
        assert skills.site_for(url, sites)["host"] == "zeptonow.com", url
    rendered = skills.render(None, [], skills.site_for("https://www.zepto.com/", sites))
    assert "Search address: https://www.zeptonow.com/search?query={query}" in rendered
    assert skills.site_for("https://www.zeptofoods.com/", sites) is None
    assert skills.site_for("https://www.google.com/search?q=x", sites) is None  # "Google Flights" has a path
    twins = {"a.test": {"host": "a.test", "name": "Shop", "aliases": []},
             "b.test": {"host": "b.test", "name": "Shop", "aliases": []}}
    assert skills.site_for("https://www.shop.in/", twins) is None  # two notes fit: neither is guessed


def test_lines_learned_on_a_sites_new_address_join_its_shipped_note():
    sites = skills.load_sites()
    out = skills.learned(sites, {"zepto.com": ["The cart opens from the top right."]})
    assert "zepto.com" not in out and out["zeptonow.com"]["search"] == sites["zeptonow.com"]["search"]
    assert out["zeptonow.com"]["body"].endswith("## Learned\n- The cart opens from the top right.")
    assert skills.site_for("https://www.zepto.com/", out) is out["zeptonow.com"]


def test_a_goal_names_its_site_and_the_skill_picks_the_start():
    sites = skills.load_sites()
    site = skills.named("Order one Diet Coke on Swiggy Instamart", sites)
    assert site["host"] == "swiggy.com"
    assert skills.start(site, "groceries") == "https://www.swiggy.com/instamart"
    assert skills.start(site, "food-delivery") == "https://www.swiggy.com"
    assert skills.named("Book a table on EazyDiner", sites) is None
    assert skills.named("Search google.com for Lalbagh hours", sites) is None  # a path note never matches by host


def test_render_orders_skill_situations_and_site():
    loaded, sites = skills.load(), skills.load_sites()
    text = skills.render(loaded["rides"], [loaded["location"]], sites["uber.com"])
    assert text.index("# Skill: rides") < text.index("# Situation: location") < text.index("# Site: Uber")


def test_a_search_address_keeps_its_site():
    site = skills.load_sites()["amazon.in"]
    assert skills.template_host("https://www.amazon.in/s?k=boat+airdopes", site)
    assert not skills.template_host("https://evil.test/s?k=boat", site)
    assert skills.template_host("https://www.flipkart.com/search?q=x", site)  # not built from Amazon's template
    zepto = skills.load_sites()["zeptonow.com"]  # the site moved: its search runs on the host the page is on
    assert skills.template_host("https://www.zepto.com/search?query=milk", zepto, here="www.zepto.com")
    assert not skills.template_host("https://evil.test/search?query=milk", zepto, here="www.zepto.com")


def test_a_broken_skill_or_site_file_is_named_in_the_error(tmp_path):
    (tmp_path / "rides.md").write_text("---\nname: rides\n---\nok")
    (tmp_path / "broken.md").write_text("no frontmatter here")
    with pytest.raises(ValueError, match="broken.md"):
        skills.load(tmp_path)
    sites = tmp_path / "sites"
    sites.mkdir()
    (sites / "nohost.md").write_text("---\nname: Shop\n---\nok")
    with pytest.raises(ValueError, match="nohost.md"):
        skills.load_sites(sites)


def test_comparing_prices_is_a_lookup_and_groceries_are_bought():
    loaded = skills.load()
    assert loaded["groceries"]["description"].startswith("Order or buy groceries")
    assert "comparing prices across grocery or shopping apps" in loaded["lookup"]["description"]


def test_every_site_a_goal_names_is_found_in_the_goals_order():
    sites = skills.load_sites()
    found = skills.named_all("Compare the iPhone 16 on Amazon.in and Flipkart, then Croma", sites)
    assert [s["host"] for s in found] == ["amazon.in", "flipkart.com"]
    assert skills.named_all("Order Diet Coke on Swiggy Instamart", sites) == [skills.named("Diet Coke on Swiggy",
                                                                                          sites)]
    assert skills.named_all("Book a table on EazyDiner", sites) == []
