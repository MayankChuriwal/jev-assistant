"""Agent Chrome through Browser Harness: observation, execution, tabs, frames, and browser events."""

import base64
import hashlib
import json
import os
import re
import sys
import threading
import time
import weakref
from pathlib import Path
from urllib.parse import urlparse

# Browser Harness fixes its daemon name at import. "default" drives the user's everyday Chrome: never fall back to it.
os.environ.setdefault("BU_NAME", "jev")
os.environ.setdefault("BU_CDP_URL", "http://127.0.0.1:9333")
if os.environ["BU_NAME"] == "default":
    raise RuntimeError("BU_NAME=default drives the user's everyday Chrome; the agent needs its own daemon: BU_NAME=jev")

from browser_harness.admin import ensure_daemon  # noqa: E402
from browser_harness.helpers import cdp, drain_events  # noqa: E402

from . import rules  # noqa: E402

HOME = Path(os.environ.get("JEV_HOME") or Path.home() / ".jev")
INCOMING = HOME / "downloads" / ".incoming"
# Atomically read visible content and controls, preserving actual DOM node identity.
READ_STATE = Path(__file__).with_name("snapshot.js").read_text()
MARKER = f"(() => {{ const state={READ_STATE}; return state?.marker ?? null; }})()"
VIEWPORT = {"width": 1120, "height": 780}
MAX_SHOWN_PDF = 25 * 1024 * 1024  # a PDF Chrome shows in its viewer (no download) is kept up to this size
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
SPOT = "((node, words) => window.__jevFast?.spot(node, words) ?? null)"
# Runs on the <iframe> element in its owner document: map a point inside the frame to the owner's viewport.
HIT_OWNER = r"""function (x, y) {
  const r = this.getBoundingClientRect();
  let w = window, c = window.__jevFast;  // the snapshot runs in the session's top window only
  try { while (!c && w.parent !== w) { w = w.parent; c = w.__jevFast; } } catch {}
  return c?.climb(this, r.x + this.clientLeft + x, r.y + this.clientTop + y) ?? null;
}"""
FRAME_VISIBLE = r"""function () {
  const r = this.getBoundingClientRect();
  let x = r.x, y = r.y, w = window;  // map through same-origin frames to the session's top viewport
  try {
    while (w.frameElement) {
      const f = w.frameElement, fr = f.getBoundingClientRect();
      x += fr.x + f.clientLeft;
      y += fr.y + f.clientTop;
      w = w.parent;
    }
  } catch {}
  // Tracking pixels are 1×1: frames smaller than 8×8 are never read.
  return r.width >= 8 && r.height >= 8 && x + r.width > 0 && y + r.height > 0 && x < w.innerWidth &&
    y < w.innerHeight && this.checkVisibility({checkOpacity: true, checkVisibilityCSS: true});
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
# Blur fields that hold secrets for the duration of a capture that leaves the process.
MASK = r"""((ids, on, hide) => {
  // For one capture: blur and dot every filled text field and every field that received a secret (§10.4), and blur
  // text showing the user's own details, which reach models only as placeholders (§10.2).
  const c = window.__jevFast;
  const roots = c?.roots?.length ? c.roots : [document];
  if (!on) {
    for (const e of roots.flatMap(r => [...r.querySelectorAll('[data-jev-mask]')])) {
      [e.style.filter, e.style.webkitTextSecurity] = JSON.parse(e.dataset.jevMask);
      delete e.dataset.jevMask;
    }
    return true;
  }
  const skip = ['checkbox', 'radio', 'button', 'submit', 'reset', 'range', 'color', 'file', 'hidden', 'image'];
  const filled = roots.flatMap(r => [...r.querySelectorAll('input,textarea')])
    .filter(e => e.value && !skip.includes(e.type));
  const values = (hide || []).map(v => [String(v).toLowerCase(), String(v).replace(/\D/g, '')]);
  const shows = text => {
    const lower = text.toLowerCase(), digits = text.replace(/\D/g, '');
    return values.some(([word, number]) => (word.length >= 3 && lower.includes(word)) ||
      (number.length >= 7 && digits.includes(number)));
  };
  const texts = [];
  if (values.length) for (const r of roots) {
    const walker = (r.ownerDocument || r).createTreeWalker(r.body || r, NodeFilter.SHOW_TEXT);
    for (let n; (n = walker.nextNode());) if (n.parentElement && shows(n.textContent)) texts.push(n.parentElement);
  }
  for (const e of new Set([...ids.map(id => c?.nodes.get(id)).filter(Boolean), ...filled, ...texts])) {
    if (e.dataset.jevMask) continue;
    e.dataset.jevMask = JSON.stringify([e.style.filter, e.style.webkitTextSecurity]);
    e.style.filter = 'blur(12px)';
    e.style.webkitTextSecurity = 'disc';
  }
  return true;
})"""
# Numbers drawn over the page for one capture, so the user can name what to click (a captcha's squares).
MARKS = r"""(marks => {
  document.getElementById('__jev_marks')?.remove();
  if (!marks) return true;
  const host = document.createElement('div');
  host.id = '__jev_marks';
  host.style.cssText = 'position:fixed;inset:0;z-index:2147483647;pointer-events:none';
  const root = host.attachShadow({mode: 'closed'});
  for (const m of marks) {
    const tag = document.createElement('span');
    tag.textContent = m.n;
    tag.style.cssText = `position:fixed;left:${m.x - 11}px;top:${m.y - 11}px;min-width:22px;height:22px;` +
      'box-sizing:border-box;padding:0 5px;border-radius:11px;border:2px solid #fff;font:bold 12px/18px sans-serif;' +
      'text-align:center;color:#fff;background:rgba(255,0,90,.92)';
    root.append(tag);
  }
  document.documentElement.append(host);
  return true;
})"""
# A labelled grid drawn over the page for one capture, in a closed shadow root: the page's own reads never see it.
GRID = r"""(cells => {
  document.getElementById('__jev_grid')?.remove();
  if (!cells) return true;
  const host = document.createElement('div');
  host.id = '__jev_grid';
  host.style.cssText = 'position:fixed;inset:0;z-index:2147483647;pointer-events:none';
  const root = host.attachShadow({mode: 'closed'});
  for (const [name, c] of Object.entries(cells)) {
    const cell = document.createElement('div');
    cell.style.cssText = `position:fixed;left:${c.x}px;top:${c.y}px;width:${c.w}px;height:${c.h}px;` +
      'box-sizing:border-box;border:1px solid rgba(255,0,90,.9)';
    const tag = document.createElement('span');
    tag.textContent = name;
    tag.style.cssText = 'position:absolute;left:1px;top:1px;padding:0 3px;font:bold 11px/14px sans-serif;' +
      'color:#fff;background:rgba(255,0,90,.85)';
    cell.append(tag);
    root.append(cell);
  }
  document.documentElement.append(host);
  return true;
})"""

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


class ExecutionUncertain(RuntimeError):
    """Input may already have reached the page. Never retried: the task stops and reports it."""


class TabsClosed(RuntimeError):
    """The task's tab is gone (closed by the site, the user, or Chrome): there is no page left to work on."""


