/* AI Team Lite - chat.
   Messages and cards in the conversation, live events, the working indicator.
   Classic scripts loaded in order by index.html; they share globals (TOKEN, $, S, cur, view...). */
/* ------------------------------------------------------------------ chat rendering */
let items = [];            // events of the current conversation
/* ------------------------------------------------------------------ messages: times + attachments */
const CLIP_SM = '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M21.44 11.05 12.25 20.24a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>';
let lastUserTs = null, answered = true, deletedFiles = new Set();
function when(ts) {
  const d = new Date(ts * 1000), now = new Date();
  const time = d.toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'});
  return d.toDateString() === now.toDateString() ? time : d.toLocaleDateString([], {month: 'short', day: 'numeric'}) + ', ' + time;
}
function took(s) { s = Math.max(0, Math.round(s || 0)); return s < 60 ? `${s} s` : s < 3600 ? `${Math.floor(s / 60)} min ${s % 60} s` : `${Math.floor(s / 3600)} h ${Math.floor(s % 3600 / 60)} min`; }
const ATT_RE = /^\[Attached file: (inputs\/[^\]]+)\]$/gm;
const isImg = p => /\.(png|jpe?g|gif|webp)$/i.test(p);
function chipHtml(path, sent) {
  const gone = deletedFiles.has(path);
  const thumb = isImg(path) && !gone ? `<img class="thumb" src="${fileUrl(path)}" alt="">` : CLIP_SM;
  return `<span class="chip ${gone ? 'gone' : ''} ${isImg(path) && !gone ? 'img' : ''}" data-path="${esc(path)}" title="${esc(path)}">${thumb}${esc(path.split('/').pop())}${gone ? ' <span class="hint">(deleted)</span>' : `<button type="button" class="x" title="Delete this file" aria-label="Delete ${esc(path.split('/').pop())}">×</button>`}</span>`;
}
function wireChips(node, sent) {
  node.querySelectorAll('.chip img.thumb').forEach(i => i.onclick = () => lightbox(i.src));
  node.querySelectorAll('.chip .x').forEach(b => b.onclick = async () => {
    const path = b.closest('.chip').dataset.path, name = path.split('/').pop();
    if (sent && !confirm(`Delete ${name}?\n\nThe file is removed from the project and Fred is told not to use it anymore.`)) return;
    try {
      await api(`/api/projects/${cur}/delete-attachment`, {path, sent});
      if (sent) { deletedFiles.add(path); refreshChips(path); }
      else { attachments = attachments.filter(a => a !== path); renderChips(); }
    } catch (e) { alertBox(e.message); }
  });
}
function refreshChips(path) {
  $('msgs').querySelectorAll(`.uchips .chip[data-path="${CSS.escape(path)}"]`).forEach(c => { const n = el(chipHtml(path, true)); c.replaceWith(n); });
}
function userMsg(ev) {
  const files = [...String(ev.text).matchAll(ATT_RE)].map(m => m[1]);
  const text = String(ev.text).replace(ATT_RE, '').trim();
  lastUserTs = ev.ts; answered = false;
  const n = el(`<div class="uwrap">${text ? '<div class="m user"></div>' : ''}${files.length ? `<div class="uchips">${files.map(f => chipHtml(f, true)).join('')}</div>` : ''}
    <div class="meta">${ev.queued ? 'Queued · ' : ''}Sent ${esc(when(ev.ts))}</div></div>`);
  if (text) n.querySelector('.m').textContent = text;
  if (ev.queued) n.title = 'Delivered to Fred as soon as he finishes the current step';
  wireChips(n, true);
  return n;
}
function fredMsg(ev) {
  const who = ev.speaker || 'Fred', mat = who === 'Mat';
  const n = el(`<div><div class="who ${mat ? 'mat' : ''}">${mat ? 'Mat · lead architect' : esc(who)}</div><div class="m fred doc ${mat ? 'mat' : ''}"></div><div class="meta"></div></div>`);
  n.querySelector('.m').innerHTML = md(ev.text);
  let meta = when(ev.ts);
  if (!answered && lastUserTs) { meta += ` · replied after ${took(ev.ts - lastUserTs)}`; answered = true; }
  n.querySelector('.meta').textContent = meta;
  return n;
}

function renderAll() {
  items = view ? view.history.slice() : [];
  lastUserTs = null; answered = true;
  deletedFiles = new Set(items.filter(e => e.type === 'attachment_deleted').map(e => e.path));
  const box = $('msgs'); box.innerHTML = '';
  if (!cur) { box.innerHTML = welcomeHtml(); maybeInterrupted(); return; }
  items.forEach(ev => box.appendChild(renderEvent(ev)));
  groupActivities(); scrollDown(true);
}
function maybeInterrupted() {
  const items = (S?.interrupted || []).filter(x => !dismissedInterrupt.has(x.slug));
  if (!items.length) return;
  const box = $('msgs');
  const when = t => t ? new Date(t * 1000).toLocaleString() : '';
  const b = el(`<div class="banner"><h3>⚠ Work was interrupted</h3>
    <div class="hint">AI Team Lite did not close cleanly last time (closed during work, a crash, or a Windows update). Nothing committed was lost. Pick up where each project stopped:</div>
    ${items.map(x => `<div class="irow" data-s="${esc(x.slug)}"><div style="flex:1"><b>${esc(x.name || x.slug)}</b>${x.iteration ? ` <span class="hint">· iteration ${x.iteration}${x.iteration_name ? ' (' + esc(x.iteration_name) + ')' : ''}${x.task ? ', ' + esc(x.task) : ''}</span>` : ''}
      <div class="hint">stopped ${esc(when(x.time))}${(x.changes || []).length ? ` · ${x.changes.length} file(s) changed, not committed` : ''}</div></div>
      <button class="primary" data-a="resume">Resume</button><button data-a="restart">Restart the step</button><button data-a="dismiss">Leave paused</button></div>`).join('')}
    <label class="row" style="margin-top:8px"><input type="checkbox" id="autoResume" ${S?.settings.auto_resume ? 'checked' : ''} style="width:auto"> Resume interrupted work automatically next time</label></div>`);
  box.prepend(b);
  b.querySelector('#autoResume').onchange = e => api('/api/settings', {auto_resume: e.target.checked});
  b.querySelectorAll('.irow').forEach(row => {
    const slug = row.dataset.s;
    row.querySelectorAll('[data-a]').forEach(btn => btn.onclick = async () => {
      if (btn.dataset.a === 'restart' && !confirm('Restart the current step from the last commit? Unfinished changes are set aside (recoverable with Git), not deleted.')) return;
      row.querySelectorAll('button').forEach(x => x.disabled = true);
      try { await api(`/api/interrupted/${slug}/${btn.dataset.a}`, {}); } catch (e) { alertBox(e.message); }
      dismissedInterrupt.add(slug); S.interrupted = (S.interrupted || []).filter(x => x.slug !== slug);
      row.remove(); if (!b.querySelector('.irow')) b.remove();
      if (btn.dataset.a !== 'dismiss') { await loadState(); open(slug); }
    });
  });
}
let dismissedInterrupt = new Set();
function welcomeHtml() {
  return `<div class="welcome"><h2>Welcome back${S?.settings.user_name ? ', ' + esc(S.settings.user_name) : ''}</h2>
  <p>Pick a project on the left, or click <b>+ New project</b> and tell Fred your idea.</p></div>`;
}
function scrollDown(force) { const b = $('msgs'); if (force || b.scrollHeight - b.scrollTop - b.clientHeight < 240) b.scrollTop = b.scrollHeight; }

