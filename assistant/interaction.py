"""Requirement identity and chat policy. Answers create facts, never browser actions.

Input values are already routed to named fact keys by the chat owner. Secret values
must have been replaced by challenge-bound opaque references by the secret owner.
"""

import math
from copy import deepcopy

from .state import TaskState, put_fact

KINDS = {"text", "choice", "secret", "file", "image"}
REASK_REASONS = {"expiration", "observed validation failure", "changed request", "invalid format", "genuine ambiguity"}
COMPARE_NOTICE = "Comparison may require separate logins and multiple OTPs."


def requirement_key(requirement):
    return (requirement["purpose"], requirement["scope"], tuple(sorted(requirement["keys"])))


def _evidence(state, ids, *, required=False):
    if not isinstance(ids, (list, tuple)) or (required and not ids):
        raise ValueError("evidence required")
    latest = next(reversed(state.observations), None)
    for eid in ids:
        record = state.evidence.get(eid) if isinstance(eid, str) else None
        if not record or record["revision"] != state.revision or record["observation_id"] != latest:
            raise ValueError("unknown or stale evidence")


def _validate(state, req):
    fields = {"id", "kind", "purpose", "keys", "scope", "depends_on", "options", "reason"}
    if not isinstance(req, dict) or set(req) != fields:
        raise ValueError("invalid requirement fields")
    if any(not isinstance(req[k], str) or not req[k].strip() for k in ("id", "kind", "purpose", "scope", "reason")):
        raise ValueError("invalid requirement identity")
    if req["kind"] not in KINDS:
        raise ValueError("invalid requirement kind")
    for name in ("keys", "depends_on"):
        values = req[name]
        if not isinstance(values, list) or any(not isinstance(v, str) or not v for v in values):
            raise ValueError("invalid requirement keys/dependencies")
        if len(values) != len(set(values)):
            raise ValueError("duplicate requirement keys/dependencies")
    if not req["keys"] or any(d not in state.requirements or d == req["id"]
                              or state.requirements[d].get("kind") not in KINDS for d in req["depends_on"]):
        raise ValueError("unknown requirement dependency")
    if not isinstance(req["options"], list) or req["options"] and req["kind"] != "choice":
        raise ValueError("options require choice kind")
    ids = set()
    for option in req["options"]:
        if not isinstance(option, dict) or set(option) != {"id", "label", "description", "evidence_ids"}:
            raise ValueError("invalid option fields")
        if any(not isinstance(option[k], str) for k in ("id", "label", "description")):
            raise ValueError("invalid option strings")
        if not option["id"] or not option["label"].strip() or option["id"] in ids:
            raise ValueError("duplicate or empty option")
        ids.add(option["id"])
        _evidence(state, option["evidence_ids"], required=True)


def _missing(state, req):
    return tuple(key for key in req["keys"] if not (
        (fact := state.facts.get(key)) and fact["valid"] and fact["value"] not in (None, "")
        and fact["scope"] in {req["scope"], "task"}
        and fact.get("purpose", req["purpose"]) == req["purpose"]
        and (key != "provider" or req["purpose"] != "task" or fact["source"] == "user")))


def _question_text(state, q):
    reqs = [state.requirements[rid] for rid in q["requirement_ids"]]
    keys = [key for req in reqs for key in _missing(state, req)]
    if "provider" in keys:
        extra = [key.replace("_", " ") for key in keys if key != "provider"]
        return ("Which provider would you prefer, or should I compare providers? " + COMPARE_NOTICE
                + (" Please also provide " + ", ".join(extra) + "." if extra else ""))
    return "Please provide " + ", ".join(key.replace("_", " ") for key in keys) + "."