class EventPump:
    """Drains Browser Harness's one shared event buffer and routes each event by CDP session id."""

    def __init__(self):
        self.handlers = {}
        self.lock = threading.RLock()
        self.thread = None
        self.running = True

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
        while self.running:
            try:
                self.flush()
            except Exception as error:  # an unexpected batch must never stop routing for every task
                print(f"jev: event pump: {error!r}", file=sys.stderr)
            time.sleep(0.05)


PUMP = EventPump()
OWNERS = weakref.WeakValueDictionary()  # tab target id -> Browser, so a download reaches the task that started it
DOWNLOADS = {}  # download guid -> Browser that started it
DAEMON = threading.Lock()  # tasks starting together must not each spawn or restart the daemon
OPENERS = {}  # pop-up target id -> the target that opened it
POPUP_DOWNLOADS = set()  # guids of downloads that started in a pop-up
WATCH = {"session": None}  # the browser-level session that sees those downloads


def watch_popups():
    """A download that starts in a pop-up (a link with target=_blank) is reported only to a browser-level session.
    Keep one, and credit such downloads to the task whose tab opened the pop-up. Call with DAEMON held."""
    for _ in range(2):
        if WATCH["session"] is None:
            WATCH["session"] = cdp("Target.attachToBrowserTarget")["sessionId"]
            PUMP.register(WATCH["session"], on_browser_event)
            PUMP.register(None, on_browser_event)  # the daemon relays target discovery without a session id
            cdp("Target.setDiscoverTargets", session_id=WATCH["session"], discover=True)
        try:
            cdp("Browser.setDownloadBehavior", session_id=WATCH["session"], behavior="allowAndName",
                downloadPath=str(INCOMING), eventsEnabled=True)
            return
        except RuntimeError:  # the daemon restarted, and its sessions with it
            PUMP.unregister(WATCH["session"])
            WATCH["session"] = None


