"""Know-how as markdown, outside the code: kind skills (groceries, rides, …), situation skills (sign-in, payment,
location), and per-site notes. A file starts with `key: value` lines between --- fences (lists comma-separated),
then markdown. Only the standard library parses them."""

import re
from pathlib import Path
from urllib.parse import urlparse

FOLDER = Path(__file__).resolve().parents[1] / "skills"
LISTS = {"inputs", "sites", "uses", "summary", "report", "aliases", "banks"}
SITUATIONS = {"sign-in", "payment", "location"}
EARN = re.compile(r"^\s*\d+(?:\.\d+)?\s+per\s+\d+\s*$")  # a card note's base earn: "5 per 150"


def parse(text):
    """(frontmatter, markdown body) of one file."""
    match = re.match(r"---\n(.*?)\n---\n?(.*)", text, re.DOTALL)
    if not match:
        raise ValueError("A skill or site file starts with a --- block.")
    meta = {}
    for line in match[1].splitlines():
        key, sep, value = line.partition(":")
        if not sep or not key.strip():
            raise ValueError(f"Not a key: value line: {line!r}")
        key, value = key.strip(), value.strip()
        meta[key] = [v.strip() for v in value.split(",") if v.strip()] if key in LISTS else value
    return meta, match[2].strip()


def read(path):
    """(frontmatter, body) of one file; a parse error names the file."""
    try:
        return parse(path.read_text())
    except ValueError as error:
        raise ValueError(f"{path.name}: {error}") from error


def load(folder=FOLDER):
    """{name: skill} for every skill file: its frontmatter, "body", and whether it is a situation skill."""
    found = {}
    for path in sorted(Path(folder).glob("*.md")):
        meta, body = read(path)
        name = meta.get("name") or path.stem
        found[name] = {"inputs": [], "sites": [], "uses": [], "summary": [], "report": [], **meta, "name": name,
                       "body": body, "situation": name in SITUATIONS}
    return found


def load_sites(folder=FOLDER / "sites"):
    """{host: site note}; a host may carry a path ("google.com/travel/flights")."""
    found = {}
    for path in sorted(Path(folder).glob("*.md")):
        meta, body = read(path)
        if not meta.get("host"):
            raise ValueError(f"{path.name}: a site note needs a host: line.")
        found[meta["host"]] = {"aliases": [], **meta, "body": body}
    return found


def load_cards(folder=FOLDER / "cards"):
    """{name: card note} for every card note: its name, aliases, bank, base earn ("5 per 150"), rupees a point, and
    where those come from. Words the user can edit; the money math reads them (money.value)."""
    return {meta["name"]: {"aliases": [], **meta} for meta, _ in map(read, sorted(Path(folder).glob("*.md")))}


CARD_FILLER = {"credit", "debit", "card", "cards", "my", "the", "a", "an"}
BANKS = (("HDFC", r"hdfc"), ("ICICI", r"icici"), ("Axis", r"axis"), ("SBI", r"sbi"),
         ("Amex", r"amex|american express"), ("IndusInd", r"indusind"), ("Yes Bank", r"yes bank"),
         ("Kotak", r"kotak"), ("IDFC FIRST", r"idfc"), ("AU", r"\bau\b"), ("RBL", r"rbl"), ("HSBC", r"hsbc"))


def card_words(text):
    """The words naming a card, in any order (live: "Regalia Gold (HDFC)" is HDFC's Regalia Gold)."""
    return " ".join(sorted(w for w in re.findall(r"[a-z0-9]+", str(text or "").lower()) if w not in CARD_FILLER))


def card_for(said, cards):
    """The card note the user's words name (its name, an alias, or its bank and name; "credit card" and the like
    don't count), else None."""
    words = card_words(said)
    for card in cards.values():
        names = [card["name"], *card["aliases"], f"{card['bank']} {card['name']}"]
        if words and any(words == card_words(name) for name in names):
            return card
    return None


def bank_of(said):
    """The bank a card's name says it is from ("HDFC Swiggy" → HDFC), else ""."""
    text = str(said or "").lower()
    return next((bank for bank, words in BANKS if re.search(rf"\b(?:{words})\b", text)), "")


def portals(sites):
    """{name: note} for every voucher store: a site note that names the banks whose cards it rewards (`banks:`)."""
    return {note["name"]: note for note in sites.values() if note.get("banks")}


def fits(portal, card):
    """Whether this store rewards this card: it takes any card, or names the card's bank."""
    banks = [b.lower() for b in portal.get("banks", [])]
    bank = str((card or {}).get("bank") or "").lower()
    return "any" in banks or (bool(bank) and bank in banks)


