"""TypeSafe makes choices; an optional small OpenAI-compatible model writes field values."""

import json
import math
import os
import time
from urllib.parse import urlparse

import httpx

from .profile import CARD_PARTS, SECRETS
from .questions import FIELD_VALUE, HEAD_PREMISE, KEY_HELP, KEY_RULE, NEXT_ACTION, OPERATIONS, TARGET, URL_VALUE

CLIENT = httpx.Client(http2=True, timeout=25)

# Observed kind -> the target question it feeds and the operations that consume that question.
HEADS = {
    "click": ("click_target", ("CLICK", "DOUBLE_CLICK", "RIGHT_CLICK", "HOVER")),
    "fill": ("type_text_target", ("TYPE_TEXT",)),
    "key": ("press_key_target", ("PRESS_KEY",)),
    "select": ("select_target", ("SELECT",)),
    "upload": ("upload_target", ("UPLOAD",)),
    "drag": ("drag_source", ("DRAG",)),
    "scroll_up": ("scroll_up_target", ("SCROLL_UP",)),
    "scroll_down": ("scroll_down_target", ("SCROLL_DOWN",)),
    "scroll_left": ("scroll_left_target", ("SCROLL_LEFT",)),
    "scroll_right": ("scroll_right_target", ("SCROLL_RIGHT",)),
    "tab": ("tab_target", ("SWITCH_TAB", "CLOSE_TAB")),
}
HEAD_OPERATIONS = dict(HEADS.values())
OPERATION_HEAD = {op: head for head, ops in HEAD_OPERATIONS.items() for op in ops}
CONTROLS = ("wait", "go_back", "go_forward", "reload", "open_url", "save_pdf", "accept_dialog", "dismiss_dialog")
# A drag can drop onto anything clickable or any scrollable panel.
DROPS = ("click_target", "scroll_up_target", "scroll_down_target", "scroll_left_target", "scroll_right_target")


