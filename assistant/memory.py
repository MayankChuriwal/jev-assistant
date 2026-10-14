"""What the agent keeps between tasks, in Postgres: facts about the user, their IDs (encrypted with JEV_MEMORY_KEY;
Postgres never sees a plain value or the key), standing approvals, learned site notes, recorded habits, and an event
log for undo and audit. One connection per Memory, behind a lock; every public method is one transaction, and a
connection Postgres dropped is opened again once."""

import json
import os
import re
import threading
from pathlib import Path

import psycopg
from cryptography.fernet import Fernet, InvalidToken
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from . import privacy

HOME = Path(os.environ.get("JEV_HOME") or Path.home() / ".jev")
DEFAULT_URL = "postgresql:///jev"
SCHEMA = "jev"
VERSION = 1
KEY = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
SCHEMA_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
FACT_KINDS = ("address", "preference", "default_app", "fact")
LABELS = {"name": "full name", "phone": "phone number", "email": "email address", "upi_id": "UPI ID",
          "experian_ern": "Experian Reference Number (ERN)", "experian_uti": "Experian Unique Transaction ID (UTI)",
          "bank_account_last4": "last 4 digits of your bank account", "icici_user_id": "ICICI net banking User ID",
          "bescom_account": "BESCOM account number", "adani_electricity_account": "Adani Electricity account (CA) number",
          "tata_power_ddl_account": "Tata Power-DDL CA number", "bses_rajdhani_account": "BSES Rajdhani account number",
          "msedcl_account": "MSEDCL consumer number"}
RECALL_FACTS, RECALL_CHARS, CLOSE = 12, 1500, 0.2
NOTES_PER_SITE = 30
RULE_DAYS = 30
UNDO_HOURS = 24
RETIRE_AFTER = 3
CONNECT_TIMEOUT = 10
FIX = "Start it (brew services start postgresql@18) or set JEV_DATABASE_URL."
UNSTORABLE = "I don't keep that: card numbers, CVVs, OTPs, passwords, PINs, Aadhaar and PAN stay out of memory."
GOAL_KEY = r"lower(btrim(regexp_replace(goal, '\s+', ' ', 'g')))"
# a habit's host is one of the given hosts or a subdomain of one: compared as text, never as a LIKE pattern
UNDER = "EXISTS (SELECT 1 FROM unnest(%s::text[]) AS h(host) WHERE habits.host = h.host " \
        "OR right(habits.host, length(h.host) + 1) = '.' || h.host)"
TABLES = {"fact": "facts", "note": "notes", "habit": "habits", "rule": "approvals"}
COLUMNS = {
    "facts": ("kind", "label", "text", "tags", "data", "source", "task", "created_at", "superseded_by"),
    "approvals": ("site", "cart", "cap", "words", "created_at", "expires_at", "uses", "last_used_at", "revoked_at"),
    "notes": ("host", "text", "status", "task", "created_at", "decided_at"),
    "habits": ("host", "skill", "title", "goal", "start", "plan", "steps", "cart", "version", "replays",
               "clean_replays", "failed_in_a_row", "created_at", "updated_at", "last_used_at", "retired_at"),
}
DDL = r"""
CREATE TABLE IF NOT EXISTS facts (
  id bigserial PRIMARY KEY,
  kind text NOT NULL CHECK (kind IN ('address', 'preference', 'default_app', 'fact')),
  label text NOT NULL DEFAULT '',
  text text NOT NULL,
  tags text[] NOT NULL DEFAULT '{}',
  data jsonb NOT NULL DEFAULT '{}',
  source text NOT NULL DEFAULT '',
  task text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  superseded_by bigint REFERENCES facts (id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS facts_text ON facts USING gin (text gin_trgm_ops);
CREATE TABLE IF NOT EXISTS ids (
  key text PRIMARY KEY CHECK (key ~ '^[a-z][a-z0-9_]{1,39}$'),
  label text NOT NULL,
  value bytea NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS approvals (
  id bigserial PRIMARY KEY,
  site text NOT NULL,
  cart jsonb NOT NULL,
  cap numeric(12, 2) NOT NULL CHECK (cap > 0),
  words text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  uses integer NOT NULL DEFAULT 0,
  last_used_at timestamptz,
  revoked_at timestamptz
);
CREATE TABLE IF NOT EXISTS notes (
  id bigserial PRIMARY KEY,
  host text NOT NULL,
  text text NOT NULL,
  status text NOT NULL CHECK (status IN ('draft', 'saved', 'skipped')),
  task text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  decided_at timestamptz
);
CREATE TABLE IF NOT EXISTS habits (
  id bigserial PRIMARY KEY,
  host text NOT NULL,
  skill text NOT NULL,
  title text NOT NULL,
  goal text NOT NULL,
  start text NOT NULL,
  plan jsonb NOT NULL,
  steps jsonb NOT NULL,
  cart jsonb NOT NULL DEFAULT '[]',
  version integer NOT NULL DEFAULT 1,
  replays integer NOT NULL DEFAULT 0,
  clean_replays integer NOT NULL DEFAULT 0,
  failed_in_a_row integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  last_used_at timestamptz,
  retired_at timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS habits_live
  ON habits (host, skill, (lower(btrim(regexp_replace(goal, '\s+', ' ', 'g'))))) WHERE retired_at IS NULL;
CREATE TABLE IF NOT EXISTS events (
  id bigserial PRIMARY KEY,
  at timestamptz NOT NULL DEFAULT now(),
  actor text NOT NULL CHECK (actor IN ('user', 'agent', 'bench', 'import')),
  action text NOT NULL,
  ref text NOT NULL,
  before jsonb,
  after jsonb,
  undone boolean NOT NULL DEFAULT false,
  purged boolean NOT NULL DEFAULT false
);
CREATE TABLE IF NOT EXISTS payments (
  id bigserial PRIMARY KEY,
  biller text NOT NULL,
  account text NOT NULL CHECK (account ~ '^[a-z][a-z0-9_]{1,39}$'),
  amount numeric(12, 2) NOT NULL CHECK (amount > 0),
  method text NOT NULL DEFAULT '',
  reference text NOT NULL DEFAULT '',
  status text NOT NULL CHECK (status IN ('started', 'paid', 'failed', 'unclear')),
  task text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS payments_account ON payments (lower(biller), account, created_at DESC);
CREATE TABLE IF NOT EXISTS schema_version (version integer NOT NULL);
"""