def require(state: TaskState, requirement: dict) -> dict | None:
    """Register a semantic need; return its stable unresolved question, if ready."""
    _validate(state, requirement)
    existing = state.requirements.get(requirement["id"])
    if existing and (existing.get("kind") not in KINDS or requirement_key(existing) != requirement_key(requirement)):
        raise ValueError("requirement ID reused for a different meaning")
    req = next((r for r in state.requirements.values() if r.get("kind") in KINDS
                and requirement_key(r) == requirement_key(requirement)), None)
    if req is None:
        req = deepcopy(requirement)
        state.requirements[req["id"]] = req
    elif req["kind"] != requirement["kind"]:
        raise ValueError("requirement kind changed")
    pending_deps, visited = list(requirement["depends_on"]), set()
    while pending_deps:
        dep = pending_deps.pop()
        if dep == req["id"]:
            raise ValueError("requirement dependency cycle")
        if dep not in visited:
            visited.add(dep)
            pending_deps.extend(state.requirements[dep]["depends_on"])
    previous_deps = set(req["depends_on"])
    current_deps = set(requirement["depends_on"])
    if current_deps != previous_deps:
        req["depends_on"] = list(requirement["depends_on"])
        if current_deps - previous_deps:
            # An old answer did not account for the newly discovered prerequisite.
            reopen(state, req["id"], "genuine ambiguity", ())
        else:
            removed_keys = {key for rid in previous_deps - current_deps for key in state.requirements[rid]["keys"]}
            retained_keys = {key for rid in current_deps for key in state.requirements[rid]["keys"]}
            for key in req["keys"]:
                fact = state.facts.get(key)
                if fact and fact["scope"] in {req["scope"], "task"}:
                    fact["depends_on"] = tuple(k for k in fact["depends_on"] if k not in removed_keys - retained_keys)
    if any(_missing(state, state.requirements[dep]) for dep in req["depends_on"]):
        req["satisfied"] = False
        return None
    if req["kind"] == "choice":
        meanings = {(o["label"], o["description"]): o for o in requirement["options"]}
        for key in req["keys"]:
            fact = state.facts.get(key)
            if fact and fact["valid"] and fact["scope"] in {req["scope"], "task"}:
                value = fact["value"]
                explicit = fact["source"] == "user" or (
                    fact["source"] == "profile" and fact.get("explicit_default") is True)
                selected = (meanings.get((value.get("label"), value.get("description")))
                            if explicit and isinstance(value, dict) else None)
                if explicit and isinstance(value, str):
                    matches = [o for o in requirement["options"]
                               if o["label"].strip().casefold() == value.strip().casefold()]
                    selected = matches[0] if len(matches) == 1 else None
                if selected:
                    fact["value"] = {"label": selected["label"], "description": selected["description"]}
                    fact["evidence"] = tuple(selected["evidence_ids"])
                else:
                    reopen(state, req["id"], "genuine ambiguity", tuple(
                        eid for o in requirement["options"] for eid in o["evidence_ids"]))
        for pending in state.questions.values():
            if req["id"] not in pending["requirement_ids"] or pending["answered_at"] is not None:
                continue
            if {(o["label"], o["description"]) for o in pending["options"]} != set(meanings):
                pending["closed"] = True
            else:
                for option in pending["options"]:
                    option["evidence_ids"] = list(meanings[(option["label"], option["description"])]["evidence_ids"])
        req["options"] = deepcopy(requirement["options"])
    for key in req["keys"]:
        if key not in _missing(state, req):
            fact = state.facts[key]
            fact["depends_on"] = tuple(dict.fromkeys((*fact["depends_on"], *(
                k for rid in req["depends_on"] for k in state.requirements[rid]["keys"]))))
    req["satisfied"] = not _missing(state, req)
    if req["satisfied"]:
        return None
    for q in state.questions.values():
        if (req["id"] in q["requirement_ids"] and q["revision"] == state.revision
                and q["answered_at"] is None and not q.get("closed")):
            return q
    # Related text needs can be grouped only before the question is displayed.
    group = next((q for q in state.questions.values() if req["kind"] == q["kind"] == "text"
                  and q["displayed_at"] is None and q["answered_at"] is None and not q.get("closed")
                  and q["revision"] == state.revision
                  and all(state.requirements[r]["purpose"] == req["purpose"]
                          and state.requirements[r]["scope"] == req["scope"] for r in q["requirement_ids"])), None)
    if group:
        group["requirement_ids"].append(req["id"])
        group["text"] = _question_text(state, group)
        return group
    qid = f"{state.task_id}-q{len(state.questions) + 1}"
    q = {"id": qid, "revision": state.revision, "requirement_ids": [req["id"]], "kind": req["kind"],
         "text": "", "options": deepcopy(requirement["options"]), "challenge_id": None,
         "displayed_at": None, "answered_at": None}
    q["text"] = _question_text(state, q)
    state.questions[qid] = q
    return q


def answer_requirements(state: TaskState, question_id: str, values: dict, *, now: float) -> tuple[str, ...]:
    """Apply a partial answer atomically; return the question's unresolved needs."""
    q = state.questions.get(question_id)
    if q is None or q["revision"] != state.revision or q.get("closed"):
        raise ValueError("unknown or stale question")
    if q["answered_at"] is not None:
        return ()  # duplicate/late replies never overwrite an accepted answer
    if not isinstance(values, dict) or type(now) not in (float, int) or not math.isfinite(now) or now < 0:
        raise ValueError("invalid answer")
    reqs = [state.requirements[rid] for rid in q["requirement_ids"]]
    missing = {key: req for req in reqs for key in _missing(state, req)}
    if set(values) - set(missing):
        raise ValueError("answer keys do not match unresolved requirements")
    trial = deepcopy(state)
    for key, value in values.items():
        req = missing[key]
        evidence = ()
        if value is None or value == "" or isinstance(value, str) and not value.strip():
            raise ValueError("empty answer")
        if req["kind"] == "secret" and not (
                isinstance(value, str) and value.startswith("[") and value.endswith("]")):
            raise ValueError("secret answer needs an opaque reference")
        if req["kind"] == "choice":
            option = next((opt for opt in q["options"] if opt["id"] == value), None)
            if option is None:
                raise ValueError("answer must identify an exact option")
            _evidence(state, option["evidence_ids"], required=True)
            evidence = tuple(option["evidence_ids"])
            value = {"label": option["label"], "description": option["description"]}
        dependencies = tuple(k for rid in req["depends_on"] for k in state.requirements[rid]["keys"])
        put_fact(trial, key, value, source="user", scope=req["scope"],
                 depends_on=dependencies, evidence=evidence)
        trial.facts[key].update(purpose=req["purpose"], question_id=question_id, answered_at=now)
    state.facts = trial.facts
    for req in reqs:
        req["satisfied"] = not _missing(state, req)
    unresolved = tuple(r["id"] for r in reqs if not r["satisfied"])
    if not unresolved:
        q["answered_at"] = now
    else:
        q["text"] = _question_text(state, q)
    return unresolved


