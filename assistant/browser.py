"""Agent Chrome through Browser Harness: observation, execution, tabs, frames, and browser events."""

import hashlib
import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from browser_harness.admin import ensure_daemon
from browser_harness.helpers import cdp, drain_events

HOME = Path(os.environ.get("JEV_HOME") or Path.home() / ".jev")
INCOMING = HOME / "downloads" / ".incoming"
# Atomically read visible content and controls, preserving actual DOM node identity.
READ_STATE = Path(__file__).with_name("snapshot.js").read_text()
MARKER = f"(() => {{ const state={READ_STATE}; return state?.marker ?? null; }})()"
VIEWPORT = {"width": 1120, "height": 780}
DATE_TYPES = {"date", "time", "datetime-local", "month", "week"}
KEYS = {  # name: (key, code, keyCode, text, modifiers)
    "Enter": ("Enter", "Enter", 13, "\r", 0),
    "Escape": ("Escape", "Escape", 27, None, 0),
    "Tab": ("Tab", "Tab", 9, None, 0),
    "Shift+Tab": ("Tab", "Tab", 9, None, 8),
    "ArrowUp": ("ArrowUp", "ArrowUp", 38, None, 0),
    "ArrowDown": ("ArrowDown", "ArrowDown", 40, None, 0),
    "ArrowLeft": ("ArrowLeft", "ArrowLeft", 37, None, 0),
    "ArrowRight": ("ArrowRight", "ArrowRight", 39, None, 0),
    "PageUp": ("PageUp", "PageUp", 33, None, 0),
    "PageDown": ("PageDown", "PageDown", 34, None, 0),
    "Home": ("Home", "Home", 36, None, 0),
    "End": ("End", "End", 35, None, 0),
    "Space": (" ", "Space", 32, " ", 0),
    "Backspace": ("Backspace", "Backspace", 8, None, 0),
}

GUARD = "(node => { const c = window.__jevFast; return c ? [c.pageKey(), c.guard(c.nodes.get(node))] : null; })"
LOCATE = "(node => window.__jevFast?.locate(node) ?? null)"
# Runs on the <iframe> element in its owner document: map a point inside the frame to the owner's viewport.
HIT_OWNER = r"""function (x, y) {
  const r = this.getBoundingClientRect();
  return window.__jevFast?.climb(this, r.x + this.clientLeft + x, r.y + this.clientTop + y) ?? null;
}"""
SELECT = r"""(({node, value}) => {
  const c = window.__jevFast, e = c?.nodes.get(node);
  if (!e || e.tagName !== 'SELECT' || !c.locate(node)) return null;
  if (![...e.options].some(o => o.value === value && !o.disabled && !o.closest('optgroup[disabled]'))) return null;
  e.value = value;
  e.dispatchEvent(new Event('input', {bubbles: true}));
  e.dispatchEvent(new Event('change', {bubbles: true}));
  return e.value;
})"""
SCROLL = r"""(({node, x, y}) => {
  const e = node === null ? null : window.__jevFast?.nodes.get(node);
  if (node !== null && !e?.isConnected) return false;
  const w = e ? e.clientWidth : innerWidth, h = e ? e.clientHeight : innerHeight;
  (e || window).scrollBy(x * w * 0.8, y * h * 0.8);
  return true;
})"""
# Native date/time inputs ignore inserted text; set the value through the element's own setter.
SET_VALUE = r"""(({node, value}) => {
  const e = window.__jevFast?.nodes.get(node);
  if (!e?.isConnected) return false;
  Object.getOwnPropertyDescriptor(Object.getPrototypeOf(e), 'value').set.call(e, value);
  e.dispatchEvent(new Event('input', {bubbles: true}));
  e.dispatchEvent(new Event('change', {bubbles: true}));
  return e.value === value;
})"""
FOCUS = "(node => { const e = window.__jevFast?.nodes.get(node); e?.focus(); return !!e && e.matches(':focus'); })"
# After input: wait two frames (50 ms cap), or up to 200 ms for a combobox's visible suggestions.
SETTLE = r"""(action => new Promise(resolve => {
  const field = action.node === null ? null : window.__jevFast?.nodes.get(action.node);
  const autocomplete = action.kind === 'fill' && field?.getAttribute('role') === 'combobox';
  let frames = 0, stopped = false;
  const finish = () => { stopped = true; resolve(); };
  setTimeout(finish, autocomplete ? 200 : 50);
  const ready = () => {
    if (stopped) return;
    const ids = (field?.getAttribute('aria-controls') || field?.getAttribute('aria-owns') || '')
      .split(/\s+/).filter(Boolean);
    const roots = ids.length ? ids.map(id => document.getElementById(id)).filter(Boolean) : [document];
    const options = roots.flatMap(root => [...root.querySelectorAll('[role="option"]')]);
    if (++frames >= 2 && (!autocomplete || options.some(e => {
      const r = e.getBoundingClientRect();
      return r.width && r.height && r.bottom > 0 && r.top < innerHeight &&
        e.checkVisibility({checkOpacity: true, checkVisibilityCSS: true});
    }))) finish();
    else requestAnimationFrame(ready);
  };
  requestAnimationFrame(ready);
}))"""


