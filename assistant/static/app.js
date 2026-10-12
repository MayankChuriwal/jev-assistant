// The chat page, dressed as the Rivo app's AI chat on a phone. WhatsApp Cloud API shapes in and out; every message
// text is set as text, never as HTML.
'use strict';

const token = document.querySelector('meta[name="jev-token"]').content;
const chat = document.getElementById('chat');
const form = document.getElementById('composer');
const input = document.getElementById('text');
const file = document.getElementById('file');
const status = document.getElementById('status');
const MAX_UPLOAD = 20 * 1024 * 1024;
const BOLD = /\*\*?([^*\n]+?)\*\*?/g; // *bold* as WhatsApp shows it (and a stray **bold**)
const CALM = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
let after = 0;
let sent = 0;
let secretFor = null; // the open secret question, as the server reports it: what is typed is hidden
let answered = null; // the secret question whose answer was just delivered
let shown = Promise.resolve(); // replies show one after another, each typed out before the next

const api = (path, options = {}) => fetch(path, {...options, headers: {...options.headers, 'X-Jev-Token': token}});
const media = id => `/media/${encodeURIComponent(id)}?t=${encodeURIComponent(token)}`;

function el(tag, className, text) {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text !== undefined) e.textContent = text;
  return e;
}

function icon(name) {
  return document.getElementById('icons').content.querySelector(`[data-icon="${name}"]`).cloneNode(true);
}

function bubble(side) {
  document.querySelector('.starters')?.remove(); // the conversation has begun
  const b = el('div', `bubble ${side}`);
  chat.insertBefore(b, document.querySelector('.activity')); // “Processing” stays last
  return b;
}

function note(text) {
  chat.insertBefore(el('div', 'note', text), document.querySelector('.activity'));
  chat.scrollTop = chat.scrollHeight;
}

function rich(p, text) {
  let at = 0;
  for (const bold of text.matchAll(BOLD)) {
    p.append(document.createTextNode(text.slice(at, bold.index)), el('strong', '', bold[1]));
    at = bold.index + bold[0].length;
  }
  p.append(document.createTextNode(text.slice(at)));
}

function body(target, text, live) {
  const title = /^\*([^*\n]+)\*\n/.exec(text); // WhatsApp bold: the task title when several tasks run
  if (title) {
    target.append(el('strong', 'title', title[1]));
    text = text.slice(title[0].length);
  }
  const p = el('p', 'text');
  target.append(p);
  if (!live || CALM) {
    rich(p, text);
    return Promise.resolve();
  }
  const plain = text.replace(BOLD, '$1'); // typed out as the Rivo app types its latest reply, then shown in full
  const cursor = el('span', 'cursor', '▍');
  const started = Date.now();
  const ms = Math.min(1500, Math.max(300, plain.length * 18));
  return new Promise(done => {
    const timer = setInterval(() => {
      const n = Math.min(plain.length, Math.ceil(plain.length * (Date.now() - started) / ms));
      p.textContent = plain.slice(0, n);
      if (n < plain.length) {
        p.append(cursor);
      } else {
        clearInterval(timer);
        p.textContent = '';
        rich(p, text);
        done();
      }
      chat.scrollTop = chat.scrollHeight;
    }, 30);
  });
}

function choices(b, m) {
  const action = m.interactive.action;
  const kind = action.buttons ? 'button_reply' : 'list_reply';
  const options = action.buttons ? action.buttons.map(x => x.reply) : action.sections.flatMap(s => s.rows);
  const group = el('div', `choices ${action.buttons ? 'buttons' : 'rows'}`);
  for (const option of options) {
    const button = el('button', 'choice');
    button.type = 'button';
    const label = el('span', '', option.title);
    if (option.description) label.append(el('small', '', option.description));
    button.append(icon('return'), label);
    button.addEventListener('click', () => {
      group.querySelectorAll('button').forEach(x => { x.disabled = true; });
      button.classList.add('picked');
      send({type: 'interactive', context: {id: m.x_id},
            interactive: {type: kind, [kind]: {id: option.id, title: option.title}}}, option.title);
    });
    group.append(button);
  }
  b.after(group); // Rivo's quick-reply pills sit under the reply
}

function feedback(b) {
  const row = el('div', 'feedback');
  row.setAttribute('aria-hidden', 'true'); // shown as the app shows them; they do nothing here
  row.append(icon('copy'), icon('up'), icon('down'));
  b.after(row);
}

function mark() {
  const ring = el('span', 'mark');
  const img = el('img');
  img.src = '/rio-mark.png';
  img.alt = '';
  ring.append(img);
  return ring;
}