def on_browser_event(event):
    """Runs on the pump thread. Tabs report their own downloads; this handles the ones pop-ups start."""
    method, params = event["method"], event.get("params", {})
    if method in {"Target.targetCreated", "Target.targetInfoChanged"} and params["targetInfo"].get("openerId"):
        OPENERS[params["targetInfo"]["targetId"]] = params["targetInfo"]["openerId"]
    elif method == "Target.targetDestroyed":
        OPENERS.pop(params.get("targetId"), None)
    elif method == "Browser.downloadWillBegin" and params.get("frameId") in OPENERS:
        target = params["frameId"]
        for _ in range(3):  # a pop-up opened by a pop-up still belongs to the tab that started the chain
            target = OPENERS.get(target, target)
            owner = OWNERS.get(target)
            if owner:
                POPUP_DOWNLOADS.add(params["guid"])
                DOWNLOADS[params["guid"]] = owner
                owner.pending_downloads[params["guid"]] = params["suggestedFilename"]
                return
    elif method == "Browser.downloadProgress" and params.get("state") == "completed":
        if params["guid"] not in POPUP_DOWNLOADS:
            return
        POPUP_DOWNLOADS.discard(params["guid"])
        owner = DOWNLOADS.pop(params["guid"])
        name = owner.pending_downloads.pop(params["guid"], None)
        if name:
            owner.downloads.append(owner._store_download(params["guid"], name))


def forget_sites(sites):
    """Sign the agent Chrome out of these sites: delete their cookies and clear their site storage, so the next task
    signs in again. Returns {host: cookies removed}. The user's everyday Chrome is never touched."""
    with DAEMON:
        ensure_daemon()
    target = cdp("Target.createTarget", url="about:blank", background=True)["targetId"]
    session = cdp("Target.attachToTarget", targetId=target, flatten=True)["sessionId"]
    try:
        cookies = cdp("Storage.getCookies")["cookies"]
        removed = {}
        for site in sites:
            parsed = urlparse(site if "://" in site else "https://" + site)
            host = parsed.hostname.removeprefix("www.")
            mine = [c for c in cookies if c["domain"].lstrip(".") == host or c["domain"].endswith("." + host)]
            for cookie in mine:
                cdp("Network.deleteCookies", session_id=session, name=cookie["name"], domain=cookie["domain"],
                    path=cookie["path"])
            origins = ({f"{parsed.scheme}://{parsed.netloc}"} if "://" in site else
                       {f"https://{prefix}{host}" for prefix in ("", "www.", "m.", "auth.")})
            for origin in origins:
                cdp("Storage.clearDataForOrigin", origin=origin, storageTypes="all")
            removed[host] = len(mine)
        return removed
    finally:
        cdp("Target.closeTarget", targetId=target)