class StoreError(RuntimeError):
    """Postgres can't be reached or set up, or the key is missing or bad: the message says what to do."""


class Unstorable(ValueError):
    """Text that is never kept: a card number, an Aadhaar number, a PAN, or words naming a secret."""


def shown(url):
    """A database address as a message may show it: never its password."""
    return re.sub(r"(://[^:/@]*:)[^@]*@", r"\1***@", url)


def first_line(error):
    text = str(error).strip()
    return text.splitlines()[0] if text else type(error).__name__


def rupees(value):
    value = float(value)
    return f"₹{value:,.0f}" if value == int(value) else f"₹{value:,.2f}"


def rule_line(rule):
    """A standing approval in one line, as the planner and `jev memory` show it."""
    items = ", ".join(f"{i['item']} × {i['qty']}" for i in rule["cart"])
    return f"{rule['site']} · {items} · up to {rupees(rule['cap'])} · until {rule['expires_at']:%d %b}"


class Memory:
    def __init__(self, url, schema, key):
        if not SCHEMA_NAME.match(schema or ""):
            raise ValueError(f"Not a usable schema name: {schema!r}")
        try:
            self.fernet = Fernet(key) if key else None
        except ValueError as error:
            raise StoreError(f"JEV_MEMORY_KEY isn't a valid key ({error}).") from error
        self.url, self.schema = url, schema
        self.lock = threading.RLock()
        self.conn, self.cache, self.stamp, self.unreadable = None, {}, None, []

    @classmethod
    def open(cls, url=None, *, schema=SCHEMA, key=None):
        """Connect (creating the default database when it is missing), make the schema and its tables, and purge old
        forget copies. `key` defaults to JEV_MEMORY_KEY; "" means none, so IDs can't be kept or read."""
        url = url or os.environ.get("JEV_DATABASE_URL") or DEFAULT_URL
        memory = cls(url, schema, os.environ.get("JEV_MEMORY_KEY") if key is None else key)
        memory.migrate()
        memory.purge()
        return memory

    def sibling(self, schema):
        """Another schema on this store's database, under this store's key: a bench pair's, whose key may live in
        memory only."""
        other = Memory.open(self.url, schema=schema, key="")
        other.fernet = self.fernet
        return other

    # ---- the connection

    def connect(self):
        try:
            self.conn = psycopg.connect(self.url, autocommit=True, row_factory=dict_row,
                                        connect_timeout=CONNECT_TIMEOUT)
        except psycopg.OperationalError as error:
            if not ("database" in str(error) and "does not exist" in str(error)):
                raise StoreError(f"Postgres isn't reachable at {shown(self.url)}: {first_line(error)}. {FIX}") \
                    from error
            self.create_database()  # another process may have made it meanwhile: connecting again settles that
            try:
                self.conn = psycopg.connect(self.url, autocommit=True, row_factory=dict_row,
                                            connect_timeout=CONNECT_TIMEOUT)
            except psycopg.OperationalError as again:
                raise StoreError(f"Postgres has no database at {shown(self.url)} and it couldn't be made: "
                                 f"{first_line(again)}. {FIX}") from again
        self.conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(self.schema)))

    def create_database(self):
        name = conninfo_to_dict(self.url).get("dbname")
        if not name:
            return
        try:
            with psycopg.connect(make_conninfo(self.url, dbname="postgres"), autocommit=True) as admin:
                admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        except psycopg.Error:
            pass

    def run(self, work):
        """work(cursor) in one transaction. A connection Postgres dropped is opened again once; a second failure is a
        StoreError, never a hang."""
        with self.lock:
            for attempt in (1, 2):
                try:
                    if self.conn is None or self.conn.closed:
                        self.connect()
                    with self.conn.transaction(), self.conn.cursor() as cur:
                        return work(cur)
                except psycopg.OperationalError as error:
                    if self.conn is not None:
                        try:
                            self.conn.close()
                        except psycopg.Error:
                            pass
                    self.conn = None
                    if attempt == 2:
                        raise StoreError(f"Postgres stopped answering: {first_line(error)}. {FIX}") from error

    def close(self):
        with self.lock:
            if self.conn is not None:
                self.conn.close()
                self.conn = None

    def drop(self):
        """Remove this schema and everything in it (a bench run's or a test's), then close. Never the user's own."""
        if self.schema == SCHEMA:
            raise ValueError(f"Refusing to drop the schema {SCHEMA!r}: it is the user's own memory.")
        self.run(lambda cur: cur.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
            sql.Identifier(self.schema))))
        self.close()

    def migrate(self):
        def work(cur):
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('jev-memory-schema'))")
            cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm SCHEMA public")
            cur.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(self.schema)))
            cur.execute(sql.SQL("SET LOCAL search_path TO {}, public").format(sql.Identifier(self.schema)))
            cur.execute(DDL)
            for column in ("undone", "purged"):  # an events table made before undo and purge had neither
                cur.execute(f"ALTER TABLE events ADD COLUMN IF NOT EXISTS {column} boolean NOT NULL DEFAULT false")
            if cur.execute("SELECT count(*) AS n FROM schema_version").fetchone()["n"] == 0:
                cur.execute("INSERT INTO schema_version (version) VALUES (%s)", (VERSION,))

        self.run(work)

    # ---- payments: one row per bill payment, written "started" before its pay control is pressed

    def payment_started(self, biller, account, amount, method="", task=""):
        """A payment about to be made: its row's id. `account` is the saved ID's key, never its value."""
        if not KEY.match(account or ""):
            raise ValueError(f"A payment names its account by the saved ID's key, not {account!r}.")

        def work(cur):
            row = cur.execute("INSERT INTO payments (biller, account, amount, method, status, task) VALUES "
                              "(%s, %s, %s, %s, 'started', %s) RETURNING id",
                              (biller, account, str(amount), method or "", task)).fetchone()
            self.log(cur, "agent", "payment_started", f"payments:{row['id']}")
            return row["id"]

        return self.run(work)

    def payment_ended(self, payment_id, status, reference=""):
        """How a started payment ended: paid, failed, or unclear (it may have gone through)."""
        def work(cur):
            cur.execute("UPDATE payments SET status = %s, reference = %s, updated_at = now() WHERE id = %s",
                        (status, privacy.storable(reference) and reference or "", payment_id))
            self.log(cur, "agent", "payment_" + status, f"payments:{payment_id}")

        self.run(work)

    def payments(self, biller, account):
        """This biller's payments for this account, newest first."""
        return self.run(lambda cur: cur.execute(
            "SELECT id, biller, amount, method, reference, status, created_at FROM payments WHERE lower(biller) = "
            "lower(%s) AND account = %s ORDER BY created_at DESC, id DESC", (biller, account)).fetchall())

    # ---- events

    @staticmethod
    def log(cur, actor, action, ref, before=None, after=None):
        cur.execute("INSERT INTO events (actor, action, ref, before, after) VALUES (%s, %s, %s, %s, %s)",
                    (actor, action, ref, None if before is None else Jsonb(before),
                     None if after is None else Jsonb(after)))

    @staticmethod
    def snapshot(cur, table, row_id):
        row = cur.execute(sql.SQL("SELECT to_jsonb(t) AS row FROM {} t WHERE id = %s").format(sql.Identifier(table)),
                          (row_id,)).fetchone()
        return row["row"] if row else None

    @staticmethod
    def id_snapshot(cur, key):
        row = cur.execute("SELECT jsonb_build_object('key', key, 'label', label, 'token', encode(value, 'base64')) "
                          "AS row FROM ids WHERE key = %s", (key,)).fetchone()
        return row["row"] if row else None

    # ---- facts

    def hide(self, text):
        """Text with every stored ID replaced by its placeholder."""
        return privacy.Secrets(self.hidden_values()).text(str(text or "").strip())

    def remember(self, kind, text, *, label="", tags=(), data=None, source="", task="", actor="agent",
                 supersede=False):
        """Keep one fact (an ID inside it becomes its placeholder) and return its id. The same live fact twice is kept
        once; with supersede, older live facts of the same kind and label give way to it."""
        if kind not in FACT_KINDS:
            raise ValueError(f"Unknown kind of fact: {kind}")
        raw = f"{str(label or '').strip()} {str(text or '').strip()}"
        text, label, source, task = self.hide(text), self.hide(label), self.hide(source), self.hide(task)[:200]
        if not text:
            raise ValueError("A fact needs words.")
        # the raw words too: a short ID inside a card number would leave a placeholder where its digits were
        if not (privacy.storable(raw) and privacy.storable(f"{label} {text}")):
            raise Unstorable(UNSTORABLE)
        source = source if privacy.storable(source) else ""
        task = task if privacy.storable(task) else ""  # a goal holding a card number is never kept, even as a label
        tags = sorted({str(t).strip().lower() for t in tags if str(t).strip()})

        def work(cur):
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"facts:{kind}:{label}",))
            same = cur.execute("SELECT id FROM facts WHERE kind = %s AND label = %s AND text = %s "
                               "AND superseded_by IS NULL", (kind, label, text)).fetchone()
            if same:
                return same["id"]
            fact = cur.execute("INSERT INTO facts (kind, label, text, tags, data, source, task) VALUES "
                               "(%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                               (kind, label, text, tags, Jsonb(data or {}), source, task)).fetchone()["id"]
            if supersede:
                cur.execute("UPDATE facts SET superseded_by = %s WHERE kind = %s AND label = %s AND id <> %s "
                            "AND superseded_by IS NULL", (fact, kind, label, fact))
            self.log(cur, actor, "remember", f"facts:{fact}", None, self.snapshot(cur, "facts", fact))
            return fact

        return self.run(work)

    def facts(self, kind=None):
        """Live facts (none that a newer one superseded), newest first."""
        return self.run(lambda cur: cur.execute(
            "SELECT id, kind, label, text, tags, data, created_at FROM facts WHERE superseded_by IS NULL "
            "AND (%s::text IS NULL OR kind = %s) ORDER BY created_at DESC, id DESC", (kind, kind)).fetchall())

    def default_app(self, skill):
        """The app saved for a kind of task, or None."""
        return next((f["text"] for f in self.facts("default_app") if f["label"] == skill), None)

    def geolocation(self):
        """The browser's location: the first address kept with coordinates, or None."""
        for fact in reversed(self.facts("address")):
            if "lat" in fact["data"] and "lng" in fact["data"]:
                return {"latitude": fact["data"]["lat"], "longitude": fact["data"]["lng"]}
        return None

    def recall(self, skill, host, goal):
        """The planner's memory block: ID keys and labels, addresses, default apps, this site's standing approvals,
        and up to RECALL_FACTS facts within RECALL_CHARS characters — tagged general first, then tagged with the skill
        or the site, then those closest to the goal by trigram similarity, newest first within each group."""
        facts = self.facts()
        pool = [f for f in facts if f["kind"] in {"preference", "fact"}]
        wanted = {t for t in (skill, host) if t}
        general = [f for f in pool if "general" in f["tags"]]
        tagged = [f for f in pool if f not in general and wanted & set(f["tags"])]
        rest = [f for f in pool if f not in general and f not in tagged]
        close = set()
        if rest and goal:
            close = {r["id"] for r in self.run(lambda cur: cur.execute(
                "SELECT id FROM facts WHERE id = ANY(%s) AND similarity(text, %s) >= %s",
                ([f["id"] for f in rest], goal, CLOSE)).fetchall())}
        lines, used = [], 0
        for fact in general + tagged + [f for f in rest if f["id"] in close]:
            line = f"{fact['label']}: {fact['text']}" if fact["label"] else fact["text"]
            if len(lines) >= RECALL_FACTS or used + len(line) > RECALL_CHARS:
                break
            lines.append(line)
            used += len(line)
        return {"ids": self.ids(),
                "addresses": [{"label": f["label"], "text": f["text"]} for f in reversed(facts)
                              if f["kind"] == "address"],
                "defaults": [f"{f['label']}: {f['text']}" for f in facts if f["kind"] == "default_app"],
                "approvals": [rule_line(r) for r in self.rules_for(host)] if host else [],
                "facts": lines}

    # ---- IDs

    def set_id(self, key, label, value, actor="agent"):
        """Keep one ID, encrypted; the same key again replaces it."""
        if not KEY.match(key or ""):
            raise ValueError(f"Not a usable ID name: {key!r} (lowercase letters, digits, and underscores).")
        value = str(value or "").strip()
        label = str(label or "").strip() or LABELS.get(key) or key.replace("_", " ")
        if not value:
            raise ValueError(f"Your {label} is empty.")
        if not (privacy.storable(label) and privacy.storable(value)):
            raise Unstorable(UNSTORABLE)
        if self.fernet is None:
            raise StoreError("JEV_MEMORY_KEY is missing, so IDs can't be kept.")
        token = self.fernet.encrypt(value.encode())

        def work(cur):
            before = self.id_snapshot(cur, key)
            cur.execute("INSERT INTO ids (key, label, value) VALUES (%s, %s, %s) ON CONFLICT (key) DO UPDATE SET "
                        "label = EXCLUDED.label, value = EXCLUDED.value, updated_at = clock_timestamp()",
                        (key, label, token))
            self.log(cur, actor, "set_id", f"ids:{key}", before, self.id_snapshot(cur, key))

        self.run(work)
        self.stamp = None  # read again on the next call

    def refresh(self):
        """Decrypt the IDs again whenever anything changed them, another task or process included. An ID this key
        can't open counts as missing: it is listed in `unreadable` and asked for again."""
        def work(cur):
            stamp = tuple(cur.execute("SELECT count(*) AS n, max(updated_at) AS at FROM ids").fetchone().values())
            return stamp, (None if stamp == self.stamp else cur.execute("SELECT key, value FROM ids").fetchall())

        stamp, rows = self.run(work)
        if rows is None:
            return
        cache, unreadable = {}, []
        for row in rows:
            try:
                cache[row["key"]] = self.fernet.decrypt(bytes(row["value"])).decode()
            except (InvalidToken, AttributeError):  # another key, or none
                unreadable.append(row["key"])
        self.cache, self.unreadable, self.stamp = cache, sorted(unreadable), stamp

    def ids(self):
        """The IDs the agent can use, as key and label: never a value."""
        self.refresh()
        rows = self.run(lambda cur: cur.execute("SELECT key, label FROM ids ORDER BY key").fetchall())
        return [{"key": r["key"], "label": r["label"]} for r in rows if r["key"] in self.cache]

    def id_value(self, key):
        """An ID's value, for typing it into a page; KeyError when it is missing or this key can't open it."""
        self.refresh()
        return self.cache[key]

    def hidden_values(self):
        """(value, placeholder) pairs every redaction registry hides: each readable ID and its reformattings."""
        self.refresh()
        return [pair for key, value in sorted(self.cache.items()) for pair in privacy.variants(key, value)]

    def has_ids(self):
        return self.run(lambda cur: cur.execute("SELECT EXISTS (SELECT 1 FROM ids) AS found").fetchone()["found"])

    def unlock(self, key):
        """Use this key from now on (one generated at first start)."""
        try:
            self.fernet = Fernet(key)
        except ValueError as error:
            raise StoreError(f"JEV_MEMORY_KEY isn't a valid key ({error}).") from error
        self.stamp = None

    # ---- standing approvals

    def approve(self, site, cart, cap, words, actor="user", days=RULE_DAYS):
        """A standing approval: this site, this cart, up to this cap, for `days` days. Returns its id."""
        cart = [{"item": str(i["item"]), "qty": int(i["qty"])} for i in cart or []]
        if not cart or float(cap) <= 0:
            raise ValueError("A standing approval needs a cart and a cap above zero.")
        words = self.hide(words)[:300]

        def work(cur):
            rule = cur.execute("INSERT INTO approvals (site, cart, cap, words, expires_at) VALUES "
                               "(%s, %s, %s, %s, now() + make_interval(days => %s)) RETURNING id",
                               (site, Jsonb(cart), cap, words, days)).fetchone()["id"]
            self.log(cur, actor, "approve", f"approvals:{rule}", None, self.snapshot(cur, "approvals", rule))
            return rule

        return self.run(work)

    def live_rules(self):
        rows = self.run(lambda cur: cur.execute(
            "SELECT id, site, cart, cap, words, expires_at, uses FROM approvals WHERE revoked_at IS NULL "
            "AND expires_at > now() ORDER BY created_at DESC, id DESC").fetchall())
        return [{**r, "cap": float(r["cap"])} for r in rows]

    def rules_for(self, host):
        """Live standing approvals for this host (bare), its subdomains included."""
        here = (host or "").lower()
        return [r for r in self.live_rules() if here == r["site"] or here.endswith("." + r["site"])]

    def used(self, rule_id):
        """Count one use of a rule, only while it is live: True when it was, False when it was revoked or expired."""
        return self.run(lambda cur: cur.execute(
            "UPDATE approvals SET uses = uses + 1, last_used_at = now() WHERE id = %s AND revoked_at IS NULL "
            "AND expires_at > now() RETURNING id", (rule_id,)).fetchone()) is not None

    # ---- learned site notes

    def draft_notes(self, host, lines, task=""):
        task, lines = self.hide(task)[:200], [self.hide(line) for line in lines]
        task = task if privacy.storable(task) else ""  # a goal holding a card number is never kept, even as a label

        def work(cur):
            return [cur.execute("INSERT INTO notes (host, text, status, task) VALUES (%s, %s, 'draft', %s) "
                                "RETURNING id", (host, line, task)).fetchone()["id"] for line in lines]

        return self.run(work)

    def decide_notes(self, ids, save, actor="user"):
        """Save or skip drafted notes. A site keeps its NOTES_PER_SITE newest saved lines; older ones are dropped."""
        def work(cur):
            hosts = set()
            for note_id in ids:
                before = self.snapshot(cur, "notes", note_id)
                if before is None or before["status"] != "draft":
                    continue
                cur.execute("UPDATE notes SET status = %s, decided_at = clock_timestamp() WHERE id = %s",
                            ("saved" if save else "skipped", note_id))
                self.log(cur, actor, "note_save" if save else "note_skip", f"notes:{note_id}", before,
                         self.snapshot(cur, "notes", note_id))
                hosts.add(before["host"])
            for host in sorted(hosts) if save else ():
                for old in cur.execute("SELECT id FROM notes WHERE host = %s AND status = 'saved' "
                                       "ORDER BY decided_at DESC, id DESC OFFSET %s",
                                       (host, NOTES_PER_SITE)).fetchall():
                    before = self.snapshot(cur, "notes", old["id"])
                    cur.execute("DELETE FROM notes WHERE id = %s", (old["id"],))
                    self.log(cur, actor, "note_drop", f"notes:{old['id']}", before, None)

        self.run(work)

    def notes(self, host):
        return [r["text"] for r in self.run(lambda cur: cur.execute(
            "SELECT text FROM notes WHERE host = %s AND status = 'saved' ORDER BY decided_at, id", (host,)).fetchall())]

    def learned(self):
        """{host: saved learned lines, oldest first}."""
        found = {}
        for row in self.run(lambda cur: cur.execute(
                "SELECT host, text FROM notes WHERE status = 'saved' ORDER BY host, decided_at, id").fetchall()):
            found.setdefault(row["host"], []).append(row["text"])
        return found

    # ---- habits

    def record_habit(self, habit, actor="agent"):
        """Keep a verified run. The same live site, skill, and goal (case and spaces aside) becomes a new version of
        it; an empty cart never replaces a known one. Returns the habit's id."""
        habit = {**habit, "goal": self.hide(habit["goal"]), "title": self.hide(habit["title"])}
        habit = {**habit, **{k: "" for k in ("goal", "title") if not privacy.storable(habit[k])}}  # never a card number
        key = " ".join(habit["goal"].lower().split())
        values = (habit["title"][:80], habit["goal"], habit["start"], Jsonb(habit["plan"]), Jsonb(habit["steps"]))
        cart = Jsonb(habit.get("cart") or [])

        def work(cur):
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",
                        (f"habit:{habit['host']}:{habit['skill']}:{key}",))
            old = cur.execute(f"SELECT id FROM habits WHERE host = %s AND skill = %s AND {GOAL_KEY} = %s "
                              "AND retired_at IS NULL", (habit["host"], habit["skill"], key)).fetchone()
            if old:
                before = self.snapshot(cur, "habits", old["id"])
                cur.execute("UPDATE habits SET title = %s, goal = %s, start = %s, plan = %s, steps = %s, "
                            "cart = CASE WHEN %s = '[]'::jsonb THEN cart ELSE %s END, version = version + 1, "
                            "failed_in_a_row = 0, updated_at = now() WHERE id = %s", (*values, cart, cart, old["id"]))
                habit_id = old["id"]
            else:
                before = None
                habit_id = cur.execute("INSERT INTO habits (host, skill, title, goal, start, plan, steps, cart) "
                                       "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                                       (habit["host"], habit["skill"], *values, cart)).fetchone()["id"]
            self.log(cur, actor, "habit_record", f"habits:{habit_id}", before, {"title": habit["title"][:80]})
            return habit_id

        return self.run(work)

    def habits(self, skill=None, hosts=None, limit=50):
        """Live habits, newest first: for one skill and these hosts, when given. A habit kept under a host's subdomain
        counts for the host (book.olacabs.com for olacabs.com), never a look-alike (evilolacabs.com)."""
        return self.run(lambda cur: cur.execute(
            f"SELECT * FROM habits WHERE retired_at IS NULL AND (%s::text IS NULL OR skill = %s) "
            f"AND (%s::text[] IS NULL OR {UNDER}) ORDER BY updated_at DESC, id DESC LIMIT %s",
            (skill, skill, hosts, hosts, limit)).fetchall())

    def habit(self, habit_id):
        return self.run(lambda cur: cur.execute("SELECT * FROM habits WHERE id = %s", (habit_id,)).fetchone())

    def replayed(self, habit_id, clean, verified, actor="agent"):
        """Count one replay. RETIRE_AFTER replays in a row that did not verify retire the habit."""
        def work(cur):
            row = cur.execute("UPDATE habits SET replays = replays + 1, clean_replays = clean_replays + %s, "
                              "failed_in_a_row = CASE WHEN %s THEN 0 ELSE failed_in_a_row + 1 END, "
                              "last_used_at = now() WHERE id = %s RETURNING failed_in_a_row",
                              (int(clean and verified), verified, habit_id)).fetchone()
            if row and row["failed_in_a_row"] >= RETIRE_AFTER:
                before = self.snapshot(cur, "habits", habit_id)
                cur.execute("UPDATE habits SET retired_at = now() WHERE id = %s", (habit_id,))
                self.log(cur, actor, "habit_retire", f"habits:{habit_id}", before,
                         self.snapshot(cur, "habits", habit_id))

        self.run(work)

    def last_cart(self, site):
        """The cart of the newest live habit on this site or a subdomain of it that recorded a confirmed order, or
        None."""
        row = self.run(lambda cur: cur.execute(
            f"SELECT cart FROM habits WHERE {UNDER} AND retired_at IS NULL AND cart <> '[]'::jsonb "
            "ORDER BY updated_at DESC, id DESC LIMIT 1", ([site],)).fetchone())
        return row["cart"] if row else None

    # ---- forget, undo, purge

    def forget(self, ref, actor="user"):
        """Remove what a ref names — "12" (a fact), "id:phone", "note:7", "habit:5" — or revoke a standing approval
        ("rule:3"). Returns it in words; KeyError when nothing matches. Its event keeps a copy for UNDO_HOURS so undo
        works; then purge() clears it."""
        kind, _, name = ref.partition(":") if ":" in ref else ("fact", "", ref)

        def work(cur):
            if kind == "id":
                before = self.id_snapshot(cur, name)
                if before is None:
                    raise KeyError(ref)
                cur.execute("DELETE FROM ids WHERE key = %s", (name,))
                self.log(cur, actor, "forget", f"ids:{name}", before, None)
                return f"your {before['label']}"
            table = TABLES.get(kind)
            before = self.snapshot(cur, table, int(name)) if table and name.isdigit() else None
            if before is None or before.get("revoked_at"):
                raise KeyError(ref)
            if table == "approvals":
                cur.execute("UPDATE approvals SET revoked_at = now() WHERE id = %s", (int(name),))
                self.log(cur, actor, "revoke", f"approvals:{name}", before, self.snapshot(cur, table, int(name)))
                return f"your standing approval on {before['site']}"
            if table == "facts":  # facts this one superseded come back live: undo must put them back under it
                before["revived"] = [r["id"] for r in cur.execute(
                    "SELECT id FROM facts WHERE superseded_by = %s ORDER BY id", (int(name),)).fetchall()]
            cur.execute(sql.SQL("DELETE FROM {} WHERE id = %s").format(sql.Identifier(table)), (int(name),))
            self.log(cur, actor, "forget", f"{table}:{name}", before, None)
            if table == "facts":
                return f"{before['label']}: {before['text']}" if before["label"] else before["text"]
            return f"the note “{before['text']}”" if table == "notes" else f"the habit “{before['title']}”"

        done = self.run(work)
        self.stamp = None
        return done

    def restore(self, cur, table, name, before):
        """Put back the row an event changed: its earlier state, or remove it when it didn't exist before."""
        if table == "ids":
            if before is None:
                cur.execute("DELETE FROM ids WHERE key = %s", (name,))
            else:
                cur.execute("INSERT INTO ids (key, label, value) VALUES (%s, %s, decode(%s, 'base64')) ON CONFLICT "
                            "(key) DO UPDATE SET label = EXCLUDED.label, value = EXCLUDED.value, "
                            "updated_at = clock_timestamp()",
                            (before["key"], before["label"], before["token"]))
            return
        ident, row_id = sql.Identifier(table), int(name)
        if before is None:
            cur.execute(sql.SQL("DELETE FROM {} WHERE id = %s").format(ident), (row_id,))
            return
        columns = sql.SQL(", ").join(map(sql.Identifier, COLUMNS[table]))
        if cur.execute(sql.SQL("SELECT 1 FROM {} WHERE id = %s").format(ident), (row_id,)).fetchone():
            cur.execute(sql.SQL("UPDATE {t} SET ({c}) = (SELECT {c} FROM jsonb_populate_record(NULL::{t}, %s)) "
                                "WHERE id = %s").format(t=ident, c=columns), (Jsonb(before), row_id))
        else:
            cur.execute(sql.SQL("INSERT INTO {t} SELECT * FROM jsonb_populate_record(NULL::{t}, %s)").format(t=ident),
                        (Jsonb(before),))
        if table == "facts" and before.get("revived"):  # forgetting it revived older facts: they give way again
            cur.execute("UPDATE facts SET superseded_by = %s WHERE id = ANY(%s) AND superseded_by IS NULL",
                        (row_id, before["revived"]))

    def undo(self, actor="user"):
        """Revert the latest change you or the agent made (never a bench run's or the import's). Returns what was
        undone, in words; KeyError when there is nothing left to undo."""
        def work(cur):
            event = cur.execute("SELECT * FROM events WHERE actor IN ('user', 'agent') AND NOT undone "
                                "AND action NOT IN ('undo', 'purge') ORDER BY id DESC LIMIT 1 FOR UPDATE").fetchone()
            if event is None:
                raise KeyError("nothing to undo")
            if event["purged"]:  # its copies are gone: restoring from nothing would delete a live row
                raise KeyError("that was forgotten more than a day ago")
            table, _, name = event["ref"].partition(":")
            self.restore(cur, table, name, event["before"])
            cur.execute("UPDATE events SET undone = true WHERE id = %s", (event["id"],))
            self.log(cur, actor, "undo", event["ref"], event["after"], event["before"])
            return f"{event['action']} {event['ref']}"

        done = self.run(work)
        self.stamp = None
        return done

    def purge(self):
        """Forgotten means gone after UNDO_HOURS: an event loses its copies (and is marked purged, so undo refuses it)
        only when it is at or before a forget of the same row that stands and is that old."""
        self.run(lambda cur: cur.execute(
            "UPDATE events e SET before = NULL, after = NULL, purged = true "
            "WHERE (e.before IS NOT NULL OR e.after IS NOT NULL) AND EXISTS (SELECT 1 FROM events f "
            "WHERE f.ref = e.ref AND f.action IN ('forget', 'note_drop') AND NOT f.undone AND f.id >= e.id "
            "AND f.at < now() - make_interval(hours => %s))", (UNDO_HOURS,)))

    # ---- views and the old profile

    def listing(self):
        """Everything memory holds, for `jev memory` and the chat: never an ID's value. Saved notes come as counts
        per site ("notes") and one by one with their refs ("notes_list"); a draft or skipped note is never listed."""
        saved = self.run(lambda cur: cur.execute(
            "SELECT id, host, text FROM notes WHERE status = 'saved' ORDER BY host, decided_at, id").fetchall())
        notes = {}
        for row in saved:
            notes[row["host"]] = notes.get(row["host"], 0) + 1
        return {
            "facts": [{"ref": str(f["id"]), "kind": f["kind"], "label": f["label"], "text": f["text"]}
                      for f in self.facts()],
            "ids": [{"ref": f"id:{i['key']}", "label": i["label"]} for i in self.ids()],
            "rules": [{"ref": f"rule:{r['id']}", "text": rule_line(r)} for r in self.live_rules()],
            "notes": notes,
            "notes_list": [{"ref": f"note:{r['id']}", "host": r["host"], "text": r["text"]} for r in saved],
            "habits": [{"ref": f"habit:{h['id']}", "host": h["host"], "skill": h["skill"], "title": h["title"],
                        "replays": h["replays"], "clean_replays": h["clean_replays"]} for h in self.habits()],
        }

    def import_profile(self, path):
        """Phase A's profile.json, once: only while memory holds no facts and no IDs. An item memory refuses to keep
        (a card number, say) is skipped; the file is then renamed profile.json.imported. True when it ran."""
        path = Path(path)
        if not path.exists() or self.has_ids() or self.facts():
            return False
        data = json.loads(path.read_text())

        def keep(work):  # one item the store refuses is skipped, never the whole import
            try:
                work()
            except (Unstorable, ValueError):
                pass

        for key, label in LABELS.items():
            if data.get(key):
                keep(lambda key=key, label=label: self.set_id(key, label, data[key], actor="import"))
        for address in data.get("addresses") or []:
            if address.get("text"):
                keep(lambda address=address: self.remember(
                    "address", address["text"], label=address.get("label", ""), tags=["address"],
                    data={k: address[k] for k in ("lat", "lng") if k in address}, actor="import"))
        for preference in data.get("preferences") or []:
            text, task = preference.get("text", "").strip(), preference.get("from_task", "")
            default = re.fullmatch(r"default app for (.+?):\s*(.+)", text, re.IGNORECASE)
            if default:
                skill = default[1].strip().lower()
                keep(lambda default=default, skill=skill, task=task: self.remember(
                    "default_app", default[2].strip(), label=skill, tags=[skill], task=task, actor="import",
                    supersede=True))
            elif text:
                keep(lambda text=text, task=task: self.remember(
                    "preference", text, tags=["general"], task=task, actor="import"))
        path.rename(path.with_name(path.name + ".imported"))
        return True