def reopen(state: TaskState, requirement_id: str, reason: str, evidence_ids: tuple[str, ...]) -> None:
    if reason not in REASK_REASONS:
        raise ValueError("unsupported reask reason")
    req = state.requirements.get(requirement_id)
    if not req or req.get("kind") not in KINDS:
        raise ValueError("unknown information requirement")
    _evidence(state, evidence_ids, required=reason == "observed validation failure")
    for key in req["keys"]:
        if key in state.facts and state.facts[key]["scope"] in {req["scope"], "task"}:
            state.facts[key]["valid"] = False
    affected = set(req["keys"])
    while additional := {key for key, fact in state.facts.items()
                         if affected.intersection(fact["depends_on"])} - affected:
        affected.update(additional)
    for key in affected - set(req["keys"]):
        state.facts[key]["valid"] = False
    req["satisfied"] = False
    req["reopened"] = {"reason": reason, "evidence_ids": tuple(evidence_ids), "revision": state.revision}
    for q in state.questions.values():
        if requirement_id in q["requirement_ids"]:
            q["closed"] = True


def provider_mode(state: TaskState, defaults: dict) -> dict:
    """Resolve only explicit instructions/defaults. Selection is not credential permission.

    Provider identity and availability still require fresh browser evidence at the
    secret gate. Provider comparison results, including skips, remain in progress.
    """
    def explicit(key):
        if key in state.constraints:
            return state.constraints[key]
        fact = state.facts.get(key)
        if fact and fact["valid"] and fact["source"] == "user" and fact["scope"] == "task":
            return fact["value"]
        return None

    provider = explicit("provider")
    if explicit("compare") is True or provider == "compare":
        return {"mode": "compare", "provider": None, "notice": COMPARE_NOTICE}
    if isinstance(provider, str) and provider.strip():
        return {"mode": "selected", "provider": provider, "notice": None}
    saved = defaults.get("provider")
    if (isinstance(saved, dict) and saved.get("consent") is True and saved.get("source") == "user"
            and saved.get("scope") in {"task", state.constraints.get("provider_scope", "task")}
            and isinstance(saved.get("value"), str) and saved["value"].strip()):
        return {"mode": "selected", "provider": saved["value"],
                "notice": f"Using your saved default provider: {saved['value']}."}
    require(state, {"id": "provider", "kind": "text", "purpose": "task", "keys": ["provider"], "scope": "task",
                    "depends_on": [], "options": [], "reason": "preferred provider or comparison needed"})
    return {"mode": "ask", "provider": None, "notice": COMPARE_NOTICE}


def chat_update(state: TaskState, notice: dict) -> dict | None:
    """Validate factual updates and deduplicate by semantic evidence, not wording."""
    if not isinstance(notice, dict) or set(notice) != {"kind", "text", "evidence_ids", "requirement_ids"}:
        raise ValueError("invalid notice")
    if any(not isinstance(notice[k], str) or not notice[k].strip() for k in ("kind", "text")):
        raise ValueError("invalid notice text")
    ids = notice["requirement_ids"]
    if not isinstance(ids, list) or any(not isinstance(r, str) or r not in state.requirements for r in ids):
        raise ValueError("unknown notice requirement")
    _evidence(state, notice["evidence_ids"], required=notice["kind"] != "start")
    semantic = sorted({(state.evidence[e]["origin"], state.evidence[e]["kind"], state.evidence[e]["quote"])
                       for e in notice["evidence_ids"]})
    signature = [state.revision, notice["kind"], [list(p) for p in semantic], sorted(ids)]
    delivered = state.progress.setdefault("chat_notices", [])
    if signature in delivered:
        return None
    delivered.append(signature)
    text = f"I’ll help with: {state.goal}" if notice["kind"] == "start" else notice["text"]
    return {"kind": "info", "text": text}