class StalePage(ValueError):
    """A decision no longer refers to the observed page."""


class EventPump:
    """Drains Browser Harness's one shared event buffer and routes each event by CDP session id."""

    def __init__(self):
        self.handlers = {}
        self.lock = threading.RLock()
        self.thread = None

    def register(self, session, handler):
        with self.lock:
            self.handlers[session] = handler
            if self.thread is None:
                self.thread = threading.Thread(target=self.run, name="jev-events", daemon=True)
                self.thread.start()

    def unregister(self, session):
        with self.lock:
            self.handlers.pop(session, None)

    def flush(self):
        with self.lock:
            try:
                events = drain_events()
            except (RuntimeError, OSError, TimeoutError):
                return  # the daemon is restarting; the next flush catches up
            for event in events:
                handler = self.handlers.get(event.get("session_id"))
                if handler is None:
                    continue
                try:
                    handler(event)
                except Exception as error:  # one bad event must not stop routing for every task
                    print(f"jev: {event.get('method')} handler failed: {error}", file=sys.stderr)

    def run(self):
        while True:
            self.flush()
            time.sleep(0.05)


PUMP = EventPump()


class Browser:
    """One task's tabs in the agent Chrome. Every executed target resolves from an observed node."""

    def __init__(self, url, *, downloads=None, geolocation=None):
        ensure_daemon()
        self.tabs, self.opener, self.frames, self.dialogs, self.titles = {}, {}, {}, {}, {}
        self.alerts, self.downloads, self.pending_downloads = [], [], {}
        self.chooser = self.intercepted = self.after_input = self.active = None
        self.downloads_dir = Path(downloads) if downloads else HOME / "downloads"
        self.geolocation = geolocation
        INCOMING.mkdir(parents=True, exist_ok=True)
        # Browser-wide: every download lands in one folder and is routed by session when it completes.
        cdp("Browser.setDownloadBehavior", behavior="allowAndName", downloadPath=str(INCOMING), eventsEnabled=True)
        for permission, setting in (("notifications", "denied"), ("camera", "denied"), ("microphone", "denied"),
                                    ("geolocation", "granted" if geolocation else "denied")):
            try:
                cdp("Browser.setPermission", permission={"name": permission}, setting=setting)
            except RuntimeError:
                pass  # ponytail: an unsupported permission keeps Chrome's prompt, which cannot block CDP
        self.active = self._adopt(cdp("Target.createTarget", url="about:blank", background=True)["targetId"])
        try:
            self.navigate(url)
        except Exception:
            self.close()
            raise

    @property
    def session(self):
        return self.tabs[self.active]

    def call(self, method, session=None, *, timeout=5.0, **params):
        return cdp(method, session_id=session or self.session, _response_timeout=timeout, **params)

    def evaluate(self, expression, session=None):
        response = self.call("Runtime.evaluate", session, expression=expression, returnByValue=True, awaitPromise=True)
        if response.get("exceptionDetails"):
            raise StalePage("Document changed during evaluation")
        return response.get("result", {}).get("value")

    def input(self, method, **params):
        """Trusted input on the active tab. A modal dialog blocks the reply: report it, never hang or retry."""
        if self.dialogs.get(self.session):
            return None
        try:
            return self.call(method, timeout=2.0, **params)
        except TimeoutError:
            PUMP.flush()
            if self.dialogs.get(self.session) or self._tab_closed():
                return None
            raise RuntimeError("Browser input did not return; inspect before retrying.") from None
        except RuntimeError:
            if self._tab_closed():
                return None  # the input closed its own tab (window.close); the next observation follows the opener
            raise

    def _tab_closed(self):
        return self.active not in {t["targetId"] for t in cdp("Target.getTargets")["targetInfos"]}

    def _session_for(self, frame):
        if not frame:
            return self.session
        if frame not in self.frames:
            raise StalePage("Frame is gone. Observe again.")
        return self.frames[frame]["session"]

    def _adopt(self, target, opener=None):
        session = cdp("Target.attachToTarget", targetId=target, flatten=True)["sessionId"]
        self.tabs[target] = session
        if opener:
            self.opener[target] = opener
        self.call("Page.enable", session)
        self.call("DOM.enable", session)  # DOM.resolveNode needs it; no events flow until a document is requested
        self.call("Page.setInterceptFileChooserDialog", session, enabled=True)
        self.call("Emulation.setDeviceMetricsOverride", session, deviceScaleFactor=1, mobile=False, **VIEWPORT)
        # Keep rAF/menus rendering in a background tab without activating it.
        self.call("Emulation.setFocusEmulationEnabled", session, enabled=True)
        if self.geolocation:
            self.call("Emulation.setGeolocationOverride", session, accuracy=50, **self.geolocation)
        PUMP.register(session, self._on_event)
        return target

    def _forget(self, target):
        session = self.tabs.pop(target, None)
        if session:
            PUMP.unregister(session)
            self.dialogs.pop(session, None)
        for fid in [f for f, info in self.frames.items() if info["tab"] == target]:
            PUMP.unregister(self.frames.pop(fid)["session"])
        if target == self.active:
            opener = self.opener.get(target)
            self.active = opener if opener in self.tabs else next(reversed(self.tabs), None)

    def _on_event(self, event):
        """Runs on the pump thread."""
        method, params, session = event["method"], event.get("params", {}), event.get("session_id")
        if method == "Page.javascriptDialogOpening":
            if params["type"] == "alert":
                self.alerts.append(params["message"])
                cdp("Page.handleJavaScriptDialog", session_id=session, accept=True)
            else:
                self.dialogs[session] = {"type": params["type"], "message": params["message"]}
        elif method == "Page.javascriptDialogClosed":
            self.dialogs.pop(session, None)
        elif method == "Page.fileChooserOpened":
            self.chooser = {"session": session, "node": params.get("backendNodeId"),
                            "multiple": params.get("mode") == "selectMultiple"}
        elif method == "Input.dragIntercepted":
            self.intercepted = params["data"]
        elif method == "Browser.downloadWillBegin":
            self.pending_downloads[params["guid"]] = params["suggestedFilename"]
        elif method == "Browser.downloadProgress" and params.get("state") == "completed":
            name = self.pending_downloads.pop(params["guid"], None)
            if name:
                self.downloads.append(self._store_download(params["guid"], name))

    def _store_download(self, guid, name):
        source = INCOMING / guid
        for _ in range(50):  # the completed event can precede the file's final rename by a few ms
            if source.exists():
                break
            time.sleep(0.02)
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        target = unique(self.downloads_dir / safe_name(name))
        source.rename(target)
        return str(target)

    def navigate(self, url):
        previous = self._time_origin()
        self.call("Page.navigate", url=url, timeout=30.0)
        self._wait_loaded(previous)

    def _time_origin(self):
        try:
            return self.evaluate("performance.timeOrigin")
        except (StalePage, RuntimeError, TimeoutError):
            return None

    def _wait_loaded(self, previous=None, timeout=15.0):
        """Wait for a new, complete document. A slow page is observed as it is once the deadline passes."""
        # ponytail: a same-document (hash) navigation waits the full deadline; add a URL check if that matters.
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                origin, state = self.evaluate("[performance.timeOrigin, document.readyState]")
                if origin != previous and state == "complete":
                    return
            except (StalePage, RuntimeError, TimeoutError, TypeError, ValueError):
                pass
            time.sleep(0.05)

    def _sync(self):
        PUMP.flush()

    def _extras(self):
        return []

    def _settle(self):
        action, self.after_input = self.after_input, None
        if action is None:
            return
        arg = {"kind": action["kind"], "node": None if action.get("frame") else action.get("node")}
        try:
            self.call("Runtime.evaluate", expression=f"({SETTLE})({json.dumps(arg)})", awaitPromise=True,
                      returnByValue=True)
        except (RuntimeError, TimeoutError):
            pass  # read-only wait after an already-logged action; a navigation may interrupt it

    def _read(self):
        main = self.evaluate(READ_STATE)
        if main is None:
            raise StalePage("Document is navigating")
        return merge([(None, main)])

    def _read_settled(self):
        for attempt in range(10):
            try:
                return self._read()
            except StalePage:
                if attempt == 9:
                    raise
                time.sleep(0.02)

    def _dialog_page(self, dialog):
        info = cdp("Target.getTargetInfo", targetId=self.active)["targetInfo"]
        return {
            "url": info["url"], "title": info["title"], "scroll": {}, "frames": {}, "omitted_actions": 0,
            "dialog": dialog, "text": f"Browser {dialog['type']} dialog: {dialog['message']}",
            "actions": [{"id": "accept_dialog", "kind": "accept_dialog", "node": None, "label": "OK"},
                        {"id": "dismiss_dialog", "kind": "dismiss_dialog", "node": None, "label": "Cancel"}],
        }

    def observe(self, screenshot=False):
        self._sync()
        page = None
        if not self.dialogs.get(self.session):
            self._settle()
            try:
                page = self._read_settled()
            except TimeoutError:
                PUMP.flush()  # a dialog opened mid-read; Runtime is frozen until it closes
                if not self.dialogs.get(self.session):
                    raise
        if page is None:
            page = self._dialog_page(self.dialogs[self.session])
        else:
            page["actions"].extend(self._extras())
            if screenshot:
                page["screenshot"] = self.call("Page.captureScreenshot", format="jpeg", quality=72)["data"]
        if self.alerts:
            alerts, self.alerts = self.alerts, []
            page["text"] = "\n".join(f"Alert: {message}" for message in alerts) + "\n" + page["text"]
        page["downloads"] = self.downloads[:]
        del self.downloads[:len(page["downloads"])]
        page["fingerprint"] = fingerprint(page)
        return page

    def fresh(self, page, action=None):
        PUMP.flush()
        dialog = self.dialogs.get(self.session)
        if dialog or page.get("dialog"):
            return dialog is not None and dialog == page.get("dialog")
        try:
            if action is not None:
                node = int(action["node"])
                current = self.evaluate(f"({GUARD})({node})", self._session_for(action.get("frame")))
                snap = page["frames"][action.get("frame") or "main"]
                return current == [snap["page_key"], snap["guards"].get(str(node))]
            return all(self.evaluate(MARKER, self._session_for(None if key == "main" else key)) == snap["marker"]
                       for key, snap in page["frames"].items())
        except (KeyError, RuntimeError):
            return False

    def act(self, operation, action, page, **inputs):
        handler = getattr(self, "_do_" + operation.lower(), None) if operation.isupper() else None
        if handler is None:
            raise ValueError(f"Unsupported operation {operation!r}; nothing executed.")
        guarded = action.get("node") is not None and action["kind"] != "fill"
        if not self.fresh(page, action if guarded else None):
            raise StalePage("Page changed since this decision. Observe again.")
        result = handler(action, **inputs) or {}
        self.after_input = None if operation == "WAIT" else action
        return result

    def _owner(self, fid):
        """Remote object of the <iframe> element holding a cross-origin frame, in its owner's session."""
        info = self.frames[fid]
        node = self.call("DOM.getFrameOwner", info["owner"], frameId=fid)["backendNodeId"]
        return self.call("DOM.resolveNode", info["owner"], backendNodeId=node)["object"]["objectId"]

    def _point(self, action):
        """Top-level viewport centre of an observed element, re-read and hit-tested now."""
        frame = action.get("frame")
        point = self.evaluate(f"({LOCATE})({int(action['node'])})", self._session_for(frame))
        while point is not None and frame:
            info = self.frames.get(frame)
            if info is None:
                raise StalePage("Frame is gone. Observe again.")
            point = self.call("Runtime.callFunctionOn", info["owner"], objectId=self._owner(frame),
                              functionDeclaration=HIT_OWNER, returnByValue=True,
                              arguments=[{"value": point["x"]}, {"value": point["y"]}]).get("result", {}).get("value")
            frame = info["parent"]
        if point is None:
            raise StalePage("Target changed or is covered. Observe again.")
        return point["x"], point["y"]

    def _mouse(self, x, y, button="left", count=1):
        self.input("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
        for kind in ("mousePressed", "mouseReleased"):
            self.input("Input.dispatchMouseEvent", type=kind, x=x, y=y, button=button, clickCount=count)

    def _do_click(self, action, **_):
        self._mouse(*self._point(action))

    def _do_double_click(self, action, **_):
        x, y = self._point(action)
        self._mouse(x, y)
        self._mouse(x, y, count=2)

    def _do_right_click(self, action, **_):
        self._mouse(*self._point(action), button="right")

    def _do_hover(self, action, **_):
        x, y = self._point(action)
        self.input("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)

    def _do_type_text(self, action, text=None, **_):
        if not isinstance(text, str) or not text:
            raise ValueError("TYPE_TEXT needs a value; nothing typed.")
        group = action.get("group")
        if group:
            if len(text) != len(group):
                raise ValueError(f"This code needs {len(group)} characters; nothing typed.")
            for node, char in zip(group, text):
                self._mouse(*self._point({**action, "node": node}))
                self.input("Input.insertText", text=char)
            return
        if action.get("constraints", {}).get("type") in DATE_TYPES:
            args = json.dumps({"node": action["node"], "value": text})
            if not self.evaluate(f"({SET_VALUE})({args})", self._session_for(action.get("frame"))):
                raise RuntimeError("The field did not accept this value; inspect before retrying.")
            return
        self._mouse(*self._point(action))
        modifiers = 4 if sys.platform == "darwin" else 2
        self.input("Input.dispatchKeyEvent", type="keyDown", key="a", code="KeyA", modifiers=modifiers,
                   commands=["selectAll"])
        self.input("Input.dispatchKeyEvent", type="keyUp", key="a", code="KeyA", modifiers=modifiers)
        self.input("Input.insertText", text=text)

    def _do_press_key(self, action, key=None, **_):
        if key not in KEYS:
            raise ValueError("PRESS_KEY needs a known key; nothing pressed.")
        if action.get("node") is not None:
            if action.get("role") == "slider":  # a click would move the thumb; focus it instead
                if not self.evaluate(f"({FOCUS})({int(action['node'])})", self._session_for(action.get("frame"))):
                    raise StalePage("Slider changed. Observe again.")
            else:
                self._mouse(*self._point(action))
        name, code, keycode, text, modifiers = KEYS[key]
        down = {"type": "keyDown" if text else "rawKeyDown", "key": name, "code": code,
                "windowsVirtualKeyCode": keycode, "nativeVirtualKeyCode": keycode, "modifiers": modifiers}
        if text:
            down["text"] = text
        self.input("Input.dispatchKeyEvent", **down)
        self.input("Input.dispatchKeyEvent", type="keyUp", key=name, code=code, windowsVirtualKeyCode=keycode,
                   nativeVirtualKeyCode=keycode, modifiers=modifiers)

    def _do_drag(self, action, drop=None, **_):
        if not drop or drop.get("node") is None:
            raise ValueError("DRAG needs an observed drop target; nothing moved.")
        (sx, sy), (tx, ty) = self._point(action), self._point(drop)
        self.intercepted = None
        # HTML5 drag-and-drop never completes from raw mouse events; intercept its data and replay the drop.
        self.call("Input.setInterceptDrags", enabled=True)
        try:
            self.input("Input.dispatchMouseEvent", type="mouseMoved", x=sx, y=sy)
            self.input("Input.dispatchMouseEvent", type="mousePressed", x=sx, y=sy, button="left", clickCount=1)
            for step in range(1, 11):
                self.input("Input.dispatchMouseEvent", type="mouseMoved", button="left",
                           x=sx + (tx - sx) * step / 10, y=sy + (ty - sy) * step / 10)
            deadline = time.monotonic() + 0.3
            while self.intercepted is None and time.monotonic() < deadline:
                PUMP.flush()
                time.sleep(0.02)
            if self.intercepted is not None:
                for kind in ("dragEnter", "dragOver", "drop"):
                    self.input("Input.dispatchDragEvent", type=kind, x=tx, y=ty, data=self.intercepted)
            self.input("Input.dispatchMouseEvent", type="mouseReleased", x=tx, y=ty, button="left", clickCount=1)
        finally:
            self.call("Input.setInterceptDrags", enabled=False)

    def _do_upload(self, action, files=None, **_):
        paths = [str(Path(f).resolve()) for f in files or []]
        if not paths:
            raise ValueError("UPLOAD needs at least one file; nothing attached.")
        missing = [p for p in paths if not Path(p).is_file()]
        if missing:
            raise ValueError(f"{missing[0]} does not exist; nothing attached.")
        if action.get("chooser"):
            chooser, self.chooser = self.chooser, None
            if not chooser:
                raise StalePage("The file picker closed. Observe again.")
            self.call("DOM.setFileInputFiles", chooser["session"], files=paths if chooser["multiple"] else paths[:1],
                      backendNodeId=chooser["node"])
        elif action.get("drop"):
            x, y = self._point(action)
            data = {"items": [], "files": paths, "dragOperationsMask": 1}
            for kind in ("dragEnter", "dragOver", "drop"):
                self.input("Input.dispatchDragEvent", type=kind, x=x, y=y, data=data)
        else:
            session = self._session_for(action.get("frame"))
            response = self.call("Runtime.evaluate", session,
                                 expression=f"window.__jevFast?.nodes.get({int(action['node'])}) ?? null")
            element = response.get("result", {}).get("objectId")
            if not element:
                raise StalePage("File input changed. Observe again.")
            self.call("DOM.setFileInputFiles", session, files=paths if action.get("multiple") else paths[:1],
                      objectId=element)

    def _do_select(self, action, **_):
        args = json.dumps({"node": action["node"], "value": action["value"]})
        response = self.call("Runtime.evaluate", self._session_for(action.get("frame")),
                             expression=f"({SELECT})({args})", returnByValue=True)
        if response.get("exceptionDetails"):
            raise RuntimeError("Dropdown execution was interrupted; inspect before retrying.")
        if response.get("result", {}).get("value") is None:
            raise RuntimeError("Dropdown execution was not confirmed; inspect before retrying.")

    def _scroll(self, action, x, y):
        # ponytail: scrollBy, not wheel events; add wheel dispatch if a site listens only to wheel.
        args = json.dumps({"node": action.get("node"), "x": x, "y": y})
        if not self.evaluate(f"({SCROLL})({args})", self._session_for(action.get("frame"))):
            raise StalePage("Scroll target changed. Observe again.")

    def _do_scroll_up(self, action, **_):
        self._scroll(action, 0, -1)

    def _do_scroll_down(self, action, **_):
        self._scroll(action, 0, 1)

    def _do_scroll_left(self, action, **_):
        self._scroll(action, -1, 0)

    def _do_scroll_right(self, action, **_):
        self._scroll(action, 1, 0)

    def _do_wait(self, action, **_):
        time.sleep(0.1)

    def close(self):
        for target in list(self.tabs):
            try:
                cdp("Target.closeTarget", targetId=target)
            except (RuntimeError, TimeoutError):
                pass
            self._forget(target)


def merge(parts):
    """One page state from the main document and its cross-origin frames. Element ids are page-wide."""
    main = parts[0][1]
    page = {"url": main["url"], "title": main["title"], "scroll": main["scroll"], "actions": [], "frames": {},
            "omitted_actions": 0}
    texts, count = [], 0
    for index, (frame, state) in enumerate(parts):
        host = urlparse(state["url"]).netloc
        page["frames"][frame or "main"] = {k: state[k] for k in ("url", "marker", "page_key", "guards")}
        page["omitted_actions"] += state["omitted_actions"]
        texts.append(state["text"] if frame is None else f"[Frame {host}]\n{state['text']}")
        for action in state["actions"]:
            if frame is not None and action["kind"] == "wait":
                continue
            item = dict(action)
            if action.get("node") is not None:
                count += 1
                item["id"] = f"e{count}"
            elif frame is not None:
                item["id"] = f"{action['id']}_f{index}"
                item["label"] = f"Frame {host}"
            if frame is not None:
                item["frame"] = frame
            page["actions"].append(item)
    page["text"] = "\n".join(texts)[:8000]
    return page


def fingerprint(state):
    content = {k: state[k] for k in ("url", "text", "actions", "scroll")}
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def safe_name(name):
    """A suggested file name reduced to one safe path component."""
    cleaned = re.sub(r"[^\w.\- ()]", "_", Path(str(name).replace("\\", "/")).name).strip(" .")
    return cleaned[:120] or "download"


def unique(path):
    candidate, n = path, 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    return candidate