async function render(m, live) {
  if (m.x_status) { // a tool's progress line: the Rio mark and muted words, as the app shows what it is doing
    document.querySelector('.starters')?.remove();
    const line = el('div', 'status-line');
    line.append(mark(), el('span', '', m.text.body));
    chat.insertBefore(line, document.querySelector('.activity'));
    chat.scrollTop = chat.scrollHeight;
    return;
  }
  const b = bubble('in');
  if (m.type === 'text') await body(b, m.text.body, live);
  if (m.type === 'interactive') await body(b, m.interactive.body.text, live);
  if (m.type === 'image') {
    const img = el('img', 'shot');
    img.alt = 'Screenshot';
    img.addEventListener('load', () => { // a square picture is a QR: a card sized for the phone; its buttons in view
      if (Math.abs(img.naturalWidth - img.naturalHeight) <= img.naturalWidth * 0.1) img.classList.add('square');
      chat.scrollTop = chat.scrollHeight;
    });
    img.src = media(m.image.id);
    b.append(img);
  }
  if (m.type === 'document') {
    const link = el('a', 'doc', '📄 ' + (m.document.filename || 'file'));
    link.href = media(m.document.id);
    link.download = m.document.filename || '';
    b.append(link);
  }
  if (m.type === 'interactive') choices(b, m);
  if (m.type === 'text') feedback(b);
  if (m.x_expects && m.x_expects.file) document.body.classList.add('wants-file');
  chat.scrollTop = chat.scrollHeight;
}

function show(m, live) {
  shown = shown.then(() => render(m, live)).catch(() => {});
}

function working(on) {
  let row = document.querySelector('.activity');
  if (on && !row) {
    row = el('div', 'activity');
    row.append(mark(), el('span', '', 'Processing'));
    chat.append(row);
    chat.scrollTop = chat.scrollHeight;
  } else if (!on && row) {
    row.remove();
  }
}

function setStatus(text) {
  status.textContent = text;
  status.hidden = text === 'online' || text === 'typing…'; // only trouble shows: working shows as “Processing”
}

function mode() {
  input.type = secretFor === null ? 'text' : 'password';
  input.placeholder = secretFor === null ? 'Ask anything or type.…' : 'Hidden answer, never sent to any model (or type cancel)';
}

async function send(message, shown) {
  const own = bubble('out');
  own.append(el('p', 'text', shown));
  chat.scrollTop = chat.scrollHeight;
  sent += 1;
  const entry = {from: 'local', id: `wamid.in${Date.now()}.${sent}`, timestamp: String(Math.floor(Date.now() / 1000)),
                 ...message};
  const hook = {object: 'whatsapp_business_account', entry: [{id: 'local', changes: [{field: 'messages', value: {
    messaging_product: 'whatsapp', contacts: [{wa_id: 'local'}], messages: [entry]}}]}]};
  try {
    const r = await api('/webhook', {method: 'POST', headers: {'Content-Type': 'application/json'},
                                     body: JSON.stringify(hook)});
    if (!r.ok) throw new Error(String(r.status));
    return true;
  } catch {
    own.classList.add('failed');
    own.append(el('small', 'error', 'Not delivered. Is jev still running?'));
    return false;
  }
}

form.addEventListener('submit', event => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  const question = secretFor;
  input.value = '';
  form.classList.add('empty');
  send({type: 'text', text: {body: text}}, question === null ? text : '••••').then(delivered => {
    if (delivered && question !== null && secretFor === question) { // stay hidden until the answer is in
      answered = question;
      secretFor = null;
      mode();
    }
  });
});

input.addEventListener('input', () => form.classList.toggle('empty', !input.value.trim()));

document.querySelectorAll('.starter').forEach(card => card.addEventListener('click', () => {
  send({type: 'text', text: {body: card.dataset.say}}, card.dataset.say);
}));

file.addEventListener('change', async () => {
  const chosen = file.files[0];
  file.value = '';
  if (!chosen) return;
  if (chosen.size > MAX_UPLOAD) return note(`${chosen.name} is over 20 MB, so it can't be sent here.`);
  try {
    const r = await api('/media', {method: 'POST', body: chosen, headers: {
      'Content-Type': chosen.type || 'application/octet-stream', 'X-Filename': encodeURIComponent(chosen.name)}});
    if (!r.ok) throw new Error(String(r.status));
    const {id} = await r.json();
    const type = chosen.type.startsWith('image/') ? 'image' : 'document';
    document.body.classList.remove('wants-file');
    send({type, [type]: {id, filename: chosen.name, mime_type: chosen.type}}, '📎 ' + chosen.name);
  } catch {
    note(`${chosen.name} could not be sent.`);
  }
});

function clock() { // the phone's status bar shows the time as an iPhone does: 4:52
  const now = new Date();
  document.getElementById('clock').textContent = `${now.getHours() % 12 || 12}:${String(now.getMinutes()).padStart(2, '0')}`;
}

async function poll() {
  let live = false; // the first read brings what was already said, shown at once; from then on replies type out
  for (;;) {
    try {
      const r = await api(`/outbox?after=${after}&wait=${live ? 25 : 0}`);
      if (r.status === 403) { // a new launch of jev has a new token
        setStatus('jev restarted: reload this page');
        return;
      }
      if (!r.ok) throw new Error(String(r.status));
      const {messages, typing, open} = await r.json();
      for (const m of messages) {
        show(m, live);
        after = Math.max(after, m.x_seq);
      }
      secretFor = open && open.secret && open.id !== answered ? open.id : null; // the server knows what is open
      mode();
      setStatus(typing ? 'typing…' : 'online');
      working(typing);
      live = true;
      if (messages.length) chat.scrollTop = chat.scrollHeight;
    } catch {
      setStatus('offline, retrying');
      await new Promise(done => setTimeout(done, 2000));
    }
  }
}

clock();
setInterval(clock, 15000);
poll();