function el(html) { const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstChild; }
function renderEvent(ev) {
  let node;
  switch (ev.type) {
    case 'message': node = ev.role === 'user' ? userMsg(ev) : fredMsg(ev); break;
    case 'attachment_deleted': deletedFiles.add(ev.path); refreshChips(ev.path); node = el(`<div class="notice">You deleted ${esc(ev.path.split('/').pop())}</div>`); break;
    case 'activity':
      node = el(`<div class="act" data-act="1"><span>${ev.who === 'Fred' ? '' : esc(ev.who) + ' · '}${esc(ev.text)}</span></div>`); break;
    case 'agent': node = agentCard(ev); break;
    case 'approval': node = approvalCard(ev); break;
    case 'secret': node = secretCard(ev); break;
    case 'folder': node = folderCard(ev); break;
    case 'choice': node = choiceCard(ev); break;
    case 'plan': node = planCard(ev); break;
    case 'architecture': node = archCard(ev); break;
    case 'change': node = changeCard(ev); break;
    case 'council': node = councilCard(ev); break;
    case 'prior_art': node = priorArtCard(ev); break;
    case 'problem': node = el(`<div class="card"><h4>🎯 The problem we are solving</h4>${problemHtml(ev)}</div>`); break;
    case 'challenge': node = challengeCard(ev); break;
    case 'questions': node = el(`<div class="card" style="border-color:var(--accent)"><h4>🧭 The questions that changed your project</h4>${questionsHtml(ev)}</div>`); break;
    case 'diagram': node = el(`<div class="itcard" style="border-left-color:var(--acc)"><b>🗺 New diagram: ${esc(ev.title)}</b>
      <div class="hint">${esc(ev.question || '')}</div><div class="acts" style="margin-top:6px"><button class="primary" data-open>Open</button>${ev.compare ? '<button data-cmp>What changed</button>' : ''}</div></div>`);
      node.querySelector('[data-open]').onclick = () => openDiagram(cur, ev.id, 'html', ev.title);
      if (ev.compare) node.querySelector('[data-cmp]').onclick = () => openDiagram(cur, ev.id, 'compare', ev.title);
      break;
    case 'release_wait': node = el(`<div class="itcard" style="border-left-color:var(--warn)"><b>⏸ Iteration ${ev.n} is waiting for your release:</b> ${esc(ev.name)}
      <div class="acts" style="margin-top:6px"><button class="primary" data-rel>Release iteration ${ev.n}</button><span class="hint">or later from the Pipeline screen</span></div></div>`);
      node.querySelector('[data-rel]').onclick = async b => { try { await api(`/api/projects/${cur}/release`, {n: ev.n}); b.target.disabled = true; b.target.textContent = 'Released'; } catch (e) { alertBox(e.message); } }; break;
    case 'handoff': node = el(`<div class="handoff"><span class="av ${ev.from === 'Mat' ? 'mat' : 'fred'}">${esc(ev.from[0])}</span>→<span class="av ${ev.to === 'Mat' ? 'mat' : 'fred'}">${esc(ev.to[0])}</span>
      <div><b>${ev.to === 'Mat' ? 'Fred handed over to Mat, the lead architect' : 'Mat handed back to Fred'}</b><div class="hint">${ev.to === 'Mat' ? 'Mat asks how things should work (data, storage, security, where it runs) with his recommendations, then shows you the architecture to approve.' : esc(ev.summary || '')}</div></div></div>`); break;
    case 'screenshot':
      node = el(`<div class="card shot"><h4>📸 Screenshot</h4><div class="hint">${esc(ev.caption || '')}</div><img alt="" src="${fileUrl(ev.path)}"></div>`);
      node.querySelector('img').onclick = () => lightbox(fileUrl(ev.path)); break;
    case 'launched': node = el(`<div class="card"><h4>▶ Your app was started</h4><div class="hint">${esc(ev.command)}${ev.url ? ' · ' + esc(ev.url) : ''}. Try it, then tell Fred what you think.</div></div>`); break;
    case 'setup_done': node = el(`<div class="card" style="border-color:var(--ok)"><h4>✅ Setup complete</h4><div class="doc">${md(ev.summary || '')}</div><div class="acts"><button class="primary">+ Start my first project</button></div></div>`);
      node.querySelector('button').onclick = () => $('newBtn').click(); break;
    case 'error': node = el(`<div class="err"></div>`); node.textContent = '⚠ ' + ev.text; break;
    case 'notice': node = el(`<div class="notice">${esc(ev.text)}</div>`); break;
    case 'limit': node = limitCard(ev); break;
    case 'watch': node = el(`<div class="notice watch">⏱ ${esc(ev.text)}</div>`); break;
    case 'headroom': node = el(`<div class="itcard" style="border-left-color:var(--warn)"><b>Your Claude plan:</b> ${esc(ev.text)} <a href="#" data-u>See usage</a></div>`);
      node.querySelector('[data-u]').onclick = e => { e.preventDefault(); UQ.scope = null; openUsage(); }; break;
    case 'stuck': node = stuckCard(ev); break;
    case 'telegram': node = phoneCard(ev); break;
    case 'iteration': node = ev.status === 'done'
      ? el(`<div class="itcard done"><b>✅ Iteration ${ev.n}${ev.total ? ' of ' + ev.total : ''} committed:</b> ${esc(ev.name)} <span class="hint">· tests passed · commit <code>${esc(ev.commit || '')}</code></span>${ev.summary ? `<div class="hint">${esc(ev.summary)}</div>` : ''}</div>`)
      : el(`<div class="itcard"><b>▶ Iteration ${ev.n}${ev.total ? ' of ' + ev.total : ''} started:</b> ${esc(ev.name)}${(ev.requirements || []).length ? ` <span class="hint">· ${esc(ev.requirements.join(', '))}</span>` : ''}</div>`); break;
    default: node = document.createComment(ev.type);
  }
  if (ev.via === 'phone' && node.querySelector) node.querySelector('h4')?.insertAdjacentHTML('beforeend', ' <span class="pill acc">📱 from your phone</span>');
  if (ev.id && node.dataset) node.dataset.id = ev.id;
  return node;
}
const fileUrl = p => `/files/${cur}/${encodeURI(p)}?token=${TOKEN}`;
function statusPill(s) {
  const cls = {approved: 'ok', saved: 'ok', answered: 'ok', done: 'ok', declined: 'bad', failed: 'bad', cancelled: '', expired: '', 'changes requested': 'warn', pending: 'acc', working: 'acc'}[s] || '';
  return `<span class="pill ${cls}">${esc(s === 'pending' ? 'waiting for you' : s)}</span>`;
}
function agentCard(ev) {
  const st = ev.status || 'working';
  const receipt = /Async agent launched/i.test(ev.summary || '');  // engine bookkeeping from 0.6.0, never a report
  const n = el(`<div class="agent ${st}"><div class="row"><b>${esc(ev.name)}</b>${st === 'working' ? `<span class="spin"></span><span class="hint">working${ev.background ? ' in the background (AI Team Lite brings its report to Fred as soon as it finishes)' : ''}…</span>` : receipt ? '<span class="pill">started in the background</span>' : statusPill(st)}</div>
    <div class="hint">${esc(ev.description || '')}</div>${ev.summary && !receipt ? `<details><summary class="hint" style="cursor:pointer">Report</summary><div class="doc">${md(ev.summary)}</div></details>` : ''}</div>`);
  return n;
}
function approvalCard(ev) {
  const pend = ev.status === 'pending';
  const n = el(`<div class="card ${pend ? 'pending' : ''}"><h4>🔐 ${esc(ev.title)} ${statusPill(ev.status)} ${ev.risk ? `<span class="risk ${esc(ev.risk)}">${esc(ev.risk)} risk</span>` : ''}</h4>
    <div><span class="hint">${esc(ev.who || 'Fred')} wants to do this${ev.reason ? ' · ' + esc(ev.reason) : ''}</span></div>
    ${ev.what ? `<div class="what"><b>What this does:</b> ${esc(ev.what)}</div>` : ''}
    ${ev.risk_reason ? `<div class="what"><b>Risk:</b> ${esc(ev.risk_reason)}</div>` : ''}
    ${ev.says ? `<div class="says doc"><span class="hint">${esc(ev.who || 'Fred')} explained:</span> ${md(ev.says)}</div>` : (ev.explanation ? `<div class="says">${inline(ev.explanation)}</div>` : '')}
    ${ev.details ? `<details ${pend ? 'open' : ''}><summary class="hint" style="cursor:pointer">Exact command / change</summary><pre></pre></details>` : ''}
    ${pend ? `<div class="acts"><button class="primary" data-a="yes">Approve</button><button data-a="no">Decline</button><input type="text" placeholder="Optional note for Fred" style="flex:1;min-width:160px"></div>` : (ev.note ? `<div class="hint">Your note: ${esc(ev.note)}</div>` : '')}
  </div>`);
  if (ev.details) n.querySelector('pre').textContent = ev.details;
  if (pend) n.querySelectorAll('button').forEach(b => b.onclick = () => respond(ev.id, {approved: b.dataset.a === 'yes', note: n.querySelector('input').value}, n));
  return n;
}
/* L17: linking the phone during setup */
function phoneCard(ev) {
  const st = ev.state || 'linking';
  const n = el(`<div class="card ${st === 'linking' ? 'pending' : ''}"><h4>📱 Link your phone ${st === 'linked' ? '<span class="pill ok">linked</span>' : st === 'expired' ? '<span class="pill">code expired</span>' : ''}</h4>
    ${st === 'linking' ? `<ol style="margin:4px 0;padding-left:20px"><li>On your phone, open <a href="${esc(ev.url)}" target="_blank" rel="noopener">${esc('@' + (ev.bot || 'your bot'))}</a> in Telegram and tap <b>Start</b>.</li>
      <li>Send this code to the bot: <b style="font-size:20px;letter-spacing:3px">${esc(ev.code)}</b></li></ol><div class="hint"><span class="spin"></span> Waiting for the code (valid 10 minutes)…</div>`
      : st === 'linked' ? `<div>Your phone is linked to ${esc('@' + (ev.bot || 'your bot'))}. Approvals and alerts now arrive there too.</div>` : '<div class="hint">The code was not received in time. Ask Fred to try again.</div>'}</div>`);
  return n;
}
/* L8: the watchdog says plainly when nothing moves, and offers help */
function stuckCard(ev) {
  const open = ev.status === 'open';
  const n = el(`<div class="card ${open ? 'pending' : ''}" style="${open ? 'border-color:var(--warn);box-shadow:0 0 0 3px var(--warnbg)' : ''}">
    <h4>⏱ Nothing is moving ${open ? '' : '<span class="pill ok">' + esc(ev.status === 'resolved' ? 'moving again' : ev.status) + '</span>'}</h4>
    <div>${esc(ev.text)}</div>
    ${open ? `<div class="acts"><button class="primary" data-a="wake">Wake ${esc(leadName())} up</button><button data-a="restart">Stop and restart this step</button></div>
      <div class="hint">Waking him up asks him to check what is going on and continue. Restarting stops the current step and starts it again from the beginning.</div>` : ''}
    ${ev.note ? `<div class="hint">${esc(ev.note)}</div>` : ''}</div>`);
  if (open) n.querySelectorAll('[data-a]').forEach(b => b.onclick = async () => {
    if (b.dataset.a === 'restart' && !confirm('Stop the current step and start it again from the beginning?')) return;
    n.querySelectorAll('button').forEach(x => x.disabled = true);
    try { await api(`/api/projects/${cur}/${b.dataset.a === 'wake' ? 'wake' : 'restart-step'}`, {}); }
    catch (e) { n.querySelectorAll('button').forEach(x => x.disabled = false); alertBox(e.message); }
  });
  return n;
}
function limitCard(ev) {
  const fmt = s => new Date(s * 1000).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
  const waiting = ev.status === 'waiting';
  const n = el(`<div class="card ${waiting ? 'pending' : ''}" style="${waiting ? 'border-color:var(--warn);box-shadow:0 0 0 3px var(--warnbg)' : ''}">
    <h4>⏸ Work stopped: plan usage limit reached ${waiting ? '' : statusPill(ev.status)}</h4>
    <div>Your Claude plan's ${esc((ev.label || 'usage').toLowerCase())} limit was reached, so Fred and the team stopped. ${ev.resets_at ? `It resets at <b>${fmt(ev.resets_at)}</b>.` : 'The reset time is not known.'}</div>
    ${waiting && ev.auto && ev.continue_at ? `<div class="hint" data-cd style="margin-top:4px"></div>` : ''}
    ${waiting ? `<div class="acts"><button class="primary" data-a="go">Continue now</button>${ev.auto ? '<button data-a="cancel">Cancel auto-continue</button>' : ''}</div>` : ''}
    ${ev.note ? `<div class="hint">${esc(ev.note)}</div>` : ''}</div>`);
  if (waiting) {
    n.querySelector('[data-a=go]').onclick = () => api(`/api/projects/${cur}/limit-continue`, {});
    const c = n.querySelector('[data-a=cancel]'); if (c) c.onclick = () => api(`/api/projects/${cur}/limit-cancel`, {});
    const cd = n.querySelector('[data-cd]');
    if (cd) { const tick = () => { const s = Math.max(0, Math.round(ev.continue_at - Date.now() / 1000)); cd.textContent = s > 0 ? `Continues automatically in ${Math.floor(s / 3600) ? Math.floor(s / 3600) + ' h ' : ''}${Math.floor(s % 3600 / 60)} min ${s % 60} s (about 10 seconds after the reset).` : 'Continuing…'; if (s > 0 && document.body.contains(cd)) setTimeout(tick, 1000); }; setTimeout(tick, 0); }
  }
  return n;
}
function secretCard(ev) {
  const pend = ev.status === 'pending';
  const n = el(`<div class="card ${pend ? 'pending' : ''}"><h4>🔑 ${esc(ev.title)} ${statusPill(ev.status)}</h4>
    ${ev.explanation ? `<div class="doc">${md(ev.explanation)}</div>` : ''}
    ${ev.help_url ? `<div><a href="${esc(ev.help_url)}" target="_blank" rel="noopener">Open the page in your browser ↗</a></div>` : ''}
    ${pend ? `<div class="acts"><input type="password" placeholder="Paste here" autocomplete="off" style="flex:1"><button class="primary" data-a="save">Save securely</button><button data-a="skip">Skip</button></div><div class="hint">Saved in Windows Credential Manager. The AI never sees it.</div>` : ''}
  </div>`);
  if (pend) {
    const inp = n.querySelector('input');
    n.querySelector('[data-a=save]').onclick = () => { if (inp.value.trim()) respond(ev.id, {value: inp.value.trim()}, n); };
    n.querySelector('[data-a=skip]').onclick = () => respond(ev.id, {cancelled: true}, n);
    inp.onkeydown = e => { if (e.key === 'Enter') n.querySelector('[data-a=save]').click(); };
  }
  return n;
}
function folderCard(ev) {
  const pend = ev.status === 'pending';
  const n = el(`<div class="card ${pend ? 'pending' : ''}"><h4>📁 ${esc(ev.title)} ${statusPill(ev.status)}</h4>
    ${ev.explanation ? `<div class="doc">${md(ev.explanation)}</div>` : ''}
    ${pend ? `<div class="acts"><input type="text" style="flex:1" value="${esc(ev.suggested || '')}">${S?.desktop ? '<button data-a="browse">Browse…</button>' : ''}<button class="primary" data-a="ok">Use this folder</button></div>`
          : (ev.value ? `<div class="hint">Chosen: <code>${esc(ev.value)}</code></div>` : '')}
  </div>`);
  if (pend) {
    const inp = n.querySelector('input');
    const b = n.querySelector('[data-a=browse]'); if (b) b.onclick = async () => { const r = await api('/api/pick-folder', {start: inp.value}); if (r.path) inp.value = r.path; };
    n.querySelector('[data-a=ok]').onclick = () => { if (inp.value.trim()) respond(ev.id, {value: inp.value.trim()}, n); };
  }
  return n;
}
function choiceCard(ev) {
  const pend = ev.status === 'pending';
  const n = el(`<div class="card ${pend ? 'pending' : ''}"><h4>❓ ${inline(ev.title)} ${statusPill(ev.status)}</h4>
    ${pend ? `<div class="acts">${(ev.options || []).map((o, i) => `<button data-i="${i}" title="${esc((ev.hints || [])[i] || '')}">${esc(o)}</button>`).join('')}</div>
      ${(ev.hints || []).some(Boolean) ? `<ul class="hint" style="margin:4px 0;padding-left:18px">${ev.options.map((o, i) => (ev.hints[i] ? `<li><b>${esc(o)}</b>: ${esc(ev.hints[i])}</li>` : '')).join('')}</ul>` : ''}
      <div class="hint">${ev.note ? esc(ev.note) + ' ' : ''}Or type your own answer in the box below.</div>` : (ev.value ? `<div class="hint">You answered: ${esc(ev.value)}</div>` : '')}</div>`);
  if (pend) n.querySelectorAll('button').forEach(b => b.onclick = () => respond(ev.id, {value: ev.options[+b.dataset.i]}, n));
  return n;
}
function planCard(ev) {
  const pend = ev.status === 'pending';
  const its = (ev.iterations || []).filter(i => i.status !== 'done');
  const n = el(`<div class="card ${pend ? 'pending' : ''}"><h4>🗺 ${esc(ev.title || 'Plan of action')} ${statusPill(ev.status)}</h4>
    <div class="doc">${md(ev.markdown)}</div>
    ${pend && its.length ? `<div style="margin-top:8px"><b>When each iteration is finished</b><table class="mini" style="margin-top:4px">${its.map((it, i) => `<tr><td>${it.n}. ${esc(it.name)}</td><td style="width:46%">${i === its.length - 1 ? '<span class="hint">last iteration</span>' : `<select data-after="${it.n}"><option value="continue" ${it.after !== 'pause' ? 'selected' : ''}>Continue automatically</option><option value="pause" ${it.after === 'pause' ? 'selected' : ''}>Pause for my release</option></select>`}</td></tr>`).join('')}</table></div>` : ''}
    ${objectionsHtml(ev.objections)}
    ${pend ? `<div class="acts"><button class="primary" data-a="yes">Approve plan and start iteration ${((ev.iterations || []).find(i => i.status !== 'done') || {n: 1}).n}</button><button data-a="no">Request changes</button></div>
      <div data-fb hidden style="margin-top:8px"><textarea rows="3" placeholder="What should change?"></textarea><div class="acts"><button class="primary" data-a="send">Send to Fred</button></div></div>`
      : (ev.feedback ? `<div class="hint">Your feedback: ${esc(ev.feedback)}</div>` : '')}
  </div>`);
  if (pend) {
    n.querySelector('[data-a=yes]').onclick = () => { const after = {}; n.querySelectorAll('[data-after]').forEach(x => after[x.dataset.after] = x.value); respond(ev.id, {approved: true, after}, n); };
    n.querySelector('[data-a=no]').onclick = () => { n.querySelector('[data-fb]').hidden = false; n.querySelector('textarea').focus(); };
    n.querySelector('[data-a=send]').onclick = () => respond(ev.id, {approved: false, feedback: n.querySelector('textarea').value}, n);
  }
  return n;
}
function archCard(ev) {
  const pend = ev.status === 'pending';
  const n = el(`<div class="card arch ${pend ? 'pending' : ''}"><h4>🏛 ${esc(ev.title || 'Architecture')} ${statusPill(ev.status)}</h4>
    <div class="hint">Mat, the lead architect, proposes how the software will be built. Nothing is planned or built until you approve it.</div>
    <table class="dec"><tr><th>Decision</th><th>What we do</th><th>Why</th></tr>${(ev.decisions || []).map(d => `<tr><td><b>${esc(d.topic)}</b></td><td>${esc(d.decision)}</td><td class="hint">${esc(d.reason)}</td></tr>`).join('')}</table>
    ${objectionsHtml(ev.objections)}<details><summary class="hint" style="cursor:pointer">Full architecture document</summary><div class="doc">${md(ev.markdown)}</div></details>
    ${pend ? `<div class="acts"><button class="primary" data-a="yes">Approve architecture</button><button data-a="no">Request changes</button></div>
      <div data-fb hidden style="margin-top:8px"><textarea rows="3" placeholder="What should change? (e.g. refresh every hour instead)"></textarea><div class="acts"><button class="primary" data-a="send">Send to Mat</button></div></div>`
      : (ev.feedback ? `<div class="hint">Your feedback: ${esc(ev.feedback)}</div>` : '')}
  </div>`);
  if (pend) {
    n.querySelector('[data-a=yes]').onclick = () => respond(ev.id, {approved: true}, n);
    n.querySelector('[data-a=no]').onclick = () => { n.querySelector('[data-fb]').hidden = false; n.querySelector('textarea').focus(); };
    n.querySelector('[data-a=send]').onclick = () => respond(ev.id, {approved: false, feedback: n.querySelector('textarea').value}, n);
  }
  return n;
}

