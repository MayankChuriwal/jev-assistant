(() => {
  if (!document.body) return null;
  const cache = window.__jevFast ||= {ids: new WeakMap(), nodes: new Map(), next: 1};
  const identity = e => {
    if (!cache.ids.has(e)) cache.ids.set(e, cache.next++);
    const id = cache.ids.get(e); cache.nodes.set(id, e); return id;
  };
  for (const [id, e] of cache.nodes) if (!e.isConnected) cache.nodes.delete(id);

  const view = e => e.ownerDocument.defaultView;
  const styles = new Map();
  const style = e => { let s = styles.get(e); if (!s) styles.set(e, s = view(e).getComputedStyle(e)); return s; };
  const safe = e => !['password', 'file', 'hidden'].includes(e.type);
  // What a sighted user sees and can press. aria-hidden hides from screen readers only: Swiggy hides its visual menu
  // cards (with their ADD buttons) behind a screen-reader copy, so controls count when rendered; text skips them.
  const rendered = e => !e.closest('[inert]') && e.checkVisibility({checkOpacity: true, checkVisibilityCSS: true});
  const visible = e => !e.closest('[aria-hidden="true"]') && rendered(e);
  // A screen-reader copy of words drawn beside it hidden from screen readers (Amazon's price: "₹799.00" read aloud,
  // transparent; "₹799" drawn, aria-hidden): the text skips the drawing, so the copy's words are what the user sees.
  const twin = e => !e.closest('[aria-hidden="true"]') && !e.closest('[inert]') &&
    e.checkVisibility({checkVisibilityCSS: true}) && [...(e.parentElement?.children || [])]
      .some(s => s !== e && s.getAttribute('aria-hidden') === 'true' && rendered(s));
  // A see-through text input laid over drawn boxes (one-time-code widgets) is where typing goes.
  const typedThrough = e => e.tagName === 'INPUT' &&
    !['radio', 'checkbox', 'file', 'hidden', 'submit', 'button', 'image', 'reset'].includes(e.type) &&
    !e.closest('[inert]') && e.checkVisibility({checkVisibilityCSS: true}) &&
    e.getBoundingClientRect().width > 0 && e.getBoundingClientRect().height > 0;
  const reachable = e => rendered(e) || typedThrough(e);
  const byId = (e, id) => e.getRootNode().getElementById?.(id) || e.ownerDocument.getElementById(id);
  const UNSHOWN = ['style', 'script', 'noscript', 'template'];  // their text is code, never a name
  // A field the user types or picks into is never named by an alt attribute (only pictures are): Rapido's pickup box
  // carries alt="Drop" and its drop box alt="PickUp", while their placeholders say what each takes. A button input
  // (submit, button, reset) is no field: with no value, its alt names it ("Place order"), as an image input's does.
  const entry = e => e.tagName === 'TEXTAREA' || e.tagName === 'SELECT' ||
    (e.tagName === 'INPUT' && !['image', 'submit', 'button', 'reset'].includes(e.type));
  const name = (e, seen = new Set()) => {
    if (!e || seen.has(e)) return '';
    seen.add(e);
    const referenced = (e.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean)
      .map(id => name(byId(e, id), seen)).filter(Boolean).join(' ');
    return referenced || e.getAttribute('aria-label') ||
      [...(e.labels || [])].map(l => name(l, seen)).filter(Boolean).join(' ') ||
      (['button', 'submit', 'reset'].includes(e.type) ? e.value : '') || (entry(e) ? '' : e.getAttribute('alt')) ||
      (e.tagName === 'INPUT' ? '' : [...e.childNodes].map(n => n.nodeType === 3 ? n.textContent :
        n.nodeType === 1 && !UNSHOWN.includes(n.localName) && n.getAttribute('aria-hidden') !== 'true' ? name(n, seen) :
        '').join(' ').trim()) ||
      e.getAttribute('title') || e.getAttribute('placeholder') || '';
  };
  // A name a site wrote as markup ('<span class="…">Buy for </span>…' in an aria-label) reads as its words.
  const label = e => name(e).replace(/<\/?[a-z][^<>]*>/gi, ' ').replace(/\s+/g, ' ').trim().slice(0, 160);
  // A control with no name of its own (an icon button on a product card) is named by the nearest text around it.
  const context = (e, own = '') => {
    for (let a = e.parentElement, n = 0; a && n < 4; a = a.parentElement, n++) {
      const t = label(a);
      if (t && t !== own) return t.slice(0, 80);
    }
    return '';
  };
  // Countdowns ("Resend code in 0:29", "expires in 4:59", "29s left") tick without changing what a page means.
  const steady = t => String(t ?? '').replace(/\b(in|within)\s+\d{1,2}:\d{2}\b/gi, '$1 #:##')
    .replace(/\b\d{1,2}:\d{2}\s+(left|remaining)\b/gi, '#:## $1')
    .replace(/\b(in|within)\s+\d+\s?(s|secs?|seconds)\b/gi, '$1 # $2')
    .replace(/\b\d+\s?(s|secs?|seconds)\s+(left|remaining)\b/gi, '# $1 $2');
  // Text as a decision's freshness reads it: a rotating placeholder (Search "milk", then Search "bread") and the lines
  // it shows in turn are no change, so its quoted words are dropped and each distinct line counts once.
  const calm = t => [...new Set(steady(t).replace(/["\u201c][^"\u201d\n]{0,60}["\u201d]/g, '""').split('\n')
    .map(l => l.trim()).filter(Boolean))].join('\n');
  const roles = ['button', 'link', 'checkbox', 'radio', 'switch', 'tab', 'menuitem', 'menuitemradio',
    'menuitemcheckbox', 'option', 'gridcell', 'combobox', 'textbox', 'searchbox', 'spinbutton', 'slider', 'treeitem'];
  const semantic = 'a[href],button,input,textarea,select,summary,[contenteditable="true"],[contenteditable=""],' +
    '[contenteditable="plaintext-only"],' + roles.map(role => '[role="' + role + '"]').join(',');
  const TEXT_TYPES = ['text', 'email', 'url', 'tel', 'password', 'date', 'time', 'datetime-local', 'month', 'week'];
  const role = e => {
    const explicit = e.getAttribute('role');
    if (roles.includes(explicit)) return explicit;
    if (e.tagName === 'BUTTON' || e.tagName === 'SUMMARY') return 'button';
    if (e.tagName === 'A') return 'link';
    if (e.tagName === 'SELECT') return 'combobox';
    if (e.tagName === 'TEXTAREA' || e.isContentEditable) return 'textbox';
    if (e.tagName === 'INPUT') {
      if (['checkbox', 'radio'].includes(e.type)) return e.type;
      if (['button', 'submit', 'reset', 'image'].includes(e.type)) return 'button';
      if (e.type === 'search') return 'searchbox';
      if (e.type === 'number') return 'spinbutton';
      if (e.type === 'range') return 'slider';
      if (TEXT_TYPES.includes(e.type)) return 'textbox';
    }
    return null;
  };

  // Roots: this document, its open shadow roots, and same-origin frames, with offsets into this viewport.
  const roots = [];
  const addRoot = (root, dx, dy) => {
    roots.push({root, dx, dy});
    for (const e of root.querySelectorAll('*')) {
      if (e.shadowRoot) addRoot(e.shadowRoot, dx, dy);
      if (e.tagName === 'IFRAME' || e.tagName === 'FRAME') {
        let doc = null;
        try { doc = e.contentDocument; } catch {}
        if (doc?.body && visible(e)) {
          const r = e.getBoundingClientRect();
          addRoot(doc, dx + r.x + e.clientLeft, dy + r.y + e.clientTop);
        }
      }
    }
  };
  addRoot(document, 0, 0);
  cache.roots = roots.map(r => r.root);

  // Hit-test a point through shadow hosts and same-origin frames up to this window. Null when covered.
  cache.climb = (e, x, y) => {
    let cur = e;
    for (;;) {
      let root = cur.getRootNode(), target = cur;
      for (;;) {
        const hit = root.elementFromPoint(x, y);
        const own = hit && (hit === target || target.contains(hit) ||
          (target === e && [...(e.labels || [])].some(l => l === hit || l.contains(hit))));
        if (!own) return null;
        if (!root.host) break;
        target = root.host;
        root = target.getRootNode();
      }
      const frame = view(cur).frameElement;
      if (!frame) break;
      const r = frame.getBoundingClientRect();
      x += r.x + frame.clientLeft;
      y += r.y + frame.clientTop;
      cur = frame;
    }
    return x >= 0 && y >= 0 && x < innerWidth && y < innerHeight ? {x, y} : null;
  };
  cache.focused = () => {  // the control a page-level key press reaches, through shadow roots and same-origin frames
    let e = document.activeElement;
    for (;;) {
      if (e?.shadowRoot?.activeElement) e = e.shadowRoot.activeElement;
      else if (e?.tagName === 'IFRAME' && e.contentDocument?.activeElement) e = e.contentDocument.activeElement;
      else break;
    }
    return e && e.tagName !== 'BODY' && e.tagName !== 'HTML' ? label(e) : '';
  };
  // The words mini quoted inside a clickable container ("Add New" in an address card): the smallest element showing
  // exactly them, hit-tested. Null when none does.
  cache.spot = (id, words) => {
    const e = cache.nodes.get(id), want = words.replace(/\s+/g, ' ').trim().toLowerCase();
    if (!e?.isConnected) return null;
    const own = [e, ...e.querySelectorAll('*')].filter(d => rendered(d) && sized(d) &&
      (d.innerText || '').replace(/\s+/g, ' ').trim().toLowerCase() === want);
    const d = own[own.length - 1];
    if (!d) return null;
    const r = d.getBoundingClientRect();
    return cache.climb(d, r.x + r.width / 2, r.y + r.height / 2);
  };
  cache.locate = id => {
    const e = cache.nodes.get(id);
    if (!e?.isConnected || !reachable(e) || e.matches(':disabled') || e.closest('[aria-disabled="true"],[inert]'))
      return null;
    const r = e.getBoundingClientRect();
    return r.width && r.height ? cache.climb(e, r.x + r.width / 2, r.y + r.height / 2) : null;
  };

  const box = (e, dx, dy) => { const r = e.getBoundingClientRect(); return {x: r.x + dx, y: r.y + dy, w: r.width, h: r.height}; };
  const inView = r => {
    const x = r.x + r.w / 2, y = r.y + r.h / 2;
    return r.w > 0 && r.h > 0 && x >= 0 && y >= 0 && x < innerWidth && y < innerHeight;
  };
  const actions = [], options = [];  // dropdown options go last: the cap trims them first

  // Visible text first. Text asking to drag or drop a file marks its container as an upload target.
  const words = [], zones = new Set();
  const FILE_WORDS = /\b(files?|upload\w*|documents?|attach\w*|pdfs?|images?|photos?|pictures?|receipts?|statements?|resumes?|cvs?|browse|docx?|jpe?g|png|csv|xlsx?)\b/i;
  let length = 0;
  const dropZone = (e, text, dx, dy) => {
    for (let i = 0; e && i < 4; e = e.parentElement, i++) {
      const rect = box(e, dx, dy);
      if (rect.w >= 60 && rect.h >= 40) {
        if (!zones.has(e) && inView(rect) && cache.locate(identity(e))) {
          zones.add(e);
          actions.push({node: identity(e), kind: 'upload', drop: true, role: 'region',
            label: 'Drop zone: ' + text.replace(/\s+/g, ' ').slice(0, 80), rect});
        }
        return;
      }
    }
  };
  for (const {root, dx, dy} of roots) {
    const doc = root.ownerDocument || root, start = root.body || root;
    const walker = doc.createTreeWalker(start, NodeFilter.SHOW_TEXT), range = doc.createRange();
    let node;
    while ((node = walker.nextNode()) && length < 6000) {
      const value = node.textContent.trim(), parent = node.parentElement;
      if (!value || !parent || parent.closest('script,style,noscript,template') || !(visible(parent) || twin(parent)))
        continue;
      range.selectNodeContents(node);
      const r = range.getBoundingClientRect(), x = r.x + dx, y = r.y + dy;
      if (!(r.width > 0 && r.height > 0 && y + r.height > 0 && y < innerHeight && x + r.width > 0 && x < innerWidth)) continue;
      words.push(value);
      length += value.length;
      if (/\b(drag|drop)\b/i.test(value) && FILE_WORDS.test(value)) dropZone(parent, value, dx, dy);  // not "Drop-off"
    }
  }
  const text = words.join('\n').slice(0, 6000);

  const pointer = e => style(e).cursor === 'pointer';
  // ponytail: cursor/onclick/tabindex heuristic for React-style clickable divs; listeners added in JS are invisible.
  const extra = e => e.hasAttribute('onclick') || typeof e.onclick === 'function' ||
    (e.hasAttribute('tabindex') && e.tabIndex >= 0) || (pointer(e) && !(e.parentElement && pointer(e.parentElement)));
  // A clickable wrapper around a link or button of the same name (site headers) is that control, offered once.
  const sameInside = e => {
    const l = label(e);
    return !!l && [...e.querySelectorAll(semantic)].some(d => label(d) === l);
  };
  const scrollDirs = e => {
    const s = style(e), dirs = [], scrolls = v => /(auto|scroll|overlay)/.test(v);
    if (scrolls(s.overflowY) && e.scrollHeight > e.clientHeight + 2) {
      if (e.scrollTop > 0) dirs.push('up');
      if (e.scrollTop + e.clientHeight < e.scrollHeight - 2) dirs.push('down');
    }
    if (scrolls(s.overflowX) && e.scrollWidth > e.clientWidth + 2) {
      if (e.scrollLeft > 0) dirs.push('left');
      if (e.scrollLeft + e.clientWidth < e.scrollWidth - 2) dirs.push('right');
    }
    return dirs;
  };
  const ofCount = e => /\b\d+\s*of\s*\d+\s*$/i.test(label(e));  // "Code 1 of 4": one box of a code
  const codeBox = b => b.maxLength === 1 || ofCount(b);
  // A little unnamed box holding at most one character: a code's box on a site that sets no maxlength and no name.
  const bareBox = b => ['number', 'tel', 'text'].includes(b.type) && b.value.length <= 1 && label(b).length <= 1 &&
    (b.maxLength > 0 ? b.maxLength <= 2 : b.getBoundingClientRect().width > 0 && b.getBoundingClientRect().width <= 72);
  const inRow = boxes => {  // side by side, as a code's boxes are; a cart's quantity boxes stand one under another
    const middles = boxes.map(b => { const r = b.getBoundingClientRect(); return r.y + r.height / 2; });
    return Math.max(...middles) - Math.min(...middles) < 8;
  };
  // A text box the size of a code's box, in its row, is one of its boxes whatever its maxlength: MyUPI's first box
  // holds 6 characters so a pasted code lands, the five after it one each.
  const twinBox = (b, box) => {
    const r = b.getBoundingClientRect(), s = box.getBoundingClientRect();
    return ['number', 'tel', 'text', 'password'].includes(b.type) && Math.abs(r.width - s.width) < 2 &&
      Math.abs(r.height - s.height) < 2 && Math.abs(r.y - s.y) < 8;
  };
  const otpGroup = e => {  // 4 to 8 boxes of one kind in the same container are one code
    if (!codeBox(e) && !bareBox(e) && e.getBoundingClientRect().width > 72) return null;  // a wide field is no box
    for (const kind of [codeBox, bareBox]) {
      for (let p = e.parentElement, i = 0; p && i < 3; p = p.parentElement, i++) {
        const inputs = [...p.querySelectorAll('input')].filter(visible);
        const first = inputs.find(kind);
        const boxes = first ? inputs.filter(b => kind(b) || twinBox(b, first)) : [];
        if (boxes.includes(e) && boxes.length >= 4 && boxes.length <= 8 && (kind === codeBox || inRow(boxes)))
          return boxes;
      }
    }
    return null;
  };
  // A control that shows a picture and no words (a product's photo gallery, Amazon's thumbnails drawn as radios).
  const pictureOnly = e => e.tagName === 'IMG' || (!(e.innerText || '').trim() && !!e.querySelector('img'));
  const states = (e, base) => {
    for (const key of ['checked', 'selected', 'expanded']) {
      const value = e.getAttribute('aria-' + key);
      if (value !== null) base[key] = value;
    }
    if (['checkbox', 'radio'].includes(e.type)) base.checked = String(e.checked);
    const popup = e.getAttribute('aria-haspopup');
    if (popup && popup !== 'false') base.popup = popup;
    return base;
  };
  const constraints = e => {
    const c = {};
    if (e.tagName === 'INPUT') c.type = e.type;
    if (e.maxLength > 0) c.maxlength = e.maxLength;
    for (const key of ['inputmode', 'pattern', 'autocomplete']) if (e.getAttribute(key)) c[key] = e.getAttribute(key);
    return c;
  };
  const taken = new Set();
  const sized = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const wrapper = e => {
    for (let a = e.parentElement, n = 0; a && n < 3; a = a.parentElement, n++) {
      const r = a.getBoundingClientRect();
      if (rendered(a) && sized(a))  // control-sized, holding this one choice only (not a row of them)
        return r.width * r.height <= 120 * 120 && a.querySelectorAll('input[type=radio],input[type=checkbox]').length === 1
          ? a : null;
    }
    return null;
  };
  const within = e => { for (let p = e.parentElement; p; p = p.parentElement) if (taken.has(p)) return true; return false; };

  for (const {root, dx, dy} of roots) {
    for (const e of root.querySelectorAll('*')) {
      if (['HTML', 'HEAD', 'BODY', 'SCRIPT', 'STYLE'].includes(e.tagName)) continue;
      if (e.tagName === 'INPUT' && e.type === 'file') {
        if (!e.disabled) actions.push({node: identity(e), kind: 'upload', role: 'file',
          label: 'File input: ' + (label(e) || e.accept || 'file'), accept: e.accept, multiple: e.multiple});
        continue;
      }
      const isSemantic = e.matches(semantic);
      const clickable = !isSemantic && !within(e) && extra(e) && !sameInside(e);
      const draggable = e.getAttribute('draggable') === 'true';
      const dirs = scrollDirs(e);
      if (!isSemantic && !clickable && !draggable && !dirs.length) continue;
      // A styled radio or checkbox hides its input and shows its label, or a small wrapper holding the drawn circle
      // (Swiggy's size options): that is what a user clicks.
      const proxy = e.tagName === 'INPUT' && ['radio', 'checkbox'].includes(e.type) &&
        !(rendered(e) && sized(e) && inView(box(e, dx, dy))) ?
        [...(e.labels || [])].find(l => rendered(l) && sized(l)) || wrapper(e) : null;
      const at = proxy || e;
      if (!reachable(at) || e.matches(':disabled') || e.closest('[aria-disabled="true"]')) continue;
      const rect = box(at, dx, dy);
      if (!inView(rect) || (clickable && rect.w * rect.h > innerWidth * innerHeight / 2)) continue;
      const node = identity(at);
      if (!cache.locate(node)) continue;  // covered (banner, overlay) or clipped: a click could not reach it
      if (draggable) actions.push({node, kind: 'drag', role: 'draggable', label: label(e) || 'Draggable item', rect});
      if (dirs.length && rect.w >= 60 && rect.h >= 24) {  // short carousels and chip rows are ~30px tall
        const panel = e.getAttribute('aria-label') || label(e).slice(0, 60) || 'panel';
        for (const d of dirs) actions.push({node, kind: 'scroll_' + d, role: 'region', label: 'Panel: ' + panel, rect});
      }
      if (!isSemantic && !clickable) continue;
      const rname = role(e) || (clickable ? 'clickable' : null);
      if (!rname || (rname === 'gridcell' && e.querySelector('button,[role="button"]'))) continue;
      taken.add(e);
      const own = label(e), near = own ? '' : context(e);
      // A hidden name ("Make payment, tap to go to payment") keeps the words the button shows ("Proceed to Pay").
      // Never for a field: its words are what was typed into it (a code, a card number).
      const typing = e.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(e.tagName) ||
        ['textbox', 'searchbox', 'spinbutton', 'combobox'].includes(rname);
      const words = !typing && e.matches('[aria-label],[aria-labelledby]') ?
        (e.innerText || '').replace(/\s+/g, ' ').trim() : '';
      const shown = words && words.length <= 40 && !own.toLowerCase().includes(words.toLowerCase()) ? words : '';
      const base = states(e, {node, role: rname, label: shown ? `${own} (shows “${shown}”)` :
        own || (near ? `${rname} in “${near}”` : rname), name: own, rect, ...(shown ? {shown} : {}),
        ...(!typing && pictureOnly(e) ? {picture: true} : {})});
      if (e.tagName === 'SELECT') {
        const current = [...e.selectedOptions].map(o => o.label).join(', ');
        for (const o of e.options) if (!o.selected && !o.disabled && !o.closest('optgroup[disabled]'))
          options.push({...base, kind: 'select', value: o.value, current_value: current, label: base.label + ' → ' + o.label});
        continue;
      }
      const editable = !e.readOnly && e.getAttribute('aria-readonly') !== 'true' &&
        (['textbox', 'searchbox', 'spinbutton'].includes(rname) ||
          (rname === 'combobox' && ['INPUT', 'TEXTAREA'].includes(e.tagName)));
      if (!editable) {
        actions.push({...base, kind: 'click', value: 'value' in e && safe(e) ? String(e.value) :
          e.isContentEditable || rname === 'combobox' ? e.innerText.trim() : ''});
        continue;
      }
      const group = e.tagName === 'INPUT' ? otpGroup(e) : null;
      if (group && group[0] !== e) continue;  // one action for a whole one-time-code group
      const fill = {...base, kind: 'fill', constraints: constraints(e),
        value: e.type === 'password' ? (e.value ? '[filled]' : '') : 'value' in e ? String(e.value) : e.innerText.trim()};
      const form = e.form || e.closest('form');
      const submitter = form?.querySelector(
        'button:not([type=button]):not([type=reset]),input[type=submit],input[type=image]');
      if (submitter) fill.submit = label(submitter);  // what Enter in this field would press
      const typed = group ? group.map(b => b.value).join('') : '';
      if (group) Object.assign(fill, {group: group.map(identity), value: group.every(safe) ? typed : typed ? '[filled]' : '',
        label: (e.closest('[aria-label]')?.getAttribute('aria-label') || 'One-time code').replace(/\s*\d+\s*of\s*\d+\s*$/i, '')});
      actions.push(fill);
      // A field that opens a picker or a list also gets a click; a plain text field would only offer a decoy.
      const opens = ['date', 'time', 'datetime-local', 'month', 'week'].includes(e.type) || rname === 'combobox' ||
        e.hasAttribute('list') || e.readOnly || !!(e.getAttribute('aria-haspopup') || '').replace('false', '');
      if (opens) actions.push({...fill, kind: 'click', label: 'Open ' + fill.label, constraints: undefined, group: undefined});
    }
  }

  const fields = () => cache.roots.flatMap(r => [...r.querySelectorAll('input,textarea,select')]).filter(safe);
  cache.pageKey = () => [performance.timeOrigin, location.href, scrollX, scrollY, innerWidth, innerHeight,
    fields().map(e => [identity(e), e.value, e.checked, e.selectedIndex, e.disabled, e.readOnly])];
  cache.guard = (e, memo = new Map()) => {
    if (!e?.isConnected || !reachable(e)) return null;
    const scope = e.closest('form,dialog,[role="dialog"],article,li,tr,[role="row"]') || e.parentElement;
    if (scope && !memo.has(scope)) memo.set(scope, calm(scope.innerText?.slice(0, 6000)));
    return [identity(e), role(e), calm(name(e)), safe(e) ? e.value ?? null : null, e.checked ?? null, e.selectedIndex ?? null,
      e.readOnly ?? null, e.matches(':disabled'), e.getAttribute('aria-disabled'), e.getAttribute('aria-expanded'),
      e.getAttribute('aria-checked'), e.getAttribute('aria-selected'), e.getAttribute('href'), memo.get(scope) ?? ''];
  };
  // Fields that share a label ("Search for a location" for pickup and dropoff) are told apart, so nothing meant for
  // one is typed into the other: by placeholder where they have one, else by position. Buttons that share one ("ADD"
  // on every menu card) are told apart by the text around them.
  // The nearest ancestors whose text differs between same-named controls: each ADD's own menu card. Once two share
  // an ancestor nothing higher can tell them apart.
  const apart = els => {
    let at = els;
    for (let n = 0; n < 10 && at.every(Boolean); n++) {
      at = at.map(a => a.parentElement);
      if (!at.every(Boolean) || new Set(at).size < at.length) return null;
      const texts = at.map(a => label(a).slice(0, 80));
      if (texts.every(Boolean) && new Set(texts).size === texts.length) return texts;
    }
    return null;
  };
  // The heading of each one's own section ("Book 07:50 PM" under each movie's title): climb while each still has an
  // ancestor of its own, none inside another's, to the first level where each holds a different h1–h4.
  const sections = els => {
    let at = els;
    for (let n = 0; n < 12; n++) {
      at = at.map(a => a?.parentElement);
      if (!at.every(Boolean) || at.some((a, i) => at.some((b, j) => i !== j && a.contains(b)))) return null;
      const heads = at.map((a, i) => {
        const h = [...a.querySelectorAll('h1,h2,h3,h4,[role="heading"]')].find(h => !h.contains(els[i]));
        return h ? label(h).slice(0, 60) : '';
      });
      if (heads.every(Boolean) && new Set(heads).size === heads.length) return heads;
    }
    return null;
  };
  const told = els => {
    const texts = apart(els) || [], heads = sections(els);
    return heads ? heads.map((h, i) => !texts[i] ? h : texts[i].includes(h) ? texts[i] : `${h}: ${texts[i]}`) : texts;
  };
  // A card's own action ("Pause", "Cancel" on each mandate; "Remove" on each cart line) carries its card's title, even
  // when one card shows at a time (MyUPI): a bare "Cancel" was pressed for the wrong mandate. Lines of nothing but
  // such actions ("PauseCancel", the card's button row) are no title.
  const ACT = /^(?:cancel|pause|resume|remove|delete|edit|revoke|stop|manage)$/i;
  const ACTS = /cancel|pause|resume|remove|delete|edit|revoke|stop|manage/gi;
  const cardTitle = e => {
    for (let a = e?.parentElement, n = 0; a && n < 6; a = a.parentElement, n++) {
      const first = (a.innerText || '').split('\n').map(t => t.trim()).find(t => t.replace(ACTS, '').trim());
      if (first) return first.slice(0, 60);
    }
    return '';
  };
  for (const a of actions) {
    const title = a.kind === 'click' && ACT.test(a.label) ? cardTitle(cache.nodes.get(a.node)) : '';
    if (title) a.label += ` (in “${title}”)`;
  }
  const byLabel = {};
  for (const a of actions) if (a.kind === 'fill' || a.kind === 'click') (byLabel[a.kind + a.label] ||= []).push(a);
  for (const same of Object.values(byLabel)) {
    if (same.length < 2) continue;
    const els = same.map(a => cache.nodes.get(a.node));
    const hints = same[0].kind === 'fill' ? els.map(e => e?.placeholder) : told(els);
    // Pictures named only by their alt text (a product's photo gallery) say so: "1 of 3" alone reads like options.
    const pictured = e => !!e && (e.tagName === 'IMG' || (!(e.innerText || '').trim() && !!e.querySelector('img')));
    const kind = els.every(pictured) ? 'picture ' : '';
    same.forEach((a, i) => {
      const hint = hints[i], unique = hints.indexOf(hint) === hints.lastIndexOf(hint);
      a.label += ` (${hint && unique && !a.label.includes(hint) ? hint : `${kind}${i + 1} of ${same.length}`})`;
    });
  }
  // A slot named by its time alone ("07:50 PM") on a page of several sections' slots (a cinema's movies, a clinic's
  // doctors) carries its row's title: the first line of the nearest ancestor that starts with other text.
  const TIME = /\b\d{1,2}:\d{2}\s?(?:am|pm)?\b|\b\d{1,2}\.\d{2}\s?(?:am|pm)\b/i;  // never a price: 79.50
  const rowTitle = e => {
    const own = (e?.innerText || '').trim();
    for (let a = e?.parentElement, n = 0; a && n < 6; a = a.parentElement, n++) {
      const first = (a.innerText || '').split('\n').map(t => t.trim()).find(Boolean) || '';
      if (first && !own.includes(first) && !TIME.test(first)) return first.slice(0, 60);
    }
    return '';
  };
  const slots = actions.filter(a => a.kind === 'click' && TIME.test(a.name || a.label) && (a.name || a.label).length <= 40);
  const titles = slots.map(a => rowTitle(cache.nodes.get(a.node)));
  if (new Set(titles.filter(Boolean)).size >= 2)
    slots.forEach((a, i) => { if (titles[i] && !a.label.includes(titles[i])) a.label += ` (in “${titles[i]}”)`; });
  actions.push(...options);
  const omitted_actions = Math.max(0, actions.length - 250);
  actions.splice(250);
  const memo = new Map(), guards = {};
  for (const a of actions) if (!(a.node in guards)) guards[a.node] = cache.guard(cache.nodes.get(a.node), memo);
  const page_key = cache.pageKey();
  // Compare meaning and identity. Geometry is always resolved and hit-tested just before input.
  const semantics = actions.map(({rect, ...action}) => ({...action, label: calm(action.label)}));
  const marker = [performance.timeOrigin, location.href, scrollX, scrollY, innerWidth, innerHeight,
    document.title, calm(text), steady(JSON.stringify(semantics)), page_key[6]];
  actions.forEach((a, i) => a.id = 'e' + (i + 1));
  const height = document.documentElement.scrollHeight, width = document.documentElement.scrollWidth;
  const page = (direction, more) => more && actions.push(
    {id: 'page_scroll_' + direction, kind: 'scroll_' + direction, node: null, role: 'page', label: 'Page'});
  page('down', scrollY + innerHeight < height - 2);
  page('up', scrollY > 0);
  page('right', scrollX + innerWidth < width - 2);
  page('left', scrollX > 0);
  actions.push({id: 'wait', kind: 'wait', node: null, label: 'Wait for the page to update'});
  return {url: location.href, title: document.title, text, scroll: {x: scrollX, y: scrollY, height, width},
    actions, marker, page_key, guards, omitted_actions};
})()
