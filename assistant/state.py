"""Validated, browser-independent records for one assistant task.

Only code that has already sanitized a browser observation should call
``observe_state``. This module retains a small allowlist of that observation and
never keeps browser objects, screenshots, form values, or temporary secrets.
"""

from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

STATUSES = frozenset({"running", "waiting_user", "waiting_site", "paused", "blocked", "done", "failed", "cancelled"})
REQUIREMENT_KINDS = frozenset({"page", "state", "action", "artifact"})
EVIDENCE_KINDS = frozenset({"page", "browser-event", "visual-observation", "artifact"})
FACT_SOURCES = frozenset({"user", "profile", "page", "derived", "system"})
_SENSITIVE_KEYS = ("password", "passcode", "otp", "cvv", "cvc", "card_number", "secret", "token")
_TERMINAL = frozenset({"done", "failed", "cancelled"})


def _safe(value):
    """Copy only JSON-shaped public values; discard sensitive fields and objects."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value if _safe(item) is not None]
    if isinstance(value, dict):
        return {key: cleaned for key, item in value.items()
                if isinstance(key, str) and not any(secret in key.lower() for secret in _SENSITIVE_KEYS)
                if (cleaned := _safe(item)) is not None}
    return None


def _url_parts(url):
    if not isinstance(url, str):
        raise ValueError("observation URL must be a string")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("observation URL must be HTTP(S) with a host")
    host = parsed.hostname.lower()
    if parsed.port:
        host = f"{host}:{parsed.port}"
    origin = f"{parsed.scheme}://{host}"
    return urlunsplit((parsed.scheme, host, parsed.path, "", "")), origin


def _provenance_texts(items):
    """Keep public event/artifact descriptions, never arbitrary payload fields."""
    if not isinstance(items, list):
        raise ValueError("provenance items must be a list")
    texts = []
    for item in items:
        if isinstance(item, str):
            texts.append(item)
        elif isinstance(item, dict) and isinstance(item.get("text"), str):
            texts.append(item["text"])
        else:
            raise ValueError("provenance item needs public text")
    return texts


@dataclass
class TaskState:
    task_id: str
    goal: str
    revision: int = 0
    status: str = "running"
    objective: str = ""
    updates: list = field(default_factory=list)
    constraints: dict = field(default_factory=dict)
    facts: dict = field(default_factory=dict)
    requirements: dict = field(default_factory=dict)
    questions: dict = field(default_factory=dict)
    challenges: dict = field(default_factory=dict)
    observations: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    executions: dict = field(default_factory=dict)
    progress: dict = field(default_factory=dict)
    approval: dict | None = None
    proposal: dict | None = None
    completion: list = field(default_factory=list)
    delivered: dict = field(default_factory=dict)
    diagnostics: list = field(default_factory=list)
    _next_observation: int = field(default=1, init=False, repr=False)
    _next_evidence: int = field(default=1, init=False, repr=False)

    def __setattr__(self, name, value):
        if name == "goal" and "goal" in self.__dict__:
            raise AttributeError("original goal is immutable")
        super().__setattr__(name, value)

    def __post_init__(self):
        if not isinstance(self.task_id, str) or not self.task_id or not isinstance(self.goal, str) or not self.goal:
            raise ValueError("task ID and goal are required")
        if self.status != "running" or self.revision != 0:
            raise ValueError("new tasks must start running at revision 0")
        self.objective = self.objective or self.goal

    def set_status(self, status):
        if status not in STATUSES:
            raise ValueError("unknown task status")
        if self.status in _TERMINAL and status != self.status:
            raise ValueError("terminal task cannot change status")
        if status == "done":
            raise ValueError("completion must pass the completion gate")
        self.status = status

    def add_requirement(self, requirement_id, description, kind, *, material_keys=(), evidence_ids=(),
                        execution_id=None, artifact_id=None):
        if not isinstance(requirement_id, str) or not requirement_id or requirement_id in self.requirements:
            raise ValueError("requirement ID must be new and nonempty")
        if not isinstance(description, str) or not description or kind not in REQUIREMENT_KINDS:
            raise ValueError("invalid completion requirement")
        if not all(isinstance(key, str) and key for key in material_keys):
            raise ValueError("invalid material fact key")
        if not all(eid in self.evidence for eid in evidence_ids):
            raise ValueError("unknown requirement evidence")
        if execution_id is not None and execution_id not in self.executions:
            raise ValueError("unknown execution")
        self.requirements[requirement_id] = {
            "id": requirement_id, "description": description, "kind": kind,
            "material_keys": tuple(material_keys), "evidence_ids": tuple(evidence_ids),
            "execution_id": execution_id, "artifact_id": artifact_id,
        }


def observe_state(state: TaskState, page: dict) -> str:
    """Retain only public observation fields and allocate a task-scoped ID."""
    if not isinstance(page, dict) or not isinstance(page.get("text"), str):
        raise ValueError("observation needs page text")
    url, origin = _url_parts(page.get("url"))
    oid = f"{state.task_id}:o{state._next_observation}"
    state._next_observation += 1
    actions = []
    for action in page.get("actions", []):
        if not isinstance(action, dict) or not isinstance(action.get("id"), str):
            raise ValueError("action needs a code-owned ID")
        actions.append({key: _safe(action[key]) for key in ("id", "kind", "label", "role", "constraints")
                        if key in action})
    text = page["text"]
    for action in page.get("actions", []):
        if isinstance(action, dict) and any(word in str(action.get("label", "")).lower()
                                            or word in str(action.get("constraints", "")).lower()
                                            for word in _SENSITIVE_KEYS):
            value = action.get("value")
            if isinstance(value, str) and value:
                text = text.replace(value, "[redacted]")
    record = {"id": oid, "revision": state.revision, "url": url, "origin": origin,
              "text": text, "actions": actions,
              "completeness": _safe(page.get("completeness", {})) or {}}
    # These provenance references are created here from observed payload presence.
    if page.get("screenshot"):
        record["screenshot_ref"] = f"{oid}:screenshot"
    record["events"] = _provenance_texts(page.get("events", []))
    record["artifacts"] = _provenance_texts(page.get("artifacts", []))
    state.observations[oid] = record
    return oid


def cite(state: TaskState, observation_id: str, quote: str, *, kind: str = "page") -> str:
    """Create evidence only when the claimed provenance is present and current."""
    observation = state.observations.get(observation_id)
    if observation is None or observation["revision"] != state.revision:
        raise ValueError("observation is unknown or stale")
    if kind not in EVIDENCE_KINDS or not isinstance(quote, str) or not quote.strip():
        raise ValueError("invalid evidence kind or quote")
    if kind == "page" and quote not in observation["text"]:
        raise ValueError("quote is absent from page")
    if kind == "browser-event" and not any(quote in text for text in observation["events"]):
        raise ValueError("quote is absent from browser events")
    if kind == "artifact" and not any(quote in text for text in observation["artifacts"]):
        raise ValueError("quote is absent from artifacts")
    if kind == "visual-observation" and "screenshot_ref" not in observation:
        raise ValueError("visual evidence needs a screenshot reference")
    eid = f"{state.task_id}:e{state._next_evidence}"
    state._next_evidence += 1
    record = {"id": eid, "observation_id": observation_id, "revision": state.revision,
              "origin": observation["origin"], "kind": kind, "quote": quote}
    if kind == "visual-observation":
        record["screenshot_ref"] = observation["screenshot_ref"]
    state.evidence[eid] = record
    return eid


def put_fact(state: TaskState, key: str, value: object, *, source: str, scope: str = "task",
             depends_on: tuple[str, ...] = (), evidence: tuple[str, ...] = ()) -> None:
    if not isinstance(key, str) or not key or source not in FACT_SOURCES:
        raise ValueError("invalid fact key or source")
    if not isinstance(scope, str) or not scope:
        raise ValueError("invalid fact scope")
    if any(secret in key.lower() for secret in _SENSITIVE_KEYS):
        if not isinstance(value, str) or not value.startswith("[") or not value.endswith("]"):
            raise ValueError("secret facts require opaque references")
    cleaned = _safe(value)
    if cleaned is None and value is not None:
        raise ValueError("fact value must be public JSON data")
    if any(dep not in state.facts or dep == key for dep in depends_on):
        raise ValueError("fact dependency is unknown or self-referential")
    if source in {"page", "derived"} and not evidence:
        raise ValueError("page-derived facts require evidence")
    if any(eid not in state.evidence or state.evidence[eid]["revision"] != state.revision
           for eid in evidence):
        raise ValueError("fact evidence is unknown or stale")
    existing = state.facts.get(key)
    if existing and existing["valid"] and existing["source"] == "user" and source != "user":
        return
    state.facts[key] = {"key": key, "value": cleaned, "source": source, "scope": scope,
                        "revision": state.revision, "depends_on": tuple(depends_on),
                        "evidence": tuple(evidence), "valid": True}


def amend(state: TaskState, text: str, changed_keys: set[str]) -> None:
    if not isinstance(text, str) or not text.strip() or not isinstance(changed_keys, set):
        raise ValueError("amendment requires text and changed key set")
    if not all(isinstance(key, str) and key for key in changed_keys):
        raise ValueError("invalid changed key")
    affected = set(changed_keys)
    while True:
        more = {key for key, fact in state.facts.items()
                if affected.intersection(fact["depends_on"])} - affected
        if not more:
            break
        affected.update(more)
    for key in affected:
        if key in state.facts:
            state.facts[key]["valid"] = False
    state.revision += 1
    state.updates.append(text)
    state.approval = None
    state.proposal = None


def public_context(state: TaskState) -> dict:
    """Return a serializable view with the immutable goal and active constraints."""
    return _safe({"task_id": state.task_id, "goal": state.goal, "revision": state.revision,
                  "status": state.status, "objective": state.objective, "updates": state.updates,
                  "constraints": state.constraints, "facts": state.facts,
                  "requirements": state.requirements, "questions": state.questions,
                  "challenges": state.challenges, "observation_ids": list(state.observations),
                  "evidence": state.evidence, "progress": state.progress,
                  "approval": state.approval, "proposal": state.proposal,
                  "completion": state.completion, "delivered": state.delivered,
                  "diagnostics": state.diagnostics})
