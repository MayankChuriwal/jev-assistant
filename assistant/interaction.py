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


class SecretStore:
    """Process-only scoped values. Consume on submission, including auto-submit typing.

    The execution owner consumes before dispatch and records the opaque reference in
    its execution ledger. An uncertain dispatch never restores a consumed value.
    """

    __slots__ = ("clock", "__entries")
    SCOPE = frozenset({"task_id", "question_id", "challenge_id", "origin", "purpose", "field_role"})

    def __init__(self, clock):
        self.clock = clock
        self.__entries = {}

    def __getstate__(self):
        raise TypeError("temporary secrets cannot be serialized")

    def put(self, value: str, *, task_id: str, question_id: str, challenge_id: str,
            origin: str, purpose: str, field_role: str, expires_at: float) -> str:
        from secrets import token_urlsafe
        from urllib.parse import urlsplit

        scope = dict(task_id=task_id, question_id=question_id, challenge_id=challenge_id,
                     origin=origin, purpose=purpose, field_role=field_role)
        if any(not isinstance(v, str) or not v.strip() for v in scope.values()):
            raise ValueError("invalid secret scope")
        parsed = urlsplit(origin)
        if (parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username
                or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
            raise ValueError("invalid secret origin")
        if not isinstance(value, str) or not value:
            raise ValueError("invalid secret value")
        now = self.clock()
        if type(expires_at) not in (int, float) or not math.isfinite(expires_at) or expires_at <= now:
            raise ValueError("secret unavailable")
        # Replacing a challenge invalidates every earlier code for the same purpose.
        for ref, entry in list(self.__entries.items()):
            if all(entry[k] == scope[k] for k in ("task_id", "origin", "purpose", "field_role")):
                del self.__entries[ref]
        cap = 900 if field_role in {"card_number", "card_expiry", "card_name", "cvv",
                                            "number", "expiry", "card.number", "card.cvv", "card.expiry"} else 600
        ref = f"[secret:{token_urlsafe(24)}]"
        self.__entries[ref] = {**scope, "value": value, "expires_at": min(expires_at, now + cap)}
        return ref

    def resolve(self, ref: str, **scope) -> str:
        entry = self.__entries.get(ref)
        if entry is None or entry["expires_at"] <= self.clock():
            self.__entries.pop(ref, None)
            raise ValueError("secret unavailable")
        if set(scope) != self.SCOPE or any(entry[key] != scope[key] for key in self.SCOPE):
            raise ValueError("secret scope mismatch")
        return entry["value"]

    def consume(self, ref: str) -> None:
        self.__entries.pop(ref, None)

    def purge(self, task_id: str | None = None) -> None:
        for ref, entry in list(self.__entries.items()):
            if task_id is None or entry["task_id"] == task_id:
                del self.__entries[ref]


def field_cache_key(state: TaskState, helper_input: dict, challenge_id: str | None) -> str:
    """Hash the complete already-sanitized helper payload, not a field heuristic."""
    import hashlib
    import json

    from .profile import SECRETS

    registry = SECRETS.get()
    payload = registry.redact(helper_input) if registry else helper_input
    encoded = json.dumps([state.task_id, state.revision, challenge_id, payload],
                         sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


AUTH_STATUSES = frozenset({'unknown', 'required', 'challenge', 'submitted', 'authenticated',
                           'rejected', 'expired', 'unsupported'})
AUTH_FIELDS = frozenset({'flow_id', 'challenge_id', 'status', 'origin', 'purpose', 'field_role',
                         'evidence_ids', 'execution_id', 'expires_at', 'allowed_origins'})


def observe_auth(state: TaskState, event: dict) -> dict:
    """Apply a semantic observation, never infer failure from field reselection.

    Challenges and execution receipts are registered by code, not the coordinator.
    A submission receipt must declare ``submission=True`` even for auto-submit
    typing. Redirect receipts declare kind=navigation and cite the destination
    observation. Merely visiting an origin never permits credentials there.
    """
    from urllib.parse import urlsplit

    if not isinstance(event, dict) or set(event) != AUTH_FIELDS:
        raise ValueError('invalid auth event fields')
    for key in ('flow_id', 'status', 'origin', 'purpose', 'field_role'):
        if not isinstance(event[key], str) or not event[key].strip():
            raise ValueError('invalid auth identity')
    if event['status'] not in AUTH_STATUSES:
        raise ValueError('invalid auth status')
    for key in ('challenge_id', 'execution_id'):
        if event[key] is not None and (not isinstance(event[key], str) or not event[key]):
            raise ValueError('invalid auth reference')
    expiry = event['expires_at']
    if expiry is not None and (type(expiry) not in (float, int) or not math.isfinite(expiry) or expiry < 0):
        raise ValueError('invalid auth expiry')
    _evidence(state, event['evidence_ids'], required=True)
    proof = [state.evidence[eid] for eid in event['evidence_ids']]
    if any(e['origin'] != event['origin'] or e['kind'] not in {'page', 'browser-event'} for e in proof):
        raise ValueError('auth evidence has incompatible source/origin')
    origins = event['allowed_origins']
    if not isinstance(origins, list) or any(not isinstance(o, str) for o in origins):
        raise ValueError('invalid auth origins')
    if len(origins) != len(set(origins)) or event['origin'] not in origins:
        raise ValueError('auth origin missing or duplicated')
    for origin in origins:
        parsed = urlsplit(origin)
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username
                or parsed.password or parsed.path or parsed.query or parsed.fragment):
            raise ValueError('invalid auth origin')
    flows = state.progress.get('auth_flows', {})
    previous = flows.get(event['flow_id'])
    if previous and (previous['revision'] != state.revision or previous['purpose'] != event['purpose']):
        raise ValueError('incompatible auth flow revision/purpose/role')
    allowed = set(previous['allowed_origins']) if previous else {event['origin']}
    order = list(state.observations)
    latest = order[-1]
    # Transitive expansion is allowed only through actual successful navigation
    # receipts, preserving the original flow rather than trusting a supplied list.
    pending = set(origins) - allowed
    while pending:
        verified = set()
        for origin in pending:
            for execution in state.executions.values():
                ids = execution.get('evidence_ids', ())
                if (execution.get('flow_id') == event['flow_id']
                        and execution.get('revision') == state.revision
                        and execution.get('kind') == 'navigation'
                        and execution.get('status') == 'succeeded'
                        and execution.get('origin') in allowed
                        and execution.get('destination_origin') == origin
                        and execution.get('observation_id') in order
                        and order.index(execution['observation_id']) < order.index(latest)
                        and ids and all(e in state.evidence and state.evidence[e]['revision'] == state.revision
                                        and state.evidence[e]['origin'] == origin
                                        and order.index(state.evidence[e]['observation_id'])
                                        > order.index(execution['observation_id']) for e in ids)):
                    verified.add(origin)
        if not verified:
            raise ValueError('auth origin requires verified flow navigation')
        allowed.update(verified)
        pending -= verified
    if previous and not set(previous['allowed_origins']).issubset(origins):
        raise ValueError('auth flow cannot discard its origin binding')
    challenge = state.challenges.get(event['challenge_id'])
    if event['challenge_id'] is not None:
        if not challenge or challenge.get('id') != event['challenge_id'] or any(
                challenge.get(k) != event[k] for k in ('flow_id', 'origin', 'purpose', 'field_role')):
            raise ValueError('incompatible auth challenge scope')
        if (challenge.get('revision') != state.revision
                or challenge.get('observation_id') not in state.observations
                or state.observations[challenge['observation_id']]['revision'] != state.revision
                or state.observations[challenge['observation_id']]['origin'] != challenge['origin']):
            raise ValueError('incompatible auth challenge revision/source')
    if challenge and challenge.get('expires_at') is not None:
        expiry = min(expiry, challenge['expires_at']) if expiry is not None else challenge['expires_at']
    status = event['status']
    if status in {'challenge', 'submitted', 'rejected', 'expired'} and challenge is None:
        raise ValueError('auth transition requires a challenge')
    execution = state.executions.get(event['execution_id'])
    if event['execution_id'] is not None or status in {'submitted', 'rejected'}:
        if (not execution or execution.get('submission') is not True
                or execution.get('revision') != state.revision
                or execution.get('status') not in {'dispatched', 'succeeded', 'uncertain'}
                or execution.get('observation_id') not in state.observations
                or any(execution.get(k) != event[k] for k in
                       ('flow_id', 'challenge_id', 'origin', 'purpose', 'field_role'))):
            raise ValueError('auth transition has no matching submission')
        submitted = state.observations[execution['observation_id']]
        if submitted['revision'] != state.revision or submitted['origin'] != event['origin']:
            raise ValueError('incompatible submission observation')
        if status in {'rejected', 'authenticated'} and order.index(execution['observation_id']) >= order.index(latest):
            raise ValueError('auth outcome requires subsequent evidence')
    if status == 'authenticated' and challenge and execution is None:
        raise ValueError('challenge authentication requires matching submission')
    terminal = {'expired', 'rejected', 'authenticated'}
    if challenge and challenge.get('status') in terminal and status not in terminal:
        raise ValueError('challenge is no longer usable')
    if previous and previous['status'] == 'submitted':
        pending = state.executions[previous['execution_id']]
        same_submission = status == 'submitted' and event['execution_id'] == previous['execution_id']
        outcome = (event['challenge_id'] == pending['challenge_id']
                   and ((status in {'rejected', 'authenticated'}
                         and event['execution_id'] == previous['execution_id'])
                        or (status == 'expired'
                            and order.index(latest) > order.index(pending['observation_id']))))
        replacement = (status == 'challenge' and challenge
                       and event['challenge_id'] != pending['challenge_id']
                       and order.index(challenge['observation_id']) > order.index(pending['observation_id']))
        if not (same_submission or outcome or replacement):
            raise ValueError('auth submission is pending; wait for observed outcome or new challenge')
    rejected = list(previous.get('rejected_executions', [])) if previous else []
    if status == 'rejected' and event['execution_id'] not in rejected:
        rejected.append(event['execution_id'])
    limit = 2 if event['field_role'] == 'cvv' or event['purpose'] == 'cvv' else 3
    blocked = bool(previous and previous.get('blocked')) or len(rejected) >= limit
    if blocked and status in {'challenge', 'submitted'}:
        raise ValueError('auth rejection limit reached')
    flow = {**deepcopy(event), 'expires_at': expiry, 'revision': state.revision, 'rejections': len(rejected),
            'rejected_executions': rejected, 'blocked': blocked}
    if previous and challenge and previous.get('challenge_id') != event['challenge_id']:
        old = state.challenges.get(previous.get('challenge_id'))
        if old:
            old['status'] = 'expired'
            for fact in state.facts.values():
                if fact['scope'] == old['id'] or fact.get('challenge_id') == old['id']:
                    fact['valid'] = False
    if challenge:
        challenge.update(status=status, expires_at=expiry, evidence_ids=list(event['evidence_ids']))
        if status in {'expired', 'rejected', 'authenticated'}:
            for fact in state.facts.values():
                if fact['scope'] == challenge['id'] or fact.get('challenge_id') == challenge['id']:
                    fact['valid'] = False
    state.progress.setdefault('auth_flows', {})[event['flow_id']] = flow
    return deepcopy(flow)


def captcha_answer(challenge: dict, reply: str, *, revision: int) -> dict:
    """Validate a human reply using the code-owned displayed challenge schema."""
    import re

    if (not isinstance(challenge, dict) or type(revision) is not int
            or type(challenge.get('revision')) is not int or challenge['revision'] != revision):
        raise ValueError('stale CAPTCHA; show the fresh challenge image in chat')
    if any(not isinstance(challenge.get(k), str) or not challenge[k] for k in ('id', 'observation_id')):
        raise ValueError('invalid CAPTCHA identity')
    rounds = challenge.get('rounds')
    if type(rounds) is not int or rounds < 0:
        raise ValueError('invalid CAPTCHA round count')
    if rounds >= 8:
        raise ValueError('CAPTCHA stopped after eight rounds')
    if challenge.get('kind') not in {'text', 'tiles'}:
        raise ValueError('This CAPTCHA cannot be completed through chat')
    if not isinstance(reply, str) or not reply.strip() or len(reply) > 4096:
        raise ValueError('invalid CAPTCHA answer')
    if challenge['kind'] == 'text':
        return {'kind': 'text', 'value': reply}
    tiles = challenge.get('tiles')
    if (not isinstance(tiles, list) or not tiles or any(not isinstance(t, str) or not t for t in tiles)
            or len(set(tiles)) != len(tiles)):
        raise ValueError('invalid CAPTCHA tile labels')
    labels = re.split(r'[\s,]+', reply.strip())
    if len(set(labels)) != len(labels) or any(label not in tiles for label in labels):
        raise ValueError('answer must use current CAPTCHA tile labels')
    return {'kind': 'tiles', 'labels': labels}


def record_captcha_answer(state: TaskState, challenge_id: str, reply: str, *, revision: int) -> dict:
    """Store a scoped fact for Jev; no browser call or target resolution occurs."""
    challenge = state.challenges.get(challenge_id)
    key = f'captcha:{challenge_id}'
    if not challenge:
        raise ValueError('unknown CAPTCHA')
    if challenge.get('revision') != revision:
        if key in state.facts:
            state.facts[key]['valid'] = False
        raise ValueError('stale CAPTCHA; show the fresh challenge image in chat')
    oid = challenge.get('observation_id')
    if (oid != next(reversed(state.observations), None)
            or state.observations[oid]['revision'] != state.revision):
        if key in state.facts:
            state.facts[key]['valid'] = False
        raise ValueError('stale CAPTCHA observation; show the fresh image in chat')
    accepted = state.facts.get(key)
    if (accepted and accepted['valid'] and accepted.get('challenge_revision') == revision
            and accepted.get('observation_id') == oid):
        return deepcopy(accepted)
    answer = captcha_answer(challenge, reply, revision=revision)
    challenge['rounds'] += 1
    put_fact(state, key, answer, source='user', scope=challenge_id)
    state.facts[key].update(purpose='captcha', challenge_revision=revision, observation_id=oid)
    return deepcopy(state.facts[key])