function objectionsHtml(objs) {
  if (!objs || !objs.length) return '';
  return `<details style="margin-top:6px"><summary class="hint" style="cursor:pointer">Devil's advocate: ${objs.length} objection(s) considered</summary><ul class="obj">${objs.map(o => `<li><b>${esc(o.objection)}</b><br>${esc(o.answer)}</li>`).join('')}</ul></details>`;
}
/* ------------------------------------------------------------------ item 79: problem first, challenge, proof */
const PROBLEM_FIELDS = [['who', 'Who is affected'], ['how_often', 'How often'], ['cost_today', 'What it costs today'], ['how_now', 'How it is done now'], ['success', 'What success looks like']];
const PROBLEM_WHY = () => S?.why?.problem || {};  // the texts come from the app (challenge.PROBLEM_WHY)
const whyHtml = t => t ? `<details class="why"><summary>Why Fred asked this</summary><div>${esc(t)}</div></details>` : '';
function problemHtml(pr) {
  pr = pr || {};
  const miss = PROBLEM_FIELDS.filter(([k]) => !(pr[k] || '').trim());
  return `${pr.statement ? `<div style="margin-bottom:4px"><b>${esc(pr.statement)}</b></div>` : ''}<table class="mini">${PROBLEM_FIELDS.map(([k, l]) => `<tr><td style="width:38%" class="hint">${l}${whyHtml(PROBLEM_WHY()[k])}</td><td>${pr[k] ? esc(pr[k]) : '<span class="hint">not known yet</span>'}</td></tr>`).join('')}</table>
    <div class="hint" style="margin-top:4px">${miss.length ? `Fred still needs: ${miss.map(m => m[1].toLowerCase()).join(', ')}.` : '✓ Complete. Fred checks the idea against it before writing requirements.'}</div>`;
}
const CH_KIND = {objection: ['🤔', 'Objection'], contradiction: ['⚖️', 'Contradiction'], assumption: ['❓', 'Assumption to confirm'], clarification: ['🔍', 'Needs clarification']};
const CH_OPTS = {objection: [['change', 'Change it'], ['keep', 'Keep as is'], ['accept', 'Accept the risk']], contradiction: [['change', 'Change it'], ['keep', 'Keep as is'], ['accept', 'Accept the risk']],
  assumption: [['confirm', 'Correct'], ['correct', 'Not correct']], clarification: [['answer', 'Answer']]};
