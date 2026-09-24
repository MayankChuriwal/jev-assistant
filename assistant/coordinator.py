"""Event-driven semantic assessment. No browser dispatch or authority to approve."""

import hashlib
import json
import math
import re
from copy import deepcopy
from decimal import Decimal, InvalidOperation

from . import model
from .profile import SECRETS
from .questions import COORDINATE, INTERPRET_MESSAGE, VISUAL_ANNOTATIONS
from .state import TaskState, public_context, put_fact, spend

ASSESSMENT_KEYS = {"objective", "facts", "requirements", "completion", "auth", "progress",
                   "proposal", "outcome", "notice", "recovery"}
EVENTS = {"start", "user_answer", "user_amendment", "authentication", "choice", "checkout",
          "completion", "missing_information", "stalled", "invalid_evidence", "confirmation",
          "outcome", "stage_change"}
SCHEMA = {
    "objective": "nonempty semantic string",
    "facts": [{"key": "string", "value": "public JSON", "source": "page|derived", "scope": "string",
               "revision": "current integer", "depends_on": ["valid fact key"],
               "evidence": ["current evidence ID"], "valid": True}],
    "requirements": [{"id": "string", "kind": "text|choice|secret|file|image", "purpose": "string",
                      "keys": ["fact key"], "scope": "string", "depends_on": ["requirement ID"],
                      "options": [{"id": "string", "label": "string", "description": "string",
                                   "evidence_ids": ["current evidence ID"]}], "reason": "string"}],
    "completion": [{"id": "string", "description": "string", "kind": "page|state|action|artifact",
                    "material_keys": ["fact key"], "evidence_ids": ["current evidence ID"],
                    "execution_id": "known ID or null", "artifact_id": "known ID or null",
                    "transfer_direction": "download|upload|null", "delivery_required": "boolean"}],
    "auth": {"flow_id": "string", "challenge_id": "known ID or null",
             "status": "unknown|required|challenge|submitted|authenticated|rejected|expired|unsupported",
             "origin": "observed origin", "purpose": "string", "field_role": "string",
             "evidence_ids": ["current evidence ID"], "execution_id": "known ID or null",
             "expires_at": "finite number or null", "allowed_origins": ["observed origin"]},
    "progress": {"summary": "string", "evidence_ids": ["current evidence ID"]},
    "proposal": {"task_id": "current ID", "revision": "current integer", "action": "semantic string",
                 "service": "string", "account": "string", "destination": "string", "items": ["string"],
                 "option": "string", "timing": "string", "payment": "string",
                 "currency": "string or null", "total_minor": "integer or null",
                 "total_kind": "exact|estimate|not_applicable", "later_terms": ["string"],
                 "lines": [{"id": "string", "label": "string", "amount_minor": "integer",
                            "currency": "string", "role": "item|subtotal|discount|fee|tax|tip|rounding|later_estimate",
                            "included_in": "line ID or null", "evidence_ids": ["current evidence ID"]}],
                 "evidence_ids": ["current evidence ID"]},
    "outcome": {"status": "pending_user|processing|success|failed|unknown",
                "evidence_ids": ["current evidence ID"], "artifact_id": "known ID or null"},
    "notice": {"kind": "string", "text": "string", "evidence_ids": ["current evidence ID"],
               "requirement_ids": ["known requirement ID"]},
    "recovery": {"objective": "semantic string", "reason": "string", "evidence_ids": ["current evidence ID"]},
}
# Only explicit timer wording is ignored; prices, quantities and other numbers remain significant.
_TIMER = re.compile(r"\b(?:resend|retry|expires?|remaining)(?:\s+in)?\s+\d+\s*(?:seconds?|secs?|s)\b", re.I)


