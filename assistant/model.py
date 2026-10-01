"""The two model transports (TypeSafe Jev and the OpenAI-compatible text model), the per-task spend meter, and a
page's action space: one index per observed element, compatible targets per operation."""

import contextvars
import json
import math
import os
import time

import httpx

from .profile import SECRETS

CLIENT = httpx.Client(http2=True, timeout=25)
TYPESAFE = "https://api.typesafe.ai/"
METER = contextvars.ContextVar("jev_meter", default=None)  # the running task's model spend
LEAK = "Refused to send: a secret survived redaction."
JEV_INPUT = 0.042e-6  # dollars per Jev input token; Jev's output is free
MINI = {"input": 0.40e-6, "cached": 0.10e-6, "output": 1.60e-6}  # GPT-4.1 mini, dollars per token


def new_meter():
    return {"mini": 0.0, "jev": 0.0, "mini_calls": 0, "jev_calls": 0}


def spent(meter):
    return meter["mini"] + meter["jev"]


def charge(body, usage, jev):
    """Add one call to the running task's meter. A Jev reply without usage is estimated from the request size."""
    meter = METER.get()
    if meter is None:
        return
    usage = usage or {}
    if jev:
        tokens = usage.get("input_tokens") or usage.get("prompt_tokens") or len(json.dumps(body)) // 4
        meter["jev"] += tokens * JEV_INPUT
        meter["jev_calls"] += 1
        return
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0  # a null count is none
    meter["mini"] += (((usage.get("prompt_tokens") or 0) - cached) * MINI["input"] + cached * MINI["cached"]
                      + (usage.get("completion_tokens") or 0) * MINI["output"])
    meter["mini_calls"] += 1


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
CONTROLS = ("wait", "go_back", "go_forward", "reload", "open_url", "save_pdf", "accept_dialog", "dismiss_dialog",
            "look")

# A drag can drop onto anything clickable or any scrollable panel.
DROPS = ("click_target", "scroll_up_target", "scroll_down_target", "scroll_left_target", "scroll_right_target")


def post_json(url, key, body):
    secrets = SECRETS.get()
    if secrets is not None:
        body = secrets.redact(body)  # the one outbound path to both models
        if secrets.leaks(body):
            raise RuntimeError(LEAK)  # never sent: a redaction gap must not reach a model
    for attempt in range(3):
        try:
            response = CLIENT.post(url, json=body, headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError:
            if attempt < 2:  # a dropped connection is tried again before it counts as a problem
                time.sleep(0.5 * 2**attempt)
                continue
            raise RuntimeError("Model connection failed; no action executed.") from None
        if response.status_code in {429, 529, 503} and attempt < 2:
            time.sleep(0.5 * 2**attempt)
            continue
        if response.is_error:
            raise RuntimeError(f"Model provider returned HTTP {response.status_code}; no action executed.")
        result = response.json()
        charge(body, result.get("usage") if isinstance(result, dict) else None, url.startswith(TYPESAFE))
        return result
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
    if "LOOK" in controls:  # a free spot on a canvas or a map, found by sight
        drops["look"] = {**controls["LOOK"], "label": "A free spot on the screen, found by looking"}
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


def text_json(system, payload, *, max_tokens=512, images=(), schema=None, schema_name="reply"):
    """One JSON object from the OpenAI-compatible text model, plus call metadata. With a schema the reply is
    held to it (strict json_schema); a reply cut off or refused is an error, never a partial step."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise ValueError("The text model needs OPENAI_API_KEY; nothing is guessed.")
    base = os.environ.get("TEXT_MODEL_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    name = os.environ.get("TEXT_MODEL", "gpt-4.1-mini")
    secrets = SECRETS.get()
    # Redact the values themselves: JSON escaping (non-ASCII, quotes) would hide them from a string-level pass.
    user = json.dumps(secrets.redact(payload) if secrets else payload, ensure_ascii=False)
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
            "response_format": ({"type": "json_schema",
                                 "json_schema": {"name": schema_name, "strict": True, "schema": schema}}
                                if schema else {"type": "json_object"}),
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        },
    )
    choice = (result.get("choices") or [{}])[0]
    if choice.get("finish_reason") == "length" or (choice.get("message") or {}).get("refusal"):
        raise ValueError("The text model's reply was cut off or refused; nothing executed.")
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