const CH_WORD = {change: 'change it', keep: 'keep as is', accept: 'accept the risk', confirm: 'confirmed', correct: 'not correct', answer: 'answered'};
function challengeCard(ev) {
  const pend = ev.status === 'pending', ans = ev.answers || {};
  const items = (ev.findings || []).map(f => {
    const [ic, label] = CH_KIND[f.kind] || CH_KIND.objection, a = ans[f.id] || {};
    const reqs = (f.requirements || []).length ? ` <span class="pill acc">${esc(f.requirements.join(', '))}</span>` : '';
    return `<div class="chf" data-f="${esc(f.id)}" data-kind="${esc(f.kind)}" style="border-top:1px solid var(--line);padding:8px 0">
      <div><span class="hint">${ic} ${label}</span>${reqs}</div><b>${esc(f.title)}</b>
      ${f.detail ? `<div class="hint">${esc(f.detail)}</div>` : ''}${f.recommendation ? `<div style="margin-top:2px">💡 <i>Fred recommends:</i> ${esc(f.recommendation)}</div>` : ''}${whyHtml(f.why)}
      ${pend ? `<div class="acts" style="margin-top:4px">${(CH_OPTS[f.kind] || CH_OPTS.objection).map(([v, t]) => `<button type="button" data-d="${v}">${t}</button>`).join('')}</div>
        <input type="text" data-why hidden placeholder="" style="margin-top:4px">`
      : `<div class="hint" style="margin-top:2px">Your decision: <b>${esc(CH_WORD[a.decision] || 'not decided')}</b>${a.reason ? ' · “' + esc(a.reason) + '”' : ''}</div>`}</div>`;
  }).join('');
  const n = el(`<div class="card ${pend ? 'pending' : ''}"><h4>😈 Devil's advocate: ${(ev.findings || []).length} point(s) to decide ${statusPill(ev.status)}</h4>
    <div class="hint">An independent reviewer who has not seen your conversation read the problem and the requirements cold. You decide on each point; you can always overrule, and your reason is kept so the people who take this over later understand why.</div>
    ${items}
    ${pend ? `<div class="acts"><button class="primary" data-send disabled>Send my decisions to Fred</button><span class="hint" data-left></span></div>` : ''}</div>`);
  if (pend) {
    const choice = {};
    const why = {keep: 'Why keep it? (optional, helps later)', accept: 'Why is the risk acceptable? (optional)', change: 'What should change? (optional, otherwise Fred uses his recommendation)', correct: 'What is right instead?', answer: 'Your answer', confirm: ''};
    const refresh = () => {
      const all = [...n.querySelectorAll('.chf')], left = all.filter(x => { const c = choice[x.dataset.f]; return !c || ((c === 'answer' || c === 'correct') && !x.querySelector('[data-why]').value.trim()); }).length;
      n.querySelector('[data-send]').disabled = left > 0;
      n.querySelector('[data-left]').textContent = left ? `${left} point(s) left to decide` : 'All decided.';
    };
    n.querySelectorAll('.chf').forEach(box => {
      box.querySelectorAll('[data-d]').forEach(b => b.onclick = () => {
        choice[box.dataset.f] = b.dataset.d;
        box.querySelectorAll('[data-d]').forEach(x => x.classList.toggle('primary', x === b));
        const inp = box.querySelector('[data-why]'); inp.placeholder = why[b.dataset.d] || ''; inp.hidden = !why[b.dataset.d]; if (!inp.hidden) inp.focus();
        refresh();
      });
      box.querySelector('[data-why]').oninput = refresh;
    });
    n.querySelector('[data-send]').onclick = () => {
      const answers = {};
      n.querySelectorAll('.chf').forEach(box => answers[box.dataset.f] = {decision: choice[box.dataset.f], reason: box.querySelector('[data-why]').value.trim()});
      respond(ev.id, {answers}, n);
    };
    refresh();
  }
  return n;
}
async function renderReq() {
  const p = $('pane'), slug = cur, pr = view.project.problem || {}, ch = view.project.challenge || {};
  const hasProblem = PROBLEM_FIELDS.some(([k]) => pr[k]) || pr.statement;
  let rows = [];
  try { rows = (await api(`/api/projects/${slug}/traceability`)).rows || []; } catch (e) { rows = []; }
  if (slug !== cur || tab !== 'req') return;
  const live = rows.filter(r => r.status !== 'dropped');
  const proven = live.filter(r => r.proven).length;
  const proof = live.length ? `<h4 style="margin:12px 0 4px">Proof by tests <span class="pill ${proven === live.length ? 'ok' : ''}">${proven} of ${live.length} proven</span></h4>
    <table class="mini" id="proofTable">${live.map(r => `<tr><td style="width:64px"><b>${esc(r.id)}</b></td><td>${esc(r.text)}</td><td style="width:150px">${r.proven ? '<span class="pill ok">✓ proven by test</span>' : (r.proof && r.proof.failing) ? '<span class="pill bad">test failing</span>' : '<span class="pill">not tested yet</span>'}</td></tr>`).join('')}</table>
    <div class="hint">A requirement is proven when a passing test checks it. This is what IT looks at when they take over a prototype.</div>` : '';
  const decided = (ch.findings || []);
  const chal = decided.length ? `<details style="margin-top:10px"><summary class="hint" style="cursor:pointer">😈 Devil's advocate review (round ${ch.round || 1}): ${decided.length} point(s), ${decided.filter(f => f.decision === 'keep' || f.decision === 'accept').length} overruled by you</summary>
    <ul class="obj">${decided.map(f => `<li><b>${esc(f.title)}</b> <span class="hint">· ${esc(CH_WORD[f.decision] || 'not decided')}${f.reason ? ' · “' + esc(f.reason) + '”' : ''}</span></li>`).join('')}</ul></details>` : '';
  const prob = (hasProblem ? `<div class="card" style="margin:0 0 8px"><h4>🎯 The problem we are solving</h4>${problemHtml(pr)}</div>` : '')
    + (view.project.questions ? `<details class="card" style="margin:0 0 8px"><summary style="cursor:pointer"><b>🧭 The questions that changed your project</b></summary>${questionsHtml(view.project.questions)}</details>` : '');
  const doc = view.docs.requirements ? `<div class="doc" style="margin-top:10px">${md(view.docs.requirements)}</div>` : '';
  p.innerHTML = (prob || doc || proof) ? prob + proof + chal + doc
    : '<p class="empty">Fred first agrees with you what problem you are solving, then writes the requirements here as you talk.</p>';
}
function questionsHtml(q) {
  q = q || {};
  return `<table class="mini"><tr><td style="width:34%" class="hint">What you first asked for</td><td>${esc(q.first_ask || '–')}</td></tr><tr><td class="hint">What you actually need</td><td><b>${esc(q.real_need || '–')}</b></td></tr></table>
    <ol style="margin:8px 0 4px;padding-left:20px">${(q.questions || []).map(x => `<li style="margin:4px 0"><b>${esc(x.question)}</b>${x.changed ? `<div>➜ ${esc(x.changed)}</div>` : ''}${x.why ? `<div class="hint">Why it matters: ${esc(x.why)}</div>` : ''}</li>`).join('')}</ol>
    <div class="hint">Saved in your project as reports/QUESTIONS.md, with the questions to ask on your next project.</div>`;
}
function changeCard(ev) {
  const pend = ev.status === 'pending';
  const chips = (label, arr) => (arr && arr.length) ? `<div class="hint">${label}: ${esc(arr.join(', '))}</div>` : '';
  const n = el(`<div class="card ${pend ? 'pending' : ''}"><h4>🔁 Change request ${esc(ev.cr || '')}: ${esc(ev.title || '')} ${statusPill(ev.status)}</h4>
    <div class="doc">${md(ev.description || '')}</div>
    ${chips('Affects requirements', ev.affects_requirements)}${chips('Affects architecture', ev.affects_decisions)}${chips('Affects built iterations', ev.affects_iterations)}
    ${ev.effort ? `<div class="hint">Estimated effort: ${esc(ev.effort)}</div>` : ''}
    ${pend ? `<div class="acts"><button class="primary" data-a="yes">Approve change</button><button data-a="no">Reject</button><input type="text" placeholder="Optional note" style="flex:1;min-width:140px"></div>` : (ev.note ? `<div class="hint">Your note: ${esc(ev.note)}</div>` : '')}</div>`);
  if (pend) n.querySelectorAll('button').forEach(b => b.onclick = () => respond(ev.id, {approved: b.dataset.a === 'yes', note: n.querySelector('input').value}, n));
  return n;
}
function councilCard(ev) {
  const working = ev.status === 'working';
  const est = ev.estimate || {};
  const op = o => `<div class="op"><div class="row"><span class="who">${esc(o.who)}</span><span class="sp"></span><span class="rank">${ev.scores && ev.scores[o.who] != null ? 'avg rank ' + ev.scores[o.who] : ''}</span></div><div class="doc">${md((o.answer || '').slice(0, 1400))}</div></div>`;
  const n = el(`<div class="card ${working ? 'pending' : ''}"><h4>🏛 Council ${working ? '<span class="spin"></span>' : statusPill('done')}</h4>
    <div class="hint">${esc(ev.question || '')}</div>
    <div style="margin:4px 0">${(ev.members || []).map(m => `<span class="mem">${esc(m)}</span>`).join('')}</div>
    ${working ? `<div class="hint">The council is answering and reviewing each other… ${est.cost != null ? '· about ' + usd(est.cost) + ' at API prices' : ''}</div>` : ''}
    ${(ev.warnings || []).length ? `<div class="hint" style="color:var(--warn)">⚠ ${ev.warnings.map(esc).join(' · ')}</div>` : ''}
    ${ev.agreement ? `<div class="what"><b>${esc(ev.agreement)}</b></div>` : ''}
    ${(ev.opinions && !working) ? `<details style="margin-top:6px"><summary class="hint" style="cursor:pointer">All opinions and rankings (${ev.opinions.length})</summary>${ev.opinions.map(op).join('')}${(ev.failed || []).map(fa => `<div class="hint">${esc(fa.who)} could not answer${fa.error ? ': ' + esc(fa.error) : ''}</div>`).join('')}</details>` : ''}
    ${(ev.opinions && !working) ? `<div class="hint" style="margin-top:4px">Fred reads this and writes the final recommendation below.</div>` : ''}</div>`);
  return n;
}
function priorArtCard(ev) {
  const cl = c => `<span class="close ${c === 'same' ? 'same' : c === 'similar' ? 'similar' : 'different'}">${esc(c || 'different')}</span>`;
  const items = (ev.items || []).map(i => `<div class="item"><div class="row"><b>${i.url ? `<a href="${esc(i.url)}" target="_blank" rel="noopener">${esc(i.name)}</a>` : esc(i.name)}</b><span class="sp"></span>${cl(i.closeness)}</div>
    <div>${esc(i.what || '')}</div>${i.cost_licence ? `<div class="hint">${esc(i.cost_licence)}</div>` : ''}${i.reuse ? `<div class="hint">Reuse: ${esc(i.reuse)}</div>` : ''}</div>`).join('');
  return el(`<div class="card pa"><h4>🔭 Existing solutions found</h4>${items || '<div class="hint">Nothing close was found: this looks worth building.</div>'}${ev.recommendation ? `<div class="what" style="margin-top:6px"><b>Recommendation:</b> ${esc(ev.recommendation)}</div>` : ''}</div>`);
}
async function respond(id, payload, node) {
  node.querySelectorAll('button,input,textarea').forEach(x => x.disabled = true);
  try { await api('/api/respond', {id, ...payload}); }
  catch (e) { node.querySelectorAll('button,input,textarea').forEach(x => x.disabled = false); alertBox(e.message); }
}
function applyUpdate(ev) {
  const target = items.find(x => x.id === ev.id); if (!target) return;
  Object.assign(target, ev.changes || {});
  const old = $('msgs').querySelector(`[data-id="${ev.id}"]`);
  if (old) old.replaceWith(renderEvent(target));
}
/* collapse runs of activity lines into one expandable line */
function groupActivities() {
  const box = $('msgs'); const kids = [...box.children]; let run = [];
  const flush = () => {
    if (run.length > 1) {
      const d = el(`<details class="act" data-grp="1"><summary>▸ ${esc(run[run.length - 1].textContent)} <span class="hint">(+${run.length - 1} more steps)</span></summary><ul></ul></details>`);
      run.forEach(r => { const li = document.createElement('li'); li.textContent = r.textContent; d.querySelector('ul').appendChild(li); });
      run[0].before(d); run.forEach(r => r.remove());
    }
    run = [];
  };
  kids.forEach(k => { if (k.dataset && k.dataset.act) run.push(k); else flush(); }); flush();
}