def _redact(value):
    secrets = SECRETS.get()
    if secrets is None:
        return deepcopy(value)

    def clean(item):
        if isinstance(item, str):
            # Apply registered patterns even to keys or strings resembling data URLs.
            for raw in sorted(secrets.values, key=len, reverse=True):
                pattern, placeholder = secrets.values[raw]
                item = pattern.sub(lambda _match: placeholder, item)
            return item
        if type(item) in (int, float):
            # JSON may turn an OTP into a number, dropping leading zeroes or using exponent notation.
            for raw, (_, placeholder) in secrets.values.items():
                try:
                    if Decimal(str(item)) == Decimal(re.sub(r"[\s-]", "", raw)):
                        return placeholder
                except InvalidOperation:
                    continue
            text = str(item)
            redacted = clean(text)
            return redacted if redacted != text else item
        if isinstance(item, dict):
            return {clean(key): clean(child) for key, child in item.items()}
        if isinstance(item, (list, tuple)):
            return [clean(child) for child in item]
        return deepcopy(item)

    return clean(value)


def _key(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _runtime(state):
    if not hasattr(state, "_coordinator"):
        state._coordinator = {"cache": {}, "last": None, "issued": {}}
    return state._coordinator


def _observation(state, page):
    if not state.observations:
        raise ValueError("observation: observe before coordinating")
    record = next(reversed(state.observations.values()))
    if record["revision"] != state.revision:
        raise ValueError("observation: stale task revision")
    # Callers supply the observed page; never take arbitrary fields/values into prompts.
    if record["text"] != page.get("text") and _redact(page.get("text")) != record["text"]:
        raise ValueError("observation: page was not observed")
    return record


def _context(state):
    context = public_context(state)
    latest = next(reversed(state.observations), None)
    context["facts"] = {k: f for k, f in context["facts"].items() if f["valid"]}
    context["evidence"] = {k: e for k, e in context["evidence"].items()
                           if e["revision"] == state.revision and e["observation_id"] == latest}
    context["observation_ids"] = [latest] if latest else []
    context["execution_ids"] = list(state.executions)
    context["executions"] = {eid: {k: row.get(k) for k in (
        "execution_id", "operation", "status", "submission", "commitment", "challenge_id", "flow_id",
        "origin", "purpose", "field_role", "observation_id", "outcome")}
        for eid, row in state.executions.items()}
    # Operational diagnostics are not semantic task evidence and grow independently of the page.
    context.pop("diagnostics", None)
    return context


def _dialog(page):
    dialog = page.get("dialog")
    if not isinstance(dialog, dict):
        return None
    return _redact({k: dialog[k] for k in ("type", "message") if isinstance(dialog.get(k), str)})


def _signature(state, page, *, execution_sensitive=True):
    # Receipts invalidate an assessment but ordinary click/scroll receipts are not semantic events.
    observation = _observation(state, page)
    public = {k: v for k, v in observation.items() if k not in {"id", "screenshot_ref"}}
    public["text"] = _TIMER.sub("timer", public["text"])
    return _key({"observation": public, "dialog": _dialog(page), "revision": state.revision,
                 "constraints": state.constraints,
                 "facts": state.facts, "requirements": state.requirements, "questions": state.questions,
                 "auth": state.challenges, "proposal": state.proposal, "approval": state.approval,
                 "executions": state.executions if execution_sensitive else None})


def events_for(state: TaskState, page: dict, *, event: str | None = None) -> tuple[str, ...]:
    if event == "secret_reply":
        return ()
    if event is not None:
        if event not in EVENTS:
            raise ValueError("event: unsupported coordination event")
        return (event,)
    last = _runtime(state)["last"]
    if last is None:
        return ("start",)
    if last[0] != state.revision:
        return ("user_amendment",)
    return ("stage_change",) if last[1] != _signature(state, page, execution_sensitive=False) else ()


def _shape(value, keys, path):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError(f"{path}.keys: unknown or missing field")


def _string(value, path, *, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()) or len(value) > 6000:
        raise ValueError(f"{path}: expected bounded string")


def _list(value, path):
    if not isinstance(value, list) or len(value) > 100:
        raise ValueError(f"{path}: expected bounded list")


def _strings(value, path):
    _list(value, path)
    for item in value:
        _string(item, path)


def _enum(value, choices, path):
    if not isinstance(value, str) or value not in choices:
        raise ValueError(f"{path}: unsupported value")


def _reference(value, collection, path, *, nullable=False):
    if nullable and value is None:
        return
    _string(value, path)
    if value not in collection:
        raise ValueError(f"{path}: unknown or stale reference")


def _evidence(state, ids, path, *, required=True):
    _strings(ids, path)
    if required and not ids:
        raise ValueError(f"{path}: evidence required")
    latest = next(reversed(state.observations), None)
    for eid in ids:
        _reference(eid, state.evidence, path)
        proof = state.evidence[eid]
        if proof["revision"] != state.revision or proof["observation_id"] != latest:
            raise ValueError(f"{path}: stale evidence")


def _artifact_ids(state):
    from .commit import artifact_ids

    return artifact_ids(state)


def _public_json(value, path, depth=0):
    if depth > 10:
        raise ValueError(f"{path}: excessive nesting")
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if isinstance(value, list):
        _list(value, path)
        for item in value:
            _public_json(item, path, depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _string(key, path)
            if key.lower() in {"operation", "target", "selector", "coordinates", "javascript", "file_path", "approval"}:
                raise ValueError(f"{path}: executable fields are forbidden")
            _public_json(item, path, depth + 1)
        return
    raise ValueError(f"{path}: expected finite public JSON")


def _validate(state, output, *, event=None):
    _shape(output, ASSESSMENT_KEYS, "assessment")
    _string(output["objective"], "objective")
    for name in ("facts", "requirements", "completion"):
        _list(output[name], name)
    trial = deepcopy(state)
    for fact in output["facts"]:
        _shape(fact, SCHEMA["facts"][0], "facts")
        _public_json(fact["value"], "facts.value")
        _string(fact["key"], "facts.key")
        _string(fact["scope"], "facts.scope")
        _enum(fact["source"], {"page", "derived"}, "facts.source")
        if type(fact["revision"]) is not int or fact["revision"] != state.revision or fact["valid"] is not True:
            raise ValueError("facts.revision/valid: invalid")
        _strings(fact["depends_on"], "facts.depends_on")
        _evidence(state, fact["evidence"], "facts.evidence")
        # Coordinator never resolves or invents opaque secrets either.
        if any(word in fact["key"].lower() for word in ("secret", "password", "otp", "cvv", "card")):
            raise ValueError("facts.key: secret facts belong to the input owner")
        put_fact(trial, fact["key"], fact["value"], source=fact["source"], scope=fact["scope"],
                 depends_on=tuple(fact["depends_on"]), evidence=tuple(fact["evidence"]))
    requirement_ids = set(state.requirements)
    emitted_requirements = set()
    for req in output["requirements"]:
        _shape(req, SCHEMA["requirements"][0], "requirements")
        for name in ("id", "purpose", "scope", "reason"):
            _string(req[name], "requirements." + name)
        _enum(req["kind"], {"text", "choice", "secret", "file", "image"}, "requirements.kind")
        if req["id"] in emitted_requirements:
            raise ValueError("requirements.id: duplicate")
        emitted_requirements.add(req["id"])
        _strings(req["keys"], "requirements.keys")
        if len(req["keys"]) != len(set(req["keys"])):
            raise ValueError("requirements.keys: duplicate")
        if not req["keys"]:
            raise ValueError("requirements.keys: empty")
        _strings(req["depends_on"], "requirements.depends_on")
        for dep in req["depends_on"]:
            _reference(dep, requirement_ids, "requirements.depends_on")
        _list(req["options"], "requirements.options")
        if req["options"] and req["kind"] != "choice":
            raise ValueError("requirements.options: needs choice kind")
        option_ids = set()
        for option in req["options"]:
            _shape(option, {"id", "label", "description", "evidence_ids"}, "requirements.options")
            for name in ("id", "label", "description"):
                _string(option[name], "requirements.options." + name, empty=name == "description")
            if option["id"] in option_ids:
                raise ValueError("requirements.options.id: duplicate")
            option_ids.add(option["id"])
            _evidence(state, option["evidence_ids"], "requirements.options.evidence_ids")
        requirement_ids.add(req["id"])
    if output["completion"] and event not in {"start", "user_amendment"}:
        raise ValueError("completion: only creation or user amendment may set requirements")
    if output["completion"] and event == "start" and state.completion:
        raise ValueError("completion: already established")
    completion_ids = set()
    for req in output["completion"]:
        if not isinstance(req, dict):
            raise ValueError("completion: object required")
        keys = set(SCHEMA["completion"][0])
        # Non-artifact callers may omit inapplicable transfer fields.
        if req.get("kind") != "artifact":
            keys -= {k for k in ("transfer_direction", "delivery_required") if k not in req}
        _shape(req, keys, "completion")
        if req["kind"] == "artifact":
            _enum(req["transfer_direction"], {"download", "upload"}, "completion.transfer_direction")
            if type(req["delivery_required"]) is not bool:
                raise ValueError("completion.delivery_required: boolean required")
            if req["transfer_direction"] == "download" and not req["delivery_required"]:
                raise ValueError("completion: downloads require chat delivery")
        elif req.get("transfer_direction") is not None or req.get("delivery_required", False) is not False:
            raise ValueError("completion: only artifacts have transfer obligations")
        _string(req["id"], "completion.id")
        if req["id"] in completion_ids:
            raise ValueError("completion.id: duplicate")
        completion_ids.add(req["id"])
        _string(req["description"], "completion.description")
        _enum(req["kind"], {"page", "state", "action", "artifact"}, "completion.kind")
        _strings(req["material_keys"], "completion.material_keys")
        _evidence(state, req["evidence_ids"], "completion.evidence_ids", required=False)
        _reference(req["execution_id"], state.executions, "completion.execution_id", nullable=True)
        _reference(req["artifact_id"], _artifact_ids(state), "completion.artifact_id", nullable=True)
    for name in ("auth", "progress", "proposal", "outcome", "notice", "recovery"):
        value = output[name]
        if value is None:
            continue
        _shape(value, SCHEMA[name], name)
        _evidence(state, value["evidence_ids"], name + ".evidence_ids")
        if name == "auth":
            _validate_auth(state, value)
        elif name == "proposal":
            _validate_proposal(state, value)
        elif name == "outcome":
            _enum(value["status"], {"pending_user", "processing", "success", "failed", "unknown"}, "outcome.status")
            _reference(value["artifact_id"], _artifact_ids(state), "outcome.artifact_id", nullable=True)
        elif name == "notice":
            _string(value["kind"], "notice.kind")
            _string(value["text"], "notice.text")
            _strings(value["requirement_ids"], "notice.requirement_ids")
            for rid in value["requirement_ids"]:
                _reference(rid, requirement_ids, "notice.requirement_ids")
        elif name == "progress":
            _string(value["summary"], "progress.summary")
        else:
            _string(value["objective"], "recovery.objective")
            _string(value["reason"], "recovery.reason")
    return deepcopy(output)


def _validate_auth(state, value):
    # Validate with the owning transition gate without mutating during assessment.
    # The runtime applies the issued event only after its other gates succeed.
    from .interaction import observe_auth

    observe_auth(deepcopy(state), value)


def _validate_proposal(state, value):
    if value["task_id"] != state.task_id or type(value["revision"]) is not int or value["revision"] != state.revision:
        raise ValueError("proposal: wrong task/revision")
    for name in ("action", "service", "account", "destination", "option", "timing", "payment"):
        _string(value[name], "proposal." + name, empty=name not in {"action", "service"})
    _strings(value["items"], "proposal.items")
    _strings(value["later_terms"], "proposal.later_terms")
    _enum(value["total_kind"], {"exact", "estimate", "not_applicable"}, "proposal.total_kind")
    if value["total_kind"] == "not_applicable":
        if value["currency"] is not None or value["total_minor"] is not None or value["lines"]:
            raise ValueError("proposal: nonmonetary money fields must be null/empty")
    elif type(value["total_minor"]) is not int or value["total_minor"] < 0:
        raise ValueError("proposal.total_minor: expected nonnegative integer")
    else:
        _string(value["currency"], "proposal.currency")
    _list(value["lines"], "proposal.lines")
    ids = set()
    for line in value["lines"]:
        _shape(line, SCHEMA["proposal"]["lines"][0], "proposal.lines")
        for name in ("id", "label", "currency"):
            _string(line[name], "proposal.lines." + name)
        if line["id"] in ids or type(line["amount_minor"]) is not int or line["currency"] != value["currency"]:
            raise ValueError("proposal.lines: duplicate ID, invalid amount or currency")
        ids.add(line["id"])
        _enum(line["role"], {"item", "subtotal", "discount", "fee", "tax", "tip", "rounding", "later_estimate"},
              "proposal.lines.role")
        _evidence(state, line["evidence_ids"], "proposal.lines.evidence_ids")
    for line in value["lines"]:
        _reference(line["included_in"], ids - {line["id"]}, "proposal.lines.included_in", nullable=True)
    # Assessment validates arithmetic, but never grants approval. The staged jobs.review_proposal
    # also binds the extracted amount strings to current rows before Task 11/12 presents a review.
    if value["total_kind"] != "not_applicable":
        from .commit import review_total

        result = review_total(value["lines"], value["total_minor"], currency=value["currency"])
        if not result["valid"]:
            raise ValueError("proposal.breakdown: " + "; ".join(result["reasons"]))


def assess(state: TaskState, page: dict, event: str, *, client=model.text_json) -> dict:
    if event not in EVENTS:
        raise ValueError("event: unsupported coordination event")
    signature = _signature(state, page)
    runtime = _runtime(state)
    key = (state.revision, signature, event)
    if key in runtime["cache"]:
        cached = runtime["cache"][key]
        try:
            _validate(state, cached, event=event)
        except ValueError:
            pass
        else:
            runtime["issued"] = {_key(cached): (state.revision, signature, event,
                                                next(reversed(state.observations)))}
            runtime["dialog"] = _dialog(page)
            runtime["last"] = (state.revision, _signature(state, page, execution_sensitive=False))
            return deepcopy(cached)
    payload = _redact({"task": _context(state), "event": event,
                       "untrusted_page": {**_observation(state, page), "dialog": _dialog(page)}, "schema": SCHEMA,
                       "nullable": ["auth", "progress", "proposal", "outcome", "notice", "recovery"]})
    for attempt in range(2):
        spend(state, "coordinator")
        try:
            output, metadata = client(COORDINATE, payload, max_tokens=4096)
        except model.TextJSONError as exc:
            output, metadata = None, exc.metadata
        except Exception:
            state.diagnostics.append({"kind": "coordinator_transport", "reason": "model call failed"})
            raise
        try:
            validated = _validate(state, _redact(output), event=event)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            # Only validator-generated paths are retained. Never retain arbitrary exception values.
            reason = str(exc) if isinstance(exc, ValueError) else "assessment: invalid nested type"
            reason = _redact(reason)
            state.diagnostics.append({"kind": "coordinator_schema", "reason": reason,
                                      "metadata": _redact(metadata)})
            if attempt:
                raise ValueError(reason) from None
            payload = {**payload, "rejected_response": _redact(output), "validation_error": reason}
            continue
        runtime["cache"][key] = deepcopy(validated)
        # Bound cache growth; no raw failed output is retained in task state.
        if len(runtime["cache"]) > 16:
            del runtime["cache"][next(iter(runtime["cache"]))]
        runtime["issued"] = {_key(validated): (state.revision, signature, event,
                                                next(reversed(state.observations)))}
        runtime["dialog"] = _dialog(page)
        runtime["last"] = (state.revision, _signature(state, page, execution_sensitive=False))
        state.diagnostics.append({"kind": "coordinator_assessment", "event": event, "metadata": _redact(metadata)})
        return validated
    raise AssertionError("unreachable")


def apply_assessment(state: TaskState, assessment: dict) -> None:
    assessment = _redact(assessment)
    _shape(assessment, ASSESSMENT_KEYS, "assessment")
    runtime = _runtime(state)
    issued = runtime["issued"].get(_key(assessment))
    event = None
    if (issued and issued[0] == state.revision
            and issued[3] == next(reversed(state.observations), None)):
        latest = next(reversed(state.observations.values()))
        if issued[1] == _signature(state, {**latest, "dialog": runtime.get("dialog")}):
            event = issued[2]
    output = _validate(state, assessment, event=event)
    if event is None:
        raise ValueError("assessment.issuance: missing or stale context; assess the current state first")
    # Validate everything before writing. Owning modules consume requirements/auth/proposal/outcome later.
    for fact in output["facts"]:
        put_fact(state, fact["key"], fact["value"], source=fact["source"], scope=fact["scope"],
                 depends_on=tuple(fact["depends_on"]), evidence=tuple(fact["evidence"]))
    state.objective = output["objective"]
    if output["completion"]:
        state.completion = deepcopy(output["completion"])
    if output["progress"]:
        state.progress["assessment"] = deepcopy(output["progress"])
    if issued and event is not None:
        latest = next(reversed(state.observations.values()))
        runtime["last"] = (state.revision, _signature(
            state, {**latest, "dialog": runtime.get("dialog")}, execution_sensitive=False))


def interpret_message(text: str, tasks: list[dict], chat: list[dict], *, client=model.text_json) -> dict:
    output, _ = client(INTERPRET_MESSAGE, _redact({"message": text, "tasks": tasks, "chat": chat[-6:]}),
                       max_tokens=1024)
    _shape(output, {"intent", "task_id", "ambiguous", "reply"}, "message")
    _enum(output["intent"], {"ANSWER", "AMEND", "NEW_TASK", "CANCEL", "STATUS", "OTHER", "RESUME"}, "message.intent")
    _string(output["reply"], "message.reply", empty=True)
    if type(output["ambiguous"]) is not bool:
        raise ValueError("message.ambiguous: expected boolean")
    candidates = {task["id"] for task in tasks}
    target = output["task_id"]
    if target is not None and (not isinstance(target, str) or target not in candidates):
        return {"intent": "OTHER", "task_id": None, "ambiguous": True,
                "reply": "Which task does your message refer to?"}
    if output["intent"] in {"ANSWER", "AMEND", "CANCEL", "RESUME"} and target is None:
        return {"intent": "OTHER", "task_id": None, "ambiguous": True,
                "reply": "Which task does your message refer to?"}
    return deepcopy(output)


def annotate_visual(state: TaskState, page: dict, image: str, cells: dict, *, client=model.text_json) -> list[dict]:
    if page.get("captcha") or any(c.get("kind") in {"text", "tiles", "unsupported"}
                                  and (c.get("purpose") == "captcha" or "tiles" in c or "rounds" in c)
                                  for c in state.challenges.values()):
        raise ValueError("visual: CAPTCHA requires a human answer")
    output, _ = client(VISUAL_ANNOTATIONS, _redact({"task": _context(state),
                      "untrusted_page": _observation(state, page), "cells": sorted(cells)}),
                      images=[image], max_tokens=2048)
    _shape(output, {"annotations"}, "visual")
    _list(output["annotations"], "visual.annotations")
    for item in output["annotations"]:
        _shape(item, {"cell", "label", "text"}, "visual.annotation")
        _reference(item["cell"], cells, "visual.cell")
        for name in ("label", "text"):
            _string(item[name], "visual." + name, empty=True)
            if len(item[name]) > 160:
                raise ValueError("visual: annotation exceeds 160 characters")
    return deepcopy(output["annotations"])
