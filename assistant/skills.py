"""Know-how as markdown, outside the code: kind skills (groceries, rides, …), situation skills (sign-in, payment,
location), and per-site notes. A file starts with `key: value` lines between --- fences (lists comma-separated),
then markdown. Only the standard library parses them."""

import re
from pathlib import Path
from urllib.parse import urlparse

FOLDER = Path(__file__).resolve().parents[1] / "skills"
LISTS = {"inputs", "sites", "uses", "summary", "report", "aliases"}
SITUATIONS = {"sign-in", "payment", "location"}


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


def kinds(skills):
    """The options Jev picks a task's skill from: {name: description}, kind skills only."""
    return {name: s["description"] for name, s in skills.items() if not s["situation"]}


def bare(host):
    for prefix in ("www.", "m."):
        host = host.removeprefix(prefix)
    return host


def site_for(url, sites):
    """The note for this address: the longest host (and path) that matches, or None."""
    parts = urlparse(url)
    here, path, best = bare(parts.netloc.lower()), parts.path, None
    for key, site in sites.items():
        host, _, prefix = key.partition("/")
        if (here == host or here.endswith("." + host)) and path.startswith("/" + prefix if prefix else ""):
            if best is None or len(key) > len(best[0]):
                best = (key, site)
    return best[1] if best else None


def named(goal, sites):
    """The site note the goal names by name, alias, or host (a note with a path is named only by its name)."""
    text = goal.lower()
    for site in sites.values():
        names = [site["name"], *site["aliases"], *([site["host"]] if "/" not in site["host"] else [])]
        if any(re.search(r"(?<!\w)" + re.escape(name.lower()) + r"(?!\w)", text) for name in names):
            return site
    return None


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


def template_host(url, site):
    """Whether an address shaped like the site's search template stays on that site's host."""
    if not site or not site.get("search"):
        return True
    template, given = urlparse(site["search"]), urlparse(url)
    if given.path.rstrip("/") != template.path.rstrip("/"):
        return True  # not built from this template
    return bare(given.netloc.lower()) == bare(template.netloc.lower())