/* ------------------------------------------------------------------ live events */
function connect() {
  if (es) es.close();
  es = new EventSource(`/api/stream?token=${TOKEN}&since=${lastSeq}`);
  es.onmessage = m => { const ev = JSON.parse(m.data); lastSeq = Math.max(lastSeq, ev.seq); onEvent(ev); };
  es.onerror = () => { es.close(); setTimeout(connect, 2000); };
}
let docTimer = null;
function onEvent(ev) {
  if (ev.type === 'brainjob') { onBrainJob(ev); return; }
  if (ev.type === 'project_list') { loadState(); return; }
  if (ev.type === 'voice' && /^test_/.test(ev.state || '')) { onMicTest(ev); return; }
  if (ev.type === 'telegram' && ev.transient) { loadState().then(() => { if ($('setDlg').open) renderPhoneBox(); }); return; }
  if (!['voice', 'team', 'docs_changed', 'activity', 'message', 'cost'].includes(ev.type)) softState();
  if (ev.type === 'usage') { loadState(); return; }
  if (ev.type === 'team' && ev.slug === cur) { if (view) view.team = ev.team; if (tab === 'team') renderTeam(); return; }
  if (ev.type === 'tests_changed' && ev.slug === cur) { markSide(); if (tab === 'tests') renderSide(); return; }
  if (ev.slug !== cur) { if (ev.type === 'busy') loadState(); return; }
  if (ev.type === 'busy') { setBusy(ev.value); if (!ev.value) { refreshView(); autoListen(); } loadState(); return; }
  if (ev.type === 'docs_changed') { markSide(); clearTimeout(docTimer); docTimer = setTimeout(refreshDocs, 400); return; }
  if (ev.type === 'cost') { if (view) view.project.cost_usd = ev.usd; renderCost(view?.project); return; }
  if (ev.type === 'voice') { onVoice(ev); return; }
  if (ev.type === 'update') { applyUpdate(ev); return; }
  if (!view) { early.push(ev); return; }  // still loading this project: open() adds it after the history
  if (items.some(x => x.seq === ev.seq)) return;
  items.push(ev); $('msgs').querySelector('.typing')?.remove();
  $('msgs').appendChild(renderEvent(ev)); groupActivities();
  if (busy) $('msgs').appendChild(typingEl());
  if (ev.type === 'screenshot' && tab === 'shots') renderSide();
  if (['setup_done', 'plan', 'iteration', 'handoff', 'architecture', 'release_wait'].includes(ev.type)) { loadState(); refreshDocs(); markSide(); }
  scrollDown(['approval', 'secret', 'folder', 'plan', 'choice', 'limit', 'architecture', 'handoff', 'change', 'council', 'prior_art', 'challenge', 'stuck'].includes(ev.type));
}
async function refreshView() { if (!cur) return; try { view = await api('/api/projects/' + cur); renderHeader(); renderSide(); } catch (e) {} }
async function refreshDocs() { await refreshView(); }

