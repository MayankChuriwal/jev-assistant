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
  const byId = (e, id) => e.getRootNode().getElementById?.(id) || e.ownerDocument.getElementById(id);
  const name = (e, seen = new Set()) => {
    if (!e || seen.has(e)) return '';
    seen.add(e);
    const referenced = (e.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean)
      .map(id => name(byId(e, id), seen)).filter(Boolean).join(' ');
    return referenced || e.getAttribute('aria-label') ||
      [...(e.labels || [])].map(l => name(l, seen)).filter(Boolean).join(' ') ||
      (['button', 'submit', 'reset'].includes(e.type) ? e.value : '') || e.getAttribute('alt') ||
      (e.tagName === 'INPUT' ? '' : [...e.childNodes].map(n => n.nodeType === 3 ? n.textContent :
        n.nodeType === 1 && n.getAttribute('aria-hidden') !== 'true' ? name(n, seen) : '').join(' ').trim()) ||
      e.getAttribute('title') || e.getAttribute('placeholder') || '';
  };
  const label = e => name(e).replace(/\s+/g, ' ').trim().slice(0, 160);
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
  cache.locate = id => {
    const e = cache.nodes.get(id);
    if (!e?.isConnected || !rendered(e) || e.matches(':disabled') || e.closest('[aria-disabled="true"],[inert]')) return null;
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
      if (!value || !parent || parent.closest('script,style,noscript,template') || !visible(parent)) continue;
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
  const otpGroup = e => {
    for (let p = e.parentElement, i = 0; p && i < 3; p = p.parentElement, i++) {
      const boxes = [...p.querySelectorAll('input')].filter(b => (b.maxLength === 1 || ofCount(b)) && visible(b));
      if (boxes.length >= 4 && boxes.length <= 8) return boxes;
    }
    return null;
  };
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
    for (const key of ['inputmode', 'pattern']) if (e.getAttribute(key)) c[key] = e.getAttribute(key);
    return c;
  };
  const taken = new Set();
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
      if (!rendered(e) || e.matches(':disabled') || e.closest('[aria-disabled="true"]')) continue;
      const rect = box(e, dx, dy);
      if (!inView(rect) || (clickable && rect.w * rect.h > innerWidth * innerHeight / 2)) continue;
      const node = identity(e);
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
      const base = states(e, {node, role: rname, label: own || (near ? `${rname} in “${near}”` : rname), rect});
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
      const group = e.tagName === 'INPUT' && (e.maxLength === 1 || ofCount(e)) ? otpGroup(e) : null;
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
    if (!e?.isConnected || !rendered(e)) return null;
    const scope = e.closest('form,dialog,[role="dialog"],article,li,tr,[role="row"]') || e.parentElement;
    if (scope && !memo.has(scope)) memo.set(scope, steady(scope.innerText?.slice(0, 6000)));
    return [identity(e), role(e), steady(name(e)), safe(e) ? e.value ?? null : null, e.checked ?? null, e.selectedIndex ?? null,
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
  const byLabel = {};
  for (const a of actions) if (a.kind === 'fill' || a.kind === 'click') (byLabel[a.kind + a.label] ||= []).push(a);
  for (const same of Object.values(byLabel)) {
    if (same.length < 2) continue;
    const els = same.map(a => cache.nodes.get(a.node));
    const hints = same[0].kind === 'fill' ? els.map(e => e?.placeholder) : apart(els) || [];
    same.forEach((a, i) => {
      const hint = hints[i], unique = hints.indexOf(hint) === hints.lastIndexOf(hint);
      a.label += ` (${hint && unique && !a.label.includes(hint) ? hint : `${i + 1} of ${same.length}`})`;
    });
  }
  actions.push(...options);
  const omitted_actions = Math.max(0, actions.length - 250);
  actions.splice(250);
  const memo = new Map(), guards = {};
  for (const a of actions) if (!(a.node in guards)) guards[a.node] = cache.guard(cache.nodes.get(a.node), memo);
  const page_key = cache.pageKey();
  // Compare meaning and identity. Geometry is always resolved and hit-tested just before input.
  const semantics = actions.map(({rect, ...action}) => action);
  const marker = [performance.timeOrigin, location.href, scrollX, scrollY, innerWidth, innerHeight,
    document.title, steady(text), steady(JSON.stringify(semantics)), page_key[6]];
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