def learned(sites, lines):
    """The site notes with each site's saved learned lines under "## Learned": hints from earlier runs. Lines saved for
    a host join the shipped note of that host or of its parent (the longest key whose host, without its path, the saved
    host is or sits under), else the one note named as the host is (`by_name`: a site that moved), so a shipped note's
    start and search addresses stay; a host no note covers gets a learned-only note of its own. The shipped notes are
    never changed."""
    gathered = {}
    for host, found in lines.items():
        covering = [key for key in sites if host == key.partition("/")[0] or host.endswith("." + key.partition("/")[0])]
        key = max(covering, key=lambda k: (len(k.partition("/")[0]), len(k)), default=None)
        gathered.setdefault(key or by_name(host, sites) or host, []).extend(found)
    out = dict(sites)
    for key, found in gathered.items():
        note = dict(out.get(key) or {"host": key, "name": key, "aliases": [], "body": "", "start": f"https://{key}/"})
        note["body"] = (note["body"].rstrip() + "\n\n## Learned\n" + "\n".join(f"- {line}" for line in found)).strip()
        out[key] = note
    return out


def kinds(skills):
    """The options Jev picks a task's skill from: {name: description}, kind skills only."""
    return {name: s["description"] for name, s in skills.items() if not s["situation"]}


def bare(host):
    for prefix in ("www.", "m."):
        host = host.removeprefix(prefix)
    return host


SECOND_LEVEL = {"co", "com", "net", "org", "gov", "ac", "edu"}  # "zepto.co.in": the name sits before "co.in"


def registrable(host):
    """A host's own name, its subdomains and public suffix dropped: "www.zepto.com" → "zepto"."""
    parts = bare((host or "").lower()).split(".")
    if len(parts) > 1:
        parts = parts[:-1]
    if len(parts) > 1 and parts[-1] in SECOND_LEVEL:
        parts = parts[:-1]
    return parts[-1]


def by_name(host, sites):
    """The key of the one note (with no path) named as this host is: a site that moved to another address keeps its
    note ("zeptonow.com" for www.zepto.com). None when no note fits, or more than one does."""
    name = registrable(host)
    keys = [key for key, site in sites.items()
            if "/" not in key and name and re.sub(r"[^a-z0-9]", "", str(site.get("name", "")).lower()) == name]
    return keys[0] if len(keys) == 1 else None


def site_for(url, sites):
    """The note for this address: the longest host (and path) that matches; else the one note named as the host is
    (a site that moved to another address); or None."""
    parts = urlparse(url)
    here, path, best = bare(parts.netloc.lower()), parts.path, None
    for key, site in sites.items():
        host, _, prefix = key.partition("/")
        if (here == host or here.endswith("." + host)) and path.startswith("/" + prefix if prefix else ""):
            if best is None or len(key) > len(best[0]):
                best = (key, site)
    if best:
        return best[1]
    key = by_name(parts.hostname or "", sites)
    return sites[key] if key else None


def named_at(goal, site):
    """Where the goal first names this site, by name, alias, or host (a note with a path is named only by its name);
    None when it doesn't."""
    names = [site["name"], *site["aliases"], *([site["host"]] if "/" not in site["host"] else [])]
    found = [m.start() for name in names
             for m in [re.search(r"(?<!\w)" + re.escape(name.lower()) + r"(?!\w)", goal.lower())] if m]
    return min(found, default=None)


def named(goal, sites):
    """The site note the goal names by name, alias, or host (a note with a path is named only by its name)."""
    return next((site for site in sites.values() if named_at(goal, site) is not None), None)


def named_all(goal, sites):
    """Every site note the goal names, in the order the goal names them: a comparison names two."""
    found = [(at, n, site) for n, site in enumerate(sites.values()) if (at := named_at(goal, site)) is not None]
    return [site for _, _, site in sorted(found, key=lambda x: x[:2])]


def start(site, skill_name):
    """Where a task starts on this site: its section for the skill, else its start page."""
    return site.get(f"start.{skill_name}") or site["start"]


def render(skill, situations, site):
    """What the planner reads, stable parts first: the kind skill, the situation skills in play, the site note."""
    parts = []
    if skill:
        parts.append(f"# Skill: {skill['name']}\nAsk up front for: {', '.join(skill['inputs']) or 'nothing'}\n"
                     f"Approval names: {', '.join(skill['summary']) or 'what, where, option, payment, total'}\n"
                     f"Final report names: {', '.join(skill['report']) or 'the outcome'}\n\n{skill['body']}")
    parts += [f"# Situation: {s['name']}\n{s['body']}" for s in situations]
    if site:
        search = f"\nSearch address: {site['search']} ({{query}} is the search words)" if site.get("search") else ""
        parts.append(f"# Site: {site['name']} ({site['host']}){search}\n{site['body']}")
    return "\n\n".join(parts)


def template_host(url, site, here=""):
    """Whether an address shaped like the site's search template stays on that site's host, or on `here`, the host the
    page is on (a site that moved serves its search there)."""
    if not site or not site.get("search"):
        return True
    template, given = urlparse(site["search"]), urlparse(url)
    if given.path.rstrip("/") != template.path.rstrip("/"):
        return True  # not built from this template
    host = bare(given.netloc.lower())
    return host == bare(template.netloc.lower()) or (bool(here) and host == bare(here.lower()))