/* L9 + L15: a mini chart of the last 30 minutes and the plan's room left, next to the working indicator */
let SPARK = '', sparkTimer = null;
function typingEl() { return el(`<div class="typing"><span class="spin"></span> ${leadName()} is working…<span class="spark">${SPARK}</span></div>`); }
async function refreshSpark() {
  if (!busy || !cur || cur === 'setup') { SPARK = ''; return; }
  let a; try { a = await api(`/api/projects/${cur}/activity`); } catch (e) { return; }
  const m = a.minutes.slice(-30), max = Math.max(1, ...m), w = 90, hh = 16, bw = w / 30;
  const bars = m.map((v, i) => v ? `<rect x="${(i * bw).toFixed(1)}" y="${(hh - Math.max(1.5, hh * v / max)).toFixed(1)}" width="${(bw - 0.6).toFixed(1)}" height="${Math.max(1.5, hh * v / max).toFixed(1)}" rx="1" fill="var(--accent)"/>` : '').join('');
  const flat = a.state === 'working' && a.flat_minutes >= 5;
  const room = (a.headroom || []).find(h => h.kind === 'five_hour' && h.percent != null);
  SPARK = `<svg width="${w}" height="${hh}" viewBox="0 0 ${w} ${hh}" role="img" aria-label="Tokens per minute, last 30 minutes"><title>Tokens per minute, last 30 minutes (${fmtTok(m.reduce((x, y) => x + y, 0))} tokens)</title><line x1="0" x2="${w}" y1="${hh - 0.5}" y2="${hh - 0.5}" stroke="var(--line)"/>${bars}</svg>`
    + (flat ? `<span class="flat">no tokens used for ${a.flat_minutes} min: something may be stuck</span>` : '')
    + (room ? `<span title="Your Claude plan: 5-hour limit">· ${Math.round(room.left)}% of the 5-hour limit left</span>` : '');
  const t = $('msgs').querySelector('.typing .spark'); if (t) t.innerHTML = SPARK;
}
let busy = false;
function setBusy(v) {
  busy = !!v; $('stopBtn').hidden = !busy; $('sendBtn').disabled = !cur;
  clearInterval(sparkTimer); if (busy) { refreshSpark(); sparkTimer = setInterval(refreshSpark, 30000); } else SPARK = '';
  $('msgs').querySelector('.typing')?.remove();
  if (busy) { $('msgs').appendChild(typingEl()); scrollDown(); }
  $('status').textContent = '';
}