def post_json(url, key, body):
    secrets = SECRETS.get()
    if secrets is not None:
        body = secrets.redact(body)  # the one outbound path to both models
    for attempt in range(3):
        try:
            response = CLIENT.post(url, json=body, headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError:
            raise RuntimeError("Model connection failed; no action executed.") from None
        if response.status_code in {429, 529, 503} and attempt < 2:
            time.sleep(0.5 * 2**attempt)
            continue
        if response.is_error:
            raise RuntimeError(f"Model provider returned HTTP {response.status_code}; no action executed.")
        return response.json()
    raise RuntimeError("Model unavailable")


def validate_choice(answer, ids):
    try:
        probabilities = answer["probabilities"]
        numbers = [*probabilities.values(), answer["confidence"]]
        valid = (
            answer["choice"] in ids
            and set(probabilities) == set(ids)
            and all(type(n) in (int, float) and math.isfinite(n) and 0 <= n <= 1 for n in numbers)
            and abs(sum(probabilities.values()) - 1) < 0.02
            and probabilities[answer["choice"]] >= max(probabilities.values()) - 1e-6
        )
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("Invalid TypeSafe response; no action executed.")
    return answer


def action_space(actions):
    """One index per observed element; each target question lists only compatible targets."""
    elements, indices, heads, controls = [], {}, {}, {}
    for action in actions:
        kind = action["kind"]
        if kind in CONTROLS:
            controls[kind.upper()] = action
            continue
        if kind not in HEADS:
            continue
        head, operations = HEADS[kind]
        index = action["id"]  # page-level targets (page scroll, page key, tabs) keep their ids
        if action.get("node") is not None:
            key = (action.get("frame"), action["node"])
            if key not in indices:
                indices[key] = str(len(elements) + 1)
                keys = ("role", "value", "checked", "selected", "expanded", "popup")
                element = {k: action[k] for k in keys if k in action}
                element.update(index=indices[key], label=action["label"].split(" → ")[0], operations=[])
                if action.get("frame"):
                    element["in_frame"] = True
                elements.append(element)
            index = indices[key]
            element = elements[int(index) - 1]
            keyed = operations + (("PRESS_KEY",) if kind == "fill" or action.get("role") == "slider" else ())
            element["operations"] += [op for op in keyed if op not in element["operations"]]
            if kind == "select":
                element["value"] = action.get("current_value", "")
                options = element.setdefault("options", [])
                index = f"{index}:{len(options) + 1}"
                options.append({"index": index, "label": action["label"], "value": action["value"]})
        heads.setdefault(head, {})[index] = action
        if kind == "fill" or action.get("role") == "slider":
            heads.setdefault("press_key_target", {})[index] = action
    drops = {i: a for h in DROPS for i, a in heads.get(h, {}).items() if a.get("node") is not None}
    if "drag_source" in heads:
        if drops:
            heads["drag_target"] = drops
        else:
            del heads["drag_source"]
    return elements, heads, controls


def criteria(candidates):
    return {
        index: {
            "element": f"[{index}] {a['label']}" if a.get("node") is not None else a["label"],
            "current_value": a.get("current_value", a.get("value", "")),
            **{k: a[k] for k in ("role", "checked", "selected", "expanded", "popup", "current") if k in a},
        }
        for index, a in candidates.items()
    }


def choose(state, goal, history):
    elements, heads, controls = action_space(state["actions"])
    operations = {op: OPERATIONS[op] for head in heads for op in HEAD_OPERATIONS.get(head, ())}
    operations.update({op: OPERATIONS[op] for op in controls})
    operations.update({op: OPERATIONS[op] for op in ("ASK_USER", "CONFIRM", "NOTE", "DONE", "BLOCKED")})
    questions = {
        "operation": {"type": "choice", "criteria": operations, "instructions": {"goal": goal, "rules": NEXT_ACTION}}
    }
    for head, candidates in heads.items():
        questions[head] = {
            "type": "choice",
            "criteria": criteria(candidates),
            "instructions": {"goal": goal, "operation": HEAD_PREMISE[head], "rules": [NEXT_ACTION, TARGET]},
        }
    if "press_key_target" in heads:
        questions["press_key_key"] = {
            "type": "choice",
            "criteria": KEY_HELP,
            "instructions": {"goal": goal, "operation": "PRESS_KEY", "rules": [NEXT_ACTION, KEY_RULE]},
        }
    body = {
        "model": os.environ.get("TYPESAFE_MODEL", "jev-latest"),
        "state": {
            "page": {k: state[k] for k in ("url", "title", "text")},
            "elements": elements,
            "recent_actions": [
                {k: h.get(k) for k in ("operation", "action", "text", "page_changed")} for h in history[-10:]
            ],
        },
        "questions": questions,
    }
    started = time.perf_counter()
    result = post_json("https://api.typesafe.ai/v1/systemone", os.environ["TYPESAFE_API_KEY"], body)
    answers = result["answers"]
    operation_answer = validate_choice(answers.get("operation", {}), operations)
    operation = operation_answer["choice"]
    decision = {"operation": operation, "target": None, "key": None, "drop": None,
                "target_probabilities": {}, "target_confidence": None}
    head = OPERATION_HEAD.get(operation)
    if head:
        # Unused target questions cannot cause an action. Validate the one the operation selects.
        target_answer = validate_choice(answers.get(head, {}), heads[head])
        action = heads[head][target_answer["choice"]]
        decision.update(
            target=target_answer["choice"],
            choice=action["id"],
            probabilities={a["id"]: target_answer["probabilities"][i] for i, a in heads[head].items()},
            target_probabilities=target_answer["probabilities"],
            target_confidence=target_answer["confidence"],
        )
        if operation == "DRAG":
            drop = validate_choice(answers.get("drag_target", {}), heads["drag_target"])["choice"]
            if heads["drag_target"][drop] is action or heads["drag_target"][drop].get("node") == action.get("node"):
                raise ValueError("Invalid TypeSafe response; no action executed.")
            decision["drop"] = heads["drag_target"][drop]["id"]
        if operation == "PRESS_KEY":
            decision["key"] = validate_choice(answers.get("press_key_key", {}), KEY_HELP)["choice"]
    else:
        choice = controls[operation]["id"] if operation in controls else operation
        decision.update(choice=choice, probabilities={choice: operation_answer["probabilities"][operation]})
    decision.update(
        confidence=operation_answer["confidence"],
        operation_probabilities=operation_answer["probabilities"],
        raw_answers=answers,
        model=result["model"],
        usage=result.get("usage", {}),
        latency_ms=round((time.perf_counter() - started) * 1000),
        request=body,
    )
    return decision

def text_json(system, payload, *, max_tokens=512, images=()):
    """One JSON object from the OpenAI-compatible text model, plus call metadata."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise ValueError("The text model needs OPENAI_API_KEY; nothing is guessed.")
    base = os.environ.get("TEXT_MODEL_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    name = os.environ.get("TEXT_MODEL", "gpt-4.1-mini")
    user = json.dumps(payload)
    if images:
        user = [{"type": "text", "text": user}] + [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image}"}} for image in images]
    started = time.perf_counter()
    result = post_json(
        base + "/chat/completions",
        key,
        {
            "model": name,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        },
    )
    try:
        output = json.loads(result["choices"][0]["message"]["content"])
    except (ValueError, KeyError, TypeError, IndexError):
        output = None
    if not isinstance(output, dict):
        raise ValueError("Text model returned no JSON object; nothing typed or executed.")
    return output, {
        "model": name,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "usage": result.get("usage", {}),
    }


def field_context(context, action, page, history, profile, allowed, previous=None):
    field = {k: action.get(k) for k in ("label", "role", "value", "constraints")}
    if action.get("group"):
        field["code_length"] = len(action["group"])
    return {
        "goal": context,
        "field": field,
        "page": {"title": page["title"], "text": page["text"][:6000]},
        "recent_actions": [{k: h.get(k) for k in ("action", "text")} for h in history[-6:]],
        "profile": profile,
        "allowed": allowed,
        "previous": previous,
    }


SAVE_AS = {f"card.{part}" for part in CARD_PARTS}


def field_value(context):
    """What goes into a field: ("text", value), ("profile", key), ("secret", key),
    or ("ask", question, secret, save_as)."""
    out, meta = text_json(FIELD_VALUE, context, max_tokens=1024)
    keys = set(out)
    if keys == {"text"} and isinstance(out["text"], str) and out["text"].strip() and len(out["text"]) <= 2000:
        return ("text", out["text"]), meta
    if keys == {"profile"} and out["profile"] in context.get("profile", {}).get("profile_keys", []):
        return ("profile", out["profile"]), meta
    if keys == {"secret"} and out["secret"] in context.get("allowed", []):
        return ("secret", out["secret"]), meta
    if (keys in ({"ask", "secret"}, {"ask", "secret", "save_as"}) and isinstance(out["ask"], str)
            and out["ask"].strip() and isinstance(out["secret"], bool)
            and out.get("save_as", "card.number") in SAVE_AS):
        return ("ask", out["ask"].strip(), out["secret"], out.get("save_as")), meta
    raise ValueError("Text helper returned no valid field value; nothing typed.")


def url_value(goal, page, history):
    output, meta = text_json(
        URL_VALUE,
        {
            "goal": goal,
            "page": {"url": page["url"], "title": page["title"], "text": page["text"][:3000]},
            "recent_actions": [{k: h.get(k) for k in ("action", "text")} for h in history[-6:]],
        },
    )
    url = output.get("url")
    parsed = urlparse(url) if isinstance(url, str) else None
    if set(output) != {"url"} or not parsed or parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Text model returned no valid http(s) URL; nothing opened.")
    return url, meta