class Browser:
    """One task's tabs in the agent Chrome. Every executed target resolves from an observed node."""

    def __init__(self, url, *, downloads=None, geolocation=None):
        with DAEMON:
            ensure_daemon()
            watch_popups()
        self.tabs, self.opener, self.frames, self.dialogs, self.titles = {}, {}, {}, {}, {}
        self.alerts, self.downloads, self.pending_downloads = [], [], {}
        self.chooser = self.intercepted = self.after_input = self.active = self.top = None
        self.shown_pdfs = set()  # addresses of PDFs already kept from Chrome's viewer
        self.downloads_dir = Path(downloads) if downloads else HOME / "downloads"
        self.geolocation = geolocation
        INCOMING.mkdir(parents=True, exist_ok=True)
        for permission, setting in (("notifications", "denied"), ("camera", "denied"), ("microphone", "denied"),
                                    ("geolocation", "granted")):  # sites detect the device's own location
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
        try:
            return self.tabs[self.active]
        except KeyError:
            raise TabsClosed("The task's browser tab has closed.") from None

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
        self.mutated = True
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
        OWNERS[target] = self
        if opener:
            self.opener[target] = opener
        self.call("Page.enable", session)
        self.call("DOM.enable", session)  # DOM.resolveNode needs it; no events flow until a document is requested
        self.call("Page.setInterceptFileChooserDialog", session, enabled=True)
        if not opener:  # a pop-up that routed downloads to itself would take them along when it closes
            self._route_downloads(session)
        self.call("Emulation.setDeviceMetricsOverride", session, deviceScaleFactor=1, mobile=False, **VIEWPORT)
        # Keep rAF/menus rendering in a background tab without activating it.
        self.call("Emulation.setFocusEmulationEnabled", session, enabled=True)
        if self.geolocation:
            self.call("Emulation.setGeolocationOverride", session, accuracy=50, **self.geolocation)
        PUMP.register(session, self._on_event)
        return target

    def _route_downloads(self, session):
        # Downloads land in one folder. Chrome reports them to the session that set this, and resets it when that
        # session detaches, so every tab sets it on adoption and the survivors set it again when a tab closes.
        self.call("Browser.setDownloadBehavior", session, behavior="allowAndName", downloadPath=str(INCOMING),
                  eventsEnabled=True)

    def _forget(self, target):
        session = self.tabs.pop(target, None)
        if session:
            PUMP.unregister(session)
            self.dialogs.pop(session, None)
        for fid in [f for f, info in self.frames.items() if info.get("tab", target) == target]:
            PUMP.unregister(self.frames.pop(fid)["session"])
        if target == self.active:
            opener = self.opener.get(target)
            self.active = opener if opener in self.tabs else next(reversed(self.tabs), None)
        OWNERS.pop(target, None)
        for owner in list(OWNERS.values()):  # the closed tab may have reset downloads for every task
            for remaining in list(owner.tabs.values()):
                try:
                    owner._route_downloads(remaining)
                except (RuntimeError, TimeoutError):
                    pass  # that tab is closing too

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
            owner = OWNERS.get(params.get("frameId")) or self  # a main frame's id is its tab's target id
            DOWNLOADS[params["guid"]] = owner
            owner.pending_downloads[params["guid"]] = params["suggestedFilename"]
        elif method == "Browser.downloadProgress" and params.get("state") == "completed":
            owner = DOWNLOADS.pop(params["guid"], self)
            name = owner.pending_downloads.pop(params["guid"], None)
            if name:
                owner.downloads.append(owner._store_download(params["guid"], name))

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

    def _keep_shown_pdf(self):
        """A PDF that Chrome shows in its own viewer (an invoice link without an attachment header) starts no download:
        keep the bytes the tab already loaded, once per address, as a download. Nothing is fetched again, so no request
        goes anywhere the page didn't; names and the size cap are a download's."""
        frame = self.top or {}
        url = frame.get("url", "")
        if frame.get("mimeType") != "application/pdf" or url in self.shown_pdfs:
            return
        self.shown_pdfs.add(url)
        try:
            found = self.call("Page.getResourceContent", frameId=frame["id"], url=url, timeout=30.0)
        except (RuntimeError, TimeoutError, KeyError):
            return  # gone, or not kept by Chrome: the planner still sees the viewer and its own Download button
        content = found.get("content") or ""
        if not found.get("base64Encoded") or len(content) * 3 // 4 > MAX_SHOWN_PDF:
            return
        data = base64.b64decode(content)
        if not data.startswith(b"%PDF"):
            return
        name = safe_name(Path(urlparse(url).path).name or "document")
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        path = unique(self.downloads_dir / (name if name.lower().endswith(".pdf") else name + ".pdf"))
        path.write_bytes(data)
        self.downloads.append(str(path))

    def navigate(self, url):
        self._navigation("Page.navigate", url=url)

    def _navigation(self, method, **params):
        """Start a navigation. A leave-page prompt blocks the call: surface it as a dialog instead of waiting."""
        previous = self._where()
        self.mutated = True
        try:
            self.call(method, timeout=3.0, **params)
        except TimeoutError:
            PUMP.flush()  # no dialog: a slow server; the load wait below still applies
        self._wait_loaded(previous)

    def _where(self):
        """[document's timeOrigin, address]: where a navigation starts from. None when the page can't say."""
        try:
            where = self.evaluate("[performance.timeOrigin, location.href]")
        except (StalePage, RuntimeError, TimeoutError):
            return None
        return where if isinstance(where, list) and len(where) == 2 else None

    def _wait_loaded(self, previous=None, timeout=15.0):
        """Wait for a new, complete document, or a complete one at a new address: back and forward on a single-page
        site keep their document. A slow page is observed as it is once the deadline passes."""
        origin, url = previous or (None, None)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.dialogs.get(self.session):
                return  # a dialog freezes the page; observe offers it
            try:
                now, state, href = self.evaluate("[performance.timeOrigin, document.readyState, location.href]")
                if state == "complete" and (now != origin or (url is not None and href != url)):
                    return
            except (StalePage, RuntimeError, TimeoutError, TypeError, ValueError):
                pass
            time.sleep(0.05)

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

    def _sync(self):
        PUMP.flush()
        infos = cdp("Target.getTargets")["targetInfos"]
        self._sync_tabs(infos)
        if not self.dialogs.get(self.session):  # an open dialog freezes the page, Page.getFrameTree included
            self._sync_frames(infos)

    def _frame_ids(self, session, tree=None):
        ids, stack = [], [tree or self.call("Page.getFrameTree", session)["frameTree"]]
        while stack:
            node = stack.pop()
            ids.append(node["frame"]["id"])
            stack.extend(node.get("childFrames", []))
        return ids

    def _sync_frames(self, infos):
        """Attach the active tab's cross-origin frames. Each frame's owner session holds its <iframe> element."""
        tree = self.call("Page.getFrameTree")["frameTree"]
        self.top = tree["frame"]  # the active tab's own document: its address and type
        owners = {fid: (self.session, None) for fid in self._frame_ids(self.session, tree)}
        seen, progress = set(), True
        frames = [t for t in infos if t["type"] == "iframe"]
        while progress:  # parents are found before children, whatever order Chrome lists them in
            progress = False
            for info in frames:
                fid = info["targetId"]
                if fid in seen or info.get("parentFrameId") not in owners:
                    continue
                seen.add(fid)
                progress = True
                try:
                    if fid not in self.frames:
                        session = cdp("Target.attachToTarget", targetId=fid, flatten=True)["sessionId"]
                        self.frames[fid] = {"session": session}
                        PUMP.register(session, self._on_event)
                        self.call("Page.enable", session)
                        self.call("DOM.enable", session)
                    children = self._frame_ids(self.frames[fid]["session"])
                except (RuntimeError, TimeoutError):  # the frame went away after Chrome listed it (the page
                    # navigated), or is too slow to answer (an ad still loading): dropped, read with a later look
                    if fid in self.frames:
                        PUMP.unregister(self.frames.pop(fid)["session"])
                    continue
                owner, parent = owners[info["parentFrameId"]]
                self.frames[fid].update(owner=owner, parent=parent, tab=self.active)
                for child in children:
                    owners[child] = (self.frames[fid]["session"], fid)
        for fid in [f for f, info in self.frames.items() if info.get("tab", self.active) == self.active
                    and f not in seen]:
            PUMP.unregister(self.frames.pop(fid)["session"])

    def _frame_visible(self, fid):
        try:
            response = self.call("Runtime.callFunctionOn", self.frames[fid]["owner"], objectId=self._owner(fid),
                                 functionDeclaration=FRAME_VISIBLE, returnByValue=True)
            return response.get("result", {}).get("value") is True
        except (RuntimeError, TimeoutError, KeyError):
            return False

    def _read(self):
        main = self.evaluate(READ_STATE)
        if main is None:
            raise StalePage("Document is navigating")
        parts, shown = [(None, main)], set()
        # ponytail: every visible third-party frame is snapshotted each observation; cap them if ads make it slow.
        for fid, info in list(self.frames.items()):
            if info["tab"] != self.active or (info["parent"] and info["parent"] not in shown):
                continue
            if not self._frame_visible(fid):
                continue
            try:
                state = self.evaluate(READ_STATE, info["session"])
            except (StalePage, RuntimeError, TimeoutError):
                continue  # a frame mid-navigation is skipped; its controls return with the next observation
            if state:
                shown.add(fid)
                parts.append((fid, state))
        return merge(parts)

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
            self._keep_shown_pdf()
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
        # Node actions compare their own target; anything else compares only its own document, so a ticking
        # third-party frame (chat widget, ad) never makes the rest of the page stale.
        try:
            frame = (action or {}).get("frame")
            snap = page["frames"][frame or "main"]
            session = self._session_for(frame)
            if action is not None and action.get("node") is not None:
                node = int(action["node"])
                current = self.evaluate(f"({GUARD})({node})", session)
                return current == [snap["page_key"], snap["guards"].get(str(node))]
            return self.evaluate(MARKER, session) == snap["marker"]
        except (KeyError, RuntimeError):
            return False

    def act(self, operation, action, page, **inputs):
        handler = getattr(self, "_do_" + operation.lower(), None) if operation.isupper() else None
        if handler is None:
            raise ValueError(f"Unsupported operation {operation!r}; nothing executed.")
        if not self.fresh(page, action):
            raise StalePage("Page changed since this decision. Observe again.")
        self.mutated = False
        try:
            result = handler(action, **inputs) or {}
        except ExecutionUncertain:
            raise
        except Exception as error:
            if self.mutated:  # input may have reached the page: never let the loop retry it as stale
                raise ExecutionUncertain(f"{error} (input may have reached the page; not retried)") from error
            raise
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
        point = None
        if action.get("within"):  # the quoted words inside the element, else its centre
            point = self.evaluate(f"({SPOT})({int(action['node'])}, {json.dumps(action['within'])})",
                                  self._session_for(frame))
        point = point or self.evaluate(f"({LOCATE})({int(action['node'])})", self._session_for(frame))
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

    def focused_label(self):
        """Label of the control a key press on the page would reach, or ""."""
        try:
            return self.evaluate("window.__jevFast?.focused?.() ?? ''") or ""
        except (StalePage, RuntimeError, TimeoutError):
            return ""

    def screenshot(self, action=None, masked=(), pad=16, clip=None, hide=()):
        """Base64 PNG of the viewport, of an element's region, or of `clip` (page coordinates), with filled fields and
        any text containing one of `hide` masked."""
        by_frame = {}
        for frame, node in masked:
            by_frame.setdefault(frame, []).append(node)
        params = {"format": "png"}
        if clip:
            params["clip"] = clip
        elif action is not None and action.get("rect") and action.get("node") is not None:
            x, y = self._point(action)
            w, h = action["rect"]["w"], action["rect"]["h"]
            sx, sy = self.evaluate("[scrollX, scrollY]")  # a clip is in page coordinates; the point is in the viewport
            params["clip"] = {"x": max(0, x - w / 2 - pad) + sx, "y": max(0, y - h / 2 - pad) + sy,
                              "width": w + 2 * pad, "height": h + 2 * pad, "scale": 1}
        try:
            self._mask(by_frame, True, hide)
            return self.call("Page.captureScreenshot", **params)["data"]
        finally:
            self._mask(by_frame, False)

    def grid_screenshot(self, region=None, columns=8, rows=6, masked=(), scale=1, hide=()):
        """(PNG of the viewport or `region` under a labelled grid, the grid's cells in viewport coordinates)."""
        width, height, sx, sy = self.evaluate("[innerWidth, innerHeight, scrollX, scrollY]")
        region = region or {"x": 0, "y": 0, "w": width, "h": height}
        cells = rules.grid(region, columns, rows)
        self.evaluate(f"({GRID})({json.dumps(cells)})")
        try:
            # Cells stay in viewport coordinates, where clicks land; the capture's clip is in page coordinates.
            clip = {"x": region["x"] + sx, "y": region["y"] + sy, "width": region["w"], "height": region["h"],
                    "scale": scale}
            return self.screenshot(masked=masked, clip=clip, hide=hide), cells
        finally:
            self.evaluate(f"({GRID})(null)")

    def marked_screenshot(self, actions, masked=(), hide=()):
        """A viewport PNG with a number (1, 2, …) on each action, for the user to name what to click."""
        marks = []
        for n, action in enumerate(actions, 1):
            try:
                x, y = self._point(action)
            except (StalePage, RuntimeError):
                continue  # covered or gone: it gets no number
            marks.append({"n": n, "x": x, "y": y})
        self.evaluate(f"({MARKS})({json.dumps(marks)})")
        try:
            return self.screenshot(masked=masked, hide=hide)
        finally:
            self.evaluate(f"({MARKS})(null)")

    def _mask(self, by_frame, on, hide=()):
        """Mask (or unmask) the page and every frame of this tab: a re-rendered field is found again by its value.
        Masking that fails where the page is still there stops the capture: nothing is captured unmasked."""
        frames = {None, *by_frame, *(f for f, info in self.frames.items() if info["tab"] == self.active)}
        for frame in frames:
            try:
                nodes, values = json.dumps(by_frame.get(frame, [])), json.dumps(list(hide), ensure_ascii=False)
                self.evaluate(f"({MASK})({nodes}, {json.dumps(on)}, {values})", self._session_for(frame))
            except (StalePage, RuntimeError, TimeoutError):
                if on and (frame is None or frame in self.frames):
                    raise RuntimeError("Couldn't hide the page's fields for a screenshot; nothing was captured.") \
                        from None

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

    def _do_look(self, action, point=None, hover=False, **_):
        if not point:
            raise ValueError("LOOK needs a point found on the screen; nothing clicked.")
        if hover:  # a menu that opens on hover: move there, press nothing
            self.input("Input.dispatchMouseEvent", type="mouseMoved", x=point[0], y=point[1])
        else:
            self._mouse(*point)

    def _do_type_text(self, action, text=None, **_):
        if not isinstance(text, str) or not text:
            raise ValueError("TYPE_TEXT needs a value; nothing typed.")
        group = action.get("group")
        if group:
            if len(text) != len(group):
                raise ValueError(f"This code needs {len(group)} characters; nothing typed.")
            points = [self._point({**action, "node": node}) for node in group]  # all reachable before typing any
            for point, char in zip(points, text):
                self._mouse(*point)
                self._select_all()  # a box a rejected code left filled takes nothing more (maxlength 1)
                self.input("Input.insertText", text=char)
            return
        if action.get("constraints", {}).get("type") in DATE_TYPES:
            args = json.dumps({"node": action["node"], "value": text})
            self.mutated = True
            if not self.evaluate(f"({SET_VALUE})({args})", self._session_for(action.get("frame"))):
                raise RuntimeError("The field did not accept this value; inspect before retrying.")
            return
        self._mouse(*self._point(action))
        self._select_all()
        self.input("Input.insertText", text=text)

    def _select_all(self):
        modifiers = 4 if sys.platform == "darwin" else 2
        self.input("Input.dispatchKeyEvent", type="keyDown", key="a", code="KeyA", modifiers=modifiers,
                   commands=["selectAll"])
        self.input("Input.dispatchKeyEvent", type="keyUp", key="a", code="KeyA", modifiers=modifiers)

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
        if not drop or (drop.get("node") is None and not drop.get("point")):
            raise ValueError("DRAG needs an observed drop target or a point; nothing moved.")
        (sx, sy) = self._point(action)
        (tx, ty) = drop["point"] if drop.get("point") else self._point(drop)

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
            self.mutated = True
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
            self.mutated = True
            self.call("DOM.setFileInputFiles", session, files=paths if action.get("multiple") else paths[:1],
                      objectId=element)

    def _do_select(self, action, **_):
        args = json.dumps({"node": action["node"], "value": action["value"]})
        self.mutated = True
        response = self.call("Runtime.evaluate", self._session_for(action.get("frame")),
                             expression=f"({SELECT})({args})", returnByValue=True)
        if response.get("exceptionDetails"):
            raise RuntimeError("Dropdown execution was interrupted; inspect before retrying.")
        if response.get("result", {}).get("value") is None:
            raise RuntimeError("Dropdown execution was not confirmed; inspect before retrying.")

    def _scroll(self, action, x, y):
        # ponytail: scrollBy, not wheel events; add wheel dispatch if a site listens only to wheel.
        args = json.dumps({"node": action.get("node"), "x": x, "y": y})
        self.mutated = True
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

    def _sync_tabs(self, infos):
        """Follow pop-ups opened by this task's tabs and forget tabs that closed."""
        pages = {t["targetId"]: t for t in infos if t["type"] == "page"}
        for target in [t for t in self.tabs if t not in pages]:
            self._forget(target)
        for target, info in pages.items():
            if target not in self.tabs and info.get("openerId") in self.tabs:
                try:
                    self.active = self._adopt(target, opener=info["openerId"])
                except RuntimeError:  # it closed while being adopted, like a tab that only started a download
                    self._forget(target)
        if self.active is None:
            raise TabsClosed("Every tab of this task has closed.")
        self.titles = {t: pages[t]["title"] or pages[t]["url"] for t in self.tabs if t in pages}

    def _active_sessions(self):
        return {self.session} | {f["session"] for f in self.frames.values() if f["tab"] == self.active}

    def _extras(self):
        """Page-level operations besides observed elements."""
        extras = [
            {"id": "key_page", "kind": "key", "node": None, "role": "page", "label": "The page (focused element)"},
            {"id": "reload", "kind": "reload", "node": None, "label": "Reload"},
            {"id": "open_url", "kind": "open_url", "node": None, "label": "Open a web address"},
            {"id": "save_pdf", "kind": "save_pdf", "node": None, "label": "Save page as PDF"},
            {"id": "look", "kind": "look", "node": None, "label": "Look at the screen"},
        ]
        history = self.call("Page.getNavigationHistory")
        index, entries = history["currentIndex"], history["entries"]
        if index > 0 and entries[index - 1]["url"] != "about:blank":
            previous = entries[index - 1]
            extras.append({"id": "go_back", "kind": "go_back", "node": None,
                           "label": "Back to " + (previous["title"] or previous["url"])})
        if index < len(entries) - 1:
            following = entries[index + 1]
            extras.append({"id": "go_forward", "kind": "go_forward", "node": None,
                           "label": "Forward to " + (following["title"] or following["url"])})
        if len(self.tabs) > 1:
            for n, target in enumerate(self.tabs, 1):
                extras.append({"id": f"tab{n}", "kind": "tab", "node": None, "tab": target,
                               "current": target == self.active, "label": "Tab: " + self.titles.get(target, "")})
        if self.chooser and self.chooser["session"] in self._active_sessions():
            extras.append({"id": "file_chooser", "kind": "upload", "node": None, "chooser": True,
                           "multiple": self.chooser["multiple"], "label": "Open file picker"})
        return extras

    def _history_step(self, offset):
        history = self.call("Page.getNavigationHistory")
        index = history["currentIndex"] + offset
        if not 0 <= index < len(history["entries"]):
            raise StalePage("History changed. Observe again.")
        self._navigation("Page.navigateToHistoryEntry", entryId=history["entries"][index]["id"])

    def _do_go_back(self, action, **_):
        self._history_step(-1)

    def _do_go_forward(self, action, **_):
        self._history_step(1)

    def _do_reload(self, action, **_):
        self._navigation("Page.reload")

    def _do_open_url(self, action, url=None, **_):
        parsed = urlparse(url or "")
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("OPEN_URL accepts only http(s) addresses; nothing opened.")
        self.navigate(url)

    def _do_save_pdf(self, action, **_):
        data = self.call("Page.printToPDF", timeout=60.0, printBackground=True)["data"]
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        path = unique(self.downloads_dir / (safe_name(self.evaluate("document.title") or "page") + ".pdf"))
        path.write_bytes(base64.b64decode(data))
        self.downloads.append(str(path))
        return {"file": str(path)}

    def _do_switch_tab(self, action, **_):
        if action["tab"] not in self.tabs:
            raise StalePage("That tab closed. Observe again.")
        self.active = action["tab"]

    def _do_close_tab(self, action, **_):
        if action["tab"] not in self.tabs or len(self.tabs) < 2:
            raise StalePage("Tabs changed. Observe again.")
        self.mutated = True
        cdp("Target.closeTarget", targetId=action["tab"])
        self._forget(action["tab"])

    def _close_dialog(self, accept):
        self.mutated = True
        self.call("Page.handleJavaScriptDialog", accept=accept)
        self.dialogs.pop(self.session, None)

    def _do_accept_dialog(self, action, **_):
        self._close_dialog(True)

    def _do_dismiss_dialog(self, action, **_):
        self._close_dialog(False)

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
