// The chat page. WhatsApp Cloud API shapes in and out; every message text is set as text, never as HTML.
'use strict';

const token = document.querySelector('meta[name="jev-token"]').content;
const chat = document.getElementById('chat');
const form = document.getElementById('composer');
const input = document.getElementById('text');
const file = document.getElementById('file');
const status = document.getElementById('status');
const MAX_UPLOAD = 20 * 1024 * 1024;
let after = 0;
let sent = 0;
let secretFor = null; // the open secret question, as the server reports it: what is typed is hidden
let answered = null; // the secret question whose answer was just delivered

const api = (path, options = {}) => fetch(path, {...options, headers: {...options.headers, 'X-Jev-Token': token}});
const media = id => `/media/${encodeURIComponent(id)}?t=${encodeURIComponent(token)}`;

function el(tag, className, text) {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text !== undefined) e.textContent = text;
  return e;
}

function bubble(side) {
  const b = el('div', `bubble ${side}`);
  chat.append(b);
  return b;
}

function note(text) {
  chat.append(el('div', 'note', text));
  chat.scrollTop = chat.scrollHeight;
}

function body(target, text) {
  const title = /^\*([^*\n]+)\*\n/.exec(text); // WhatsApp bold: the task title when several tasks run
  if (title) {
    target.append(el('strong', 'title', title[1]));
    text = text.slice(title[0].length);
  }
  const p = el('p', 'text');
  let at = 0;
  for (const bold of text.matchAll(/\*\*?([^*\n]+?)\*\*?/g)) { // *bold* as WhatsApp shows it (and a stray **bold**)
    p.append(document.createTextNode(text.slice(at, bold.index)), el('strong', '', bold[1]));
    at = bold.index + bold[0].length;
  }
  p.append(document.createTextNode(text.slice(at)));
  target.append(p);
}

function choices(b, m) {
  const action = m.interactive.action;
  const kind = action.buttons ? 'button_reply' : 'list_reply';
  const options = action.buttons ? action.buttons.map(x => x.reply) : action.sections.flatMap(s => s.rows);
  const group = el('div', action.buttons ? 'buttons' : 'rows');
  for (const option of options) {
    const button = el('button', 'choice', option.title);
    button.type = 'button';
    if (option.description) button.append(el('small', '', option.description));
    button.addEventListener('click', () => {
      group.querySelectorAll('button').forEach(x => { x.disabled = true; });
      button.classList.add('picked');
      send({type: 'interactive', context: {id: m.x_id},
            interactive: {type: kind, [kind]: {id: option.id, title: option.title}}}, option.title);
    });
    group.append(button);
  }
  b.append(group);
}

function show(m) {
  const b = bubble('in');
  if (m.type === 'text') body(b, m.text.body);
  if (m.type === 'interactive') {
    body(b, m.interactive.body.text);
    choices(b, m);
  }
  if (m.type === 'image') {
    const img = el('img', 'shot');
    img.alt = 'Screenshot';
    img.src = media(m.image.id);
    b.append(img);
  }
  if (m.type === 'document') {
    const link = el('a', 'doc', '📄 ' + (m.document.filename || 'file'));
    link.href = media(m.document.id);
    link.download = m.document.filename || '';
    b.append(link);
  }
  if (m.x_expects && m.x_expects.file) document.body.classList.add('wants-file');
}

function mode() {
  input.type = secretFor === null ? 'text' : 'password';
  input.placeholder = secretFor === null ? 'Message' : 'Hidden answer, never sent to any model (or type cancel)';
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
  send({type: 'text', text: {body: text}}, question === null ? text : '••••').then(delivered => {
    if (delivered && question !== null && secretFor === question) { // stay hidden until the answer is in
      answered = question;
      secretFor = null;
      mode();
    }
  });
});

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

async function poll() {
  for (;;) {
    try {
      const r = await api(`/outbox?after=${after}&wait=25`);
      if (r.status === 403) { // a new launch of jev has a new token
        status.textContent = 'jev restarted: reload this page';
        return;
      }
      if (!r.ok) throw new Error(String(r.status));
      const {messages, typing, open} = await r.json();
      for (const m of messages) {
        show(m);
        after = Math.max(after, m.x_seq);
      }
      secretFor = open && open.secret && open.id !== answered ? open.id : null; // the server knows what is open
      mode();
      status.textContent = typing ? 'typing…' : 'online';
      if (messages.length) chat.scrollTop = chat.scrollHeight;
    } catch {
      status.textContent = 'offline, retrying';
      await new Promise(done => setTimeout(done, 2000));
    }
  }
}

poll();
