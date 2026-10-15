/* AI Team Lite - screens.
   Pipeline, feedback, requested features, launch, layout, council, dialogs, settings, onboarding.
   Classic scripts loaded in order by index.html; they share globals (TOKEN, $, S, cur, view...). */
/* ------------------------------------------------------------------ work queue (request G) */
let workTimer = null;
async function renderWork() {
  const d = await api('/api/work');
  $('workSum').textContent = ` · ${d.running} of ${d.max_parallel} working slots in use`;
  const stPill = s => `<span class="pill ${s === 'working' ? 'ok' : s === 'needs you' || s === 'limit' ? 'warn' : s === 'paused' ? 'bad' : ''}">${esc(s === 'limit' ? 'stopped: usage limit' : s)}</span>`;
  const since = s => s ? `since ${new Date(s * 1000).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})}` : '';
  $('workBody').innerHTML = `<div class="qrow head"><div>Project</div><div>Status</div><div>Who is working on what</div><div>Priority</div><div></div></div>` +
    (d.rows.length ? d.rows.map(r => `<div class="qrow" data-s="${esc(r.slug)}"><div><a href="#" data-open>${esc(r.name)}</a><div class="hint">${esc(phaseLabel(r.phase) + iterLabel(r.progress, r.phase))}</div></div>
      <div>${stPill(r.state)}<div class="hint">${since(r.since)}${r.continue_at ? ' · continues ' + new Date(r.continue_at * 1000).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}) : ''}${r.queued ? ` · ${r.queued} message(s) waiting` : ''}</div></div>
      <div>${r.who.length ? r.who.map(w => `<div><b>${esc(w.name)}</b>: ${esc(w.task || w.last || '')}</div>`).join('') : `<span class="hint">${r.pending.length ? 'Waiting for your answer in the chat' : 'Nobody'}</span>`}</div>
      <div><select data-prio><option value="high">High</option><option value="normal">Normal</option><option value="low">Low</option></select></div>
      <div class="row" style="flex-wrap:wrap">${r.state === 'paused' ? '<button type="button" data-act="resume">▶ Resume</button>' : '<button type="button" data-act="pause">⏸ Pause</button>'}<button type="button" data-act="restart" title="Start the current step again">↻ Restart</button></div></div>`).join('')
      : '<p class="empty">No projects yet.</p>');
  $('workBody').querySelectorAll('.qrow[data-s]').forEach(row => {
    const slug = row.dataset.s, r = d.rows.find(x => x.slug === slug);
    row.querySelector('[data-prio]').value = r.priority;
    row.querySelector('[data-prio]').onchange = e => api(`/api/projects/${slug}/priority`, {priority: e.target.value}).then(renderWork);
    row.querySelectorAll('[data-act]').forEach(b => b.onclick = async () => {
      if (b.dataset.act === 'restart' && !confirm('Restart the current step of this project from the beginning?')) return;
      await api(`/api/projects/${slug}/${b.dataset.act}`, {}); renderWork(); loadState();
    });
    row.querySelector('[data-open]').onclick = e => { e.preventDefault(); $('workDlg').close(); open(slug); };
  });
}
let wtab = 'pipe';
async function renderPipe() {
  const d = await api('/api/pipeline');
  $('workSum').textContent = ` · ${d.running} of ${d.max_parallel} working slots in use`;
  $('pipeHint').innerHTML = `All approved features of all projects, in master-plan order (top first). ${d.auto ? 'When a project stops or waits for your answer, the next one here continues.' : 'The master plan does not continue projects by itself (Settings &gt; General).'} A project waiting for your answer does not use a working slot.`;
  const pill = st => `<span class="pill ${st === 'done' ? 'ok' : st === 'building' ? 'acc' : st === 'waiting for release' || st === 'waiting for your answer' ? 'warn' : st === 'stopped mid-way' ? 'bad' : ''}">${esc(st)}</span>`;
  $('pipeBody').innerHTML = d.projects.length ? d.projects.map((p, pi) => `<div class="pipe-proj" data-s="${esc(p.slug)}">
    <div class="row" style="flex-wrap:wrap"><b style="font-size:14.5px"><a href="#" data-open>${esc(p.name)}</a></b>${stateHtml(p.state)}${p.approved ? '' : '<span class="pill warn">plan not approved yet</span>'}
      <span class="sp"></span><button type="button" class="ghost" data-mv="-1" ${pi === 0 ? 'disabled' : ''} title="Move up in the master plan">↑</button><button type="button" class="ghost" data-mv="1" ${pi === d.projects.length - 1 ? 'disabled' : ''} title="Move down">↓</button>
      ${p.state === 'paused' ? '<button type="button" data-pact="resume">▶ Resume</button>' : '<button type="button" data-pact="pause">⏸ Pause</button>'}
      ${p.launch ? '<button type="button" class="primary" data-launch>▶ Launch</button>' : ''}</div>
    ${p.iterations.map(it => `<div class="pipe-it"><div class="itnum ${it.status === 'done' ? 'done' : it.status === 'building' ? 'building' : ''}">${it.status === 'done' ? '✓' : it.n}</div>
      <div><b>${esc(it.name)}</b>${it.requirements.length ? ` <span class="hint">· ${esc(it.requirements.join(', '))}</span>` : ''}${it.who.length ? `<div class="hint">${it.who.map(w => esc(w.name + (w.task ? ': ' + w.task : ''))).join(' · ')}</div>` : ''}</div>
      <div>${pill(it.status)}${it.commit ? ` <code class="hint">${esc(it.commit)}</code>` : ''}</div>
      <div>${it.status === 'done' || it.n === p.iterations[p.iterations.length - 1].n ? '' : `<select data-after="${it.n}" title="What happens when this iteration is finished"><option value="continue" ${it.after !== 'pause' ? 'selected' : ''}>then continue</option><option value="pause" ${it.after === 'pause' ? 'selected' : ''}>then pause for me</option></select>`}</div>
      <div>${it.status === 'waiting for release' ? `<button type="button" class="primary" data-release="${it.n}">Release</button>` : it.status === 'waiting for your answer' ? '<a href="#" data-open>Answer</a>' : ''}</div></div>`).join('')}</div>`).join('')
    : '<p class="empty">No plans yet. Approved plans and their iterations appear here.</p>';
  $('pipeBody').querySelectorAll('.pipe-proj').forEach(box => {
    const slug = box.dataset.s, act = (path, body) => api(`/api/projects/${slug}/${path}`, body || {}).then(renderPipe).then(loadState).catch(e => alertBox(e.message));
    box.querySelectorAll('[data-open]').forEach(a => a.onclick = e => { e.preventDefault(); $('workDlg').close(); open(slug); });
    box.querySelectorAll('[data-after]').forEach(sel => sel.onchange = () => act('iteration-after', {n: +sel.dataset.after, after: sel.value}));
    box.querySelectorAll('[data-release]').forEach(b => b.onclick = () => act('release', {n: +b.dataset.release}));
    box.querySelectorAll('[data-pact]').forEach(b => b.onclick = () => act(b.dataset.pact));
    const l = box.querySelector('[data-launch]'); if (l) l.onclick = () => launchProject(slug);
    box.querySelectorAll('[data-mv]').forEach(b => b.onclick = async () => {
      const order = d.projects.map(x => x.slug), k = order.indexOf(slug), t = k + +b.dataset.mv;
      [order[k], order[t]] = [order[t], order[k]];
      await api('/api/pipeline/order', {order}); renderPipe();
    });
  });
}
document.querySelectorAll('.wtab').forEach(b => b.onclick = () => {
  wtab = b.dataset.w; document.querySelectorAll('.wtab').forEach(x => x.classList.toggle('on', x === b));
  document.querySelectorAll('[data-wp]').forEach(x => x.hidden = x.dataset.wp !== wtab);
  (wtab === 'pipe' ? renderPipe : renderWork)();
});
const refreshWork = () => (wtab === 'pipe' ? renderPipe : renderWork)();
$('workBtn').onclick = async () => { await refreshWork(); $('workDlg').showModal(); clearInterval(workTimer); workTimer = setInterval(() => { if ($('workDlg').open) refreshWork(); else clearInterval(workTimer); }, 4000); };

/* ------------------------------------------------------------- feedback (item 50) */
$('feedbackBtn').onclick = () => { $('fbTitle').value = ''; $('fbBody').value = ''; $('fbMsg').textContent = ''; $('fbSend').disabled = false; $('fbDlg').showModal(); $('fbTitle').focus(); };
$('fbSend').onclick = async () => {
  const title = $('fbTitle').value.trim(); if (!title) { $('fbMsg').textContent = 'Please write a short title first.'; return; }
  $('fbSend').disabled = true; $('fbMsg').textContent = 'Sending…';
  try { const r = await api('/api/features/feedback', {title, body: $('fbBody').value, voice_log: $('fbVoice').checked});
    $('fbMsg').textContent = r.shared ? 'Thank you! Your feedback reached the author.' : 'Thank you! Your feedback was saved' + (r.error ? ' (it will reach the author when a connection is available).' : '.');
    setTimeout(() => $('fbDlg').close(), 1400);
  } catch (e) { $('fbMsg').textContent = e.message; $('fbSend').disabled = false; }
};

/* ------------------------------------------------- requested features page (item 51) */
const FEAT_LABEL = {new: 'Needs a decision', approved: 'Approved', rejected: 'Rejected', planned: 'Planned', built: 'Built'};
async function renderFeatures() {
  let d; try { d = await api('/api/features'); } catch (e) { $('featBody').innerHTML = '<p class="hint">Could not load.</p>'; return; }
  const manage = d.can_manage;
  $('featImport').hidden = !d.is_owner;
  $('featIntro').textContent = manage
    ? 'Feedback from you and from people using the app. Approve what should be built (it goes to the council and the queue) or reject it with a reason.'
    : 'What the author has approved to build, and what was turned down (with the reason). Use “Send feedback” to suggest your own.';
  const order = manage ? ['new', 'approved', 'planned', 'built', 'rejected'] : ['approved', 'planned', 'built', 'rejected'];
  const shown = (d.items || []).filter(i => order.includes(i.status));
  if (!shown.length) { $('featBody').innerHTML = '<p class="hint">Nothing here yet.</p>'; return; }
  $('featBody').innerHTML = order.map(st => {
    const its = shown.filter(i => i.status === st); if (!its.length) return '';
    return `<h4 style="margin:12px 0 4px">${FEAT_LABEL[st]} <span class="pill">${its.length}</span></h4>` + its.map(i => `
      <div class="feat" data-id="${esc(i.id)}" style="border:1px solid var(--line);border-radius:8px;padding:8px 10px;margin:6px 0">
        <div><b>${esc(i.title)}</b> ${i.source !== 'me' ? `<span class="hint">· from ${i.source === 'feedback' ? 'you' : 'a user'}${i.author ? ' (' + esc(i.author) + ')' : ''}</span>` : ''}</div>
        ${i.body ? `<div class="hint" style="white-space:pre-wrap;margin:3px 0">${esc(i.body)}</div>` : ''}
        ${i.status === 'rejected' && i.reason ? `<div class="hint" style="color:var(--bad)">Reason: ${esc(i.reason)}</div>` : ''}
        ${manage ? `<div class="row" style="flex-wrap:wrap;margin-top:5px">
          ${st !== 'approved' ? '<button type="button" data-act="approved">Approve</button>' : ''}
          ${st !== 'planned' ? '<button type="button" data-act="planned">Mark planned</button>' : ''}
          ${st !== 'built' ? '<button type="button" data-act="built">Mark built</button>' : ''}
          ${st !== 'rejected' ? '<button type="button" class="danger" data-act="rejected">Reject…</button>' : ''}
        </div>` : ''}
      </div>`).join('');
  }).join('');
  if (manage) $('featBody').querySelectorAll('.feat').forEach(card => {
    const id = card.dataset.id;
    card.querySelectorAll('[data-act]').forEach(b => b.onclick = async () => {
      let reason = '';
      if (b.dataset.act === 'rejected') { reason = prompt('Why is this being turned down? (shown on the requested-features page)') || ''; if (reason === '') return; }
      try { await api('/api/features/status', {id, status: b.dataset.act, reason}); await renderFeatures(); loadState(); } catch (e) { alertBox(e.message); }
    });
  });
}
$('featuresBtn').onclick = async () => { await renderFeatures(); $('featDlg').showModal(); };
$('featImport').onclick = async () => {
  $('featImport').disabled = true;
  try { const r = await api('/api/features/import', {}); alertBox(r.added ? `${r.added} new item(s) imported.` : 'No new feedback to import.', true); await renderFeatures(); loadState(); }
  catch (e) { alertBox(e.message); }
  $('featImport').disabled = false;
};

/* ------------------------------------------------------------------ launch the project (request 21) */
let LAUNCH_SAVE_ONLY = false;
function chooseStart(slug, info, saveOnly) {
  LAUNCH_SAVE_ONLY = !!saveOnly;
  const list = info.all_choices && info.all_choices.length ? info.all_choices : (info.choices || []);
  $('launchList').innerHTML = list.map((c, i) => `<label><input type="radio" name="lstart" value="${esc(c)}" ${c === info.command || (!info.command && !i) ? 'checked' : ''}><b>${esc(c)}</b></label>`).join('');
  $('launchGo').textContent = saveOnly ? 'Save' : 'Start it';
  $('launchGo').onclick = async () => {
    const command = (document.querySelector('input[name=lstart]:checked') || {}).value; if (!command) return;
    $('launchDlg').close();
    try { const r = await api(`/api/projects/${slug}/launch`, {command, save_only: LAUNCH_SAVE_ONLY});
      alertBox(LAUNCH_SAVE_ONLY ? `The project now starts with ${command}.` : `Started ${r.version || command}.`, true); refreshView(); }
    catch (e) { alertBox(e.message); }
  };
  $('launchDlg').showModal();
}
async function launchProject(slug) {
  try { const r = await api(`/api/projects/${slug}/launch`, {});
    if (r.needs_choice) { chooseStart(slug, r, false); return; }
    alertBox(`Started ${r.version || 'the app'}.`, true); }
  catch (e) {
    const d = el(`<div class="err" style="position:fixed;bottom:20px;right:20px;z-index:30;max-width:440px"><div></div><div class="acts" style="margin-top:8px"><button class="primary" data-fix>Ask Fred to fix it</button><button data-x>Close</button></div></div>`);
    d.firstChild.textContent = '⚠ ' + e.message; document.body.appendChild(d);
    d.querySelector('[data-x]').onclick = () => d.remove();
    d.querySelector('[data-fix]').onclick = async () => { d.remove(); if (cur !== slug) await open(slug); await api(`/api/projects/${slug}/message`, {text: `I tried to launch the project with the Launch button and it failed: ${e.message}\nPlease find out why and fix it.`}); };
  }
}
$('launchBtn').onclick = () => cur && launchProject(cur);

/* ------------------------------------------------------------------ layout: side panel width and collapse (requests 16, 17) */
const LS = {get: k => { try { return localStorage.getItem(k); } catch (e) { return null; } }, set: (k, v) => { try { localStorage.setItem(k, v); } catch (e) {} }};
let sideHidden = LS.get('aiteam.sideHidden') === '1', sideOver = false, sideBadge = false;
function applyLayout() {
  const app = $('app'), narrow = innerWidth <= 1180, w = parseInt(LS.get('aiteam.sideW') || '0');
  if (w) app.style.setProperty('--sidew', Math.max(380, Math.min(w, innerWidth - 240 - 420)) + 'px');
  app.classList.toggle('noside', !narrow && sideHidden);
  app.classList.toggle('sideover', narrow && sideOver);
  const shown = narrow ? sideOver : !sideHidden, t = $('sideToggle');
  t.style.right = shown ? $('side').getBoundingClientRect().width + 'px' : '0';
  t.innerHTML = (shown ? '»' : '«') + (!shown && sideBadge ? '<span class="dot"></span>' : '');
  t.title = shown ? 'Hide the side panel' : 'Show the side panel' + (sideBadge ? ' (something new)' : '');
  if (shown) sideBadge = false;
}
$('sideToggle').onclick = () => {
  if (innerWidth <= 1180) sideOver = !sideOver; else { sideHidden = !sideHidden; LS.set('aiteam.sideHidden', sideHidden ? '1' : '0'); }
  if (!sideHidden || sideOver) sideBadge = false;
  applyLayout(); if (!sideHidden) renderSide();
};
function markSide() { const narrow = innerWidth <= 1180; if (narrow ? !sideOver : sideHidden) { sideBadge = true; applyLayout(); } }
$('resizer').onmousedown = e => {
  e.preventDefault(); const r = $('resizer'); r.classList.add('drag');
  const move = ev => { const w = Math.max(380, Math.min(innerWidth - ev.clientX, innerWidth - 240 - 420)); $('app').style.setProperty('--sidew', w + 'px'); LS.set('aiteam.sideW', String(Math.round(w))); applyLayout(); };
  const up = () => { r.classList.remove('drag'); removeEventListener('mousemove', move); removeEventListener('mouseup', up); if (tab === 'team') renderTeam(); };
  addEventListener('mousemove', move); addEventListener('mouseup', up);
};
addEventListener('resize', applyLayout);

/* ------------------------------------------------------------------ brains settings (requests D and E) */
let BR = null;
async function renderBrains() {
  BR = await api('/api/brains');
  $('brainList').innerHTML = BR.brains.length ? BR.brains.map(b => `<div class="brainrow" data-id="${esc(b.id)}">
      <label class="row" style="gap:6px"><input type="checkbox" data-en ${b.enabled ? 'checked' : ''} style="width:auto"> <b>${esc(b.label)}</b></label>
      <span class="pill">${esc(b.badge)}</span>${!b.has_key ? '<span class="pill bad">no key</span>' : ''}${b.out_of_quota ? '<span class="pill warn">out of quota</span>' : ''}
      <span class="sp"></span><button type="button" data-test>Test</button><button type="button" class="danger" data-rm>Remove</button>
      <span class="hint" data-msg style="width:100%"></span></div>`).join('') : '<p class="hint">No council brains yet: Fred decides on his own.</p>';
  $('brainList').querySelectorAll('.brainrow').forEach(row => {
    const id = row.dataset.id;
    row.querySelector('[data-en]').onchange = e => api('/api/brains/toggle', {id, enabled: e.target.checked}).then(renderBrains);
    row.querySelector('[data-rm]').onclick = () => confirm('Remove this brain from the council?') && api('/api/brains/remove', {id}).then(renderBrains);
    row.querySelector('[data-test]').onclick = async () => { const m = row.querySelector('[data-msg]'); m.textContent = 'Testing…'; try { const r = await api('/api/brains/test', {id}); m.textContent = (r.ok ? '✓ ' : '✗ ') + r.message; } catch (e) { m.textContent = e.message; } };
  });
  const keepProv = $('bProv').value, keepMsg = $('bMsg').innerHTML;
  $('bProv').innerHTML = Object.entries(BR.providers).map(([k, p]) => `<option value="${k}">${esc(p.name)} (${esc(p.badge)})</option>`).join('');
  if (keepProv && BR.providers[keepProv]) $('bProv').value = keepProv;  // stay on the provider you were using
  provChanged(); $('bMsg').innerHTML = keepMsg;
}
let REC_WHY = '';
const tierPill = t => t === 'free' ? '<span class="pill ok">free</span>' : t === 'paid' ? '<span class="pill warn">paid</span>' : '';
function selectModel(name) {
  $('bModel').value = name;
  $('bModelList').querySelectorAll('.mitem').forEach(it => it.classList.toggle('sel', it.dataset.m === name));
}
function modelRow(r, isOllama) {
  const badges = (r.recommended ? `<span class="pill ok" title="${esc(REC_WHY)}">★ recommended (coding)</span>` : '') + (r.best_for || []).map(b => `<span class="pill acc">${esc(b)}</span>`).join('') + (r.added ? '<span class="pill">already added</span>' : '') + (isOllama
    ? `${r.installed ? '<span class="pill ok">✓ installed</span>' : ''}${r.params ? `<span class="pill">${esc(r.params)}</span>` : ''}`
    : tierPill(r.tier));
  const extra = isOllama ? [r.size_gb ? `${r.size_gb} GB download` : '', r.min_ram_gb ? `needs ${r.min_ram_gb} GB memory` : ''].filter(Boolean).join(' · ') : '';
  return `<label class="mitem ${isOllama && r.installed ? 'inst' : ''} ${isOllama && !r.ram_ok ? 'off' : ''}" data-m="${esc(r.model)}">
    <input type="radio" name="bpick">
    <div class="mt"><b>${esc(r.model)}</b> ${badges}<span class="hint">${esc(r.note || '')}${extra ? ' · ' + esc(extra) : ''}</span></div></label>`;
}
function renderModelList(rows, isOllama) {
  if (!rows.length) { $('bModelList').innerHTML = `<div class="mitem off"><div class="mt hint">No models found. Check the key and try again.</div></div>`; return; }
  if (isOllama) {
    // installed first, then everything that fits this PC (small to large), then the ones that are too big, folded
    const inst = rows.filter(r => r.installed), fit = rows.filter(r => !r.installed && r.ram_ok), big = rows.filter(r => !r.installed && !r.ram_ok);
    $('bModelList').innerHTML =
      (inst.length ? `<div class="mgroup">Already on your PC (${inst.length})</div>${inst.map(r => modelRow(r, true)).join('')}` : '') +
      (fit.length ? `<div class="mgroup">Fit your PC — download when you add one (${fit.length})</div>${fit.map(r => modelRow(r, true)).join('')}` : '') +
      (big.length ? `<details><summary>Too big for this PC's memory (${big.length}) — would be very slow</summary>${big.map(r => modelRow(r, true)).join('')}</details>` : '');
  } else {
    const rec = rows.filter(r => r.recommended), rest = rows.filter(r => !r.recommended);
    $('bModelList').innerHTML = (rec.length ? `<div class="mgroup">Recommended</div>${rec.map(r => modelRow(r, false)).join('')}<div class="mgroup">All other models (${rest.length})</div>` : '') + rest.map(r => modelRow(r, false)).join('');
  }
  $('bModelList').querySelectorAll('.mitem').forEach(it => it.onclick = () => { it.querySelector('input').checked = true; selectModel(it.dataset.m); });
  const pick = isOllama ? (rows.find(r => r.installed && !r.added) || rows.find(r => r.recommended) || rows.find(r => r.ram_ok)) : (rows.find(r => r.recommended) || rows[0]);
  if (pick) { selectModel(pick.model); const el0 = $('bModelList').querySelector(`.mitem[data-m="${CSS.escape(pick.model)}"] input`); if (el0) el0.checked = true; }
}
function provChanged() {
  const p = BR.providers[$('bProv').value]; const isOllama = !!p.is_local;
  $('bKeyWrap').hidden = !p.needs_key;
  $('bHow').innerHTML = (p.how || []).map(x => `<li>${inline(x)}</li>`).join('');
  $('bNote').innerHTML = `<b>${esc(p.free_hint || '')}</b><br>${esc(p.privacy)}`;
  $('bModel').value = ''; $('bModelList').innerHTML = ''; $('bLoadMsg').textContent = '';
  $('bLoad').textContent = isOllama ? 'Refresh the list' : 'Load the list of models';
  if (isOllama) loadModels();  // local catalog: show it right away, no key needed
}
$('bProv').onchange = () => { $('bMsg').textContent = ''; $('bKey').value = ''; provChanged(); };  // never send one provider's key to another
async function loadModels() {
  const isOllama = !!BR.providers[$('bProv').value].is_local;
  $('bLoadMsg').textContent = isOllama ? 'Checking your PC and Ollama…' : 'Loading…';
  try { const r = await api('/api/brains/models', {provider: $('bProv').value, key: $('bKey').value.trim()});
    const rows = r.tiered || r.models.map(m => ({model: m, tier: ''}));
    REC_WHY = r.recommended_why || '';
    renderModelList(rows, isOllama);
    if (isOllama) {
      const nInst = rows.filter(x => x.installed).length, nFit = rows.filter(x => x.ram_ok).length;
      const state = r.ollama_state === 'running' ? '' : r.ollama_state === 'stopped' ? ' Ollama is installed but could not be started.' : ' Ollama is not installed yet: AI Team Lite installs it when you add your first local model.';
      $('bLoadMsg').innerHTML = `${esc(`Your PC has ${r.ram_gb || '?'} GB of memory${r.vram_gb ? ` and a ${r.vram_gb} GB graphics card (faster)` : ''}: ${nFit} models fit${nInst ? `, ${nInst} already installed` : ''}.${state}`)}<br><span class="hint">${esc(REC_WHY)}</span>`;
    } else {
      $('bLoadMsg').textContent = `${rows.length} models found. All are listed below with what each is best for; the recommended one is at the top.`;
    }
  } catch (e) { $('bLoadMsg').textContent = e.message; }
}
$('bLoad').onclick = loadModels;
async function addBrain(force) {
  const prov = $('bProv').value, model = $('bModel').value.trim();
  if (!model) { $('bMsg').textContent = 'Pick a model first (Load the list, then choose one).'; return; }
  // a live counter so the user always sees what is happening (item 63)
  const t0 = Date.now(), limit = prov === 'ollama' ? 90 : 30;
  const tick = () => { $('bMsg').innerHTML = `<span class="spin"></span> Testing ${esc(model)}… ${Math.round((Date.now() - t0) / 1000)} s <span class="hint">(it answers within ${limit} s, or AI Team Lite stops waiting)</span>`; };
  tick(); const timer = setInterval(tick, 1000); $('bAddBtn').disabled = true;
  try {
    const r = await api('/api/brains/add', {provider: prov, model, key: $('bKey').value.trim(), force: !!force}).finally(() => { clearInterval(timer); $('bAddBtn').disabled = false; });
    if (r.needs_download) {
      $('bMsg').innerHTML = `${esc(model)} is not on your PC yet${r.size_gb ? ` (${r.size_gb} GB download)` : ''}.${r.ollama_installed ? '' : ' Ollama will be installed first.'} <button type="button" class="primary" id="bGet">Download and add</button>`;
      $('bGet').onclick = async () => { $('bGet').disabled = true; $('bMsg').textContent = 'Starting…'; try { await api('/api/brains/ollama-get', {model}); } catch (e) { $('bMsg').textContent = e.message; } };
      return;
    }
    if (r.added) { $('bKey').value = ''; $('bModel').value = '';
      $('bMsg').innerHTML = r.test_ok ? `✓ <b>${esc(model)} added and working</b> — it now appears in the list above. ${esc(r.test)}` : `Added but switched off (the test failed): ${esc(r.test)}`;
      renderBrains(); loadState(); return; }
    const tierWarn = r.tier === 'paid' ? ' This model is paid, so it needs credit on your account.' : '';
    $('bMsg').innerHTML = `✗ Not added — the test failed: ${esc(r.test)}.${tierWarn} <a href="#" id="bForce">Add anyway (switched off)</a>`;
    $('bForce').onclick = e => { e.preventDefault(); addBrain(true); };
  } catch (e) { $('bMsg').textContent = e.message; }
}
$('bAddBtn').onclick = () => addBrain(false);
function onBrainJob(ev) {
  const pct = ev.frac != null ? ` ${Math.round(ev.frac * 100)}%` : '';
  $('bMsg').textContent = (ev.text || '') + pct;
  if (ev.done && $('setDlg').open) { renderBrains().then(() => { if (BR.providers[$('bProv').value]?.is_local) loadModels(); }); loadState(); }
}
document.querySelectorAll('.stab').forEach(b => b.onclick = () => {
  document.querySelectorAll('.stab').forEach(x => x.classList.toggle('on', x === b));
  document.querySelectorAll('[data-sp]').forEach(sec => sec.hidden = sec.dataset.sp !== b.dataset.s);
  if (b.dataset.s === 'brains') renderBrains();
  if (b.dataset.s === 'setup') renderSetupTab();
});

/* ------------------------------------------------------------------ dialogs */
function alertBox(msg, ok) { const d = el(`<div class="err" style="position:fixed;bottom:20px;right:20px;z-index:30;max-width:420px${ok ? ';background:var(--okbg);color:var(--ok);border-color:var(--ok)' : ''}"></div>`); d.textContent = msg; document.body.appendChild(d); setTimeout(() => d.remove(), 6000); }
function lightbox(src) { const d = el(`<div class="lightbox"><img src="${src}" alt=""></div>`); d.onclick = () => d.remove(); document.body.appendChild(d); }
function newKind() {
  const ex = document.querySelector('input[name=nkind]:checked').value === 'existing';
  $('newExisting').hidden = !ex; $('newBrowse').hidden = !S?.desktop;
  $('newName').required = !ex;
  $('newNameLbl').textContent = ex ? 'Name in AI Team Lite (optional: the folder name is used if empty)' : 'Give it a short name (you can describe the idea to Fred next)';
  $('newName').placeholder = ex ? 'e.g. Price watcher' : 'e.g. Invoice tracker';
  return ex;
}
document.querySelectorAll('input[name=nkind]').forEach(r => r.onchange = () => { if (newKind()) $('newFolder').focus(); else $('newName').focus(); });
$('newBrowse').onclick = async () => { const r = await api('/api/pick-folder', {start: $('newFolder').value}); if (r.path) $('newFolder').value = r.path; };
$('newBtn').onclick = () => {
  $('newName').value = ''; $('newFolder').value = ''; document.querySelector('input[name=nkind][value=new]').checked = true;
  newKind(); $('newDlg').showModal(); $('newName').focus();
};
$('newCancel').onclick = () => $('newDlg').close();
$('newForm').onsubmit = async e => {
  e.preventDefault(); const name = $('newName').value.trim(), ex = newKind(), folder = $('newFolder').value.trim();
  if (ex ? !folder : !name) return;
  try { const p = await api('/api/projects', ex ? {name, folder} : {name}); $('newDlg').close(); await loadState(); open(p.slug); } catch (err) { alertBox(err.message); }
};
$('hiddenBtn').onclick = () => { showHidden = !showHidden; loadState(); };
$('menuBtn').onclick = () => {
  const p = view?.project; if (!p) return;
  $('projDlgTitle').textContent = p.name; $('projMsg').textContent = '';
  $('projDlgInfo').innerHTML = `Folder: <code>${esc(p.folder || '')}</code>${p.existing_folder ? '<br>Started from an existing folder.' : ''}`;
  $('hideBtn').textContent = p.hidden ? '👁 Show this project again' : '🙈 Hide this project';
  $('hideHint').textContent = p.hidden ? '' : 'Hidden projects disappear from the list (nothing is deleted). Bring them back with "Show hidden projects".';
  $('removeWrap').hidden = !p.existing_folder;
  const li = p.launch || {}; $('startRow').hidden = !((li.all_choices || []).length > 1);
  $('startHint').textContent = li.command ? `Now: ${li.command}` : '';
  $('delLbl').innerHTML = `<b>Delete everything:</b> the project folder <code>${esc(p.folder || '')}</code> and all its files${p.existing_folder ? ' (your original files too)' : ''}. This cannot be undone. Type <b>${esc(p.name)}</b> to confirm.`;
  $('delConfirm').value = ''; $('delBtn').disabled = true;
  $('projDlg').showModal();
};
$('delConfirm').oninput = () => { $('delBtn').disabled = $('delConfirm').value.trim() !== view?.project?.name; };
async function projAction(path, body) {
  $('projMsg').textContent = '';
  try { const r = await api(`/api/projects/${cur}/${path}`, body); return r; } catch (e) { $('projMsg').textContent = e.message; return null; }
}
$('startBtn').onclick = () => { $('projDlg').close(); chooseStart(cur, view.project.launch, true); };
$('reloadBtn').onclick = async () => {
  const r = await projAction('reload', {}); if (r) { $('projDlg').close(); alertBox('Fred is re-reading the project folder.', true); await refreshView(); loadState(); }
};
let BRK = null;
function openBroken(b) {
  BRK = b; $('brkTitle').textContent = `“${b.name}” can't be opened`; $('brkMsg').textContent = '';
  $('brkInfo').innerHTML = `${esc(b.reason)}<br>Folder: <code>${esc(b.path)}</code>`;
  $('brkReload').hidden = !b.folder_exists; $('brkPathRow').hidden = true; $('brkPath').value = '';
  $('brokenDlg').showModal();
}
async function brokenReload(folder) {
  $('brkMsg').textContent = '';
  try { const p = await api(`/api/broken/${BRK.slug}/reload`, folder ? {folder} : {}); $('brokenDlg').close(); await loadState(); open(p.slug); alertBox('Reloaded: Fred is re-reading the folder.', true); }
  catch (e) { $('brkMsg').textContent = e.message; }
}
$('brkReload').onclick = () => brokenReload('');
$('brkPick').onclick = async () => {
  if (S?.desktop) { const r = await api('/api/pick-folder', {start: BRK.path}); if (r.path) brokenReload(r.path); }
  else { $('brkPathRow').hidden = false; $('brkPath').focus(); }
};
$('brkPathGo').onclick = () => $('brkPath').value.trim() && brokenReload($('brkPath').value.trim());
$('brkRemove').onclick = async () => {
  try { const r = await api(`/api/broken/${BRK.slug}/remove`, {}); $('brokenDlg').close(); alertBox(r.message, true); loadState(); }
  catch (e) { $('brkMsg').textContent = e.message; }
};
$('hideBtn').onclick = async () => { const h = view.project.hidden; if (await projAction(h ? 'unhide' : 'hide', {})) { $('projDlg').close(); if (!h) { await loadState(); open(null); } else { await refreshView(); loadState(); } } };
$('removeBtn').onclick = async () => { if (!confirm('Remove this project from AI Team Lite? Your folder and files are kept.')) return; const r = await projAction('delete', {mode: 'remove'}); if (r) { $('projDlg').close(); alertBox(r.message, true); await loadState(); open(null); } };
$('delBtn').onclick = async () => { const r = await projAction('delete', {mode: 'all', confirm: $('delConfirm').value.trim()}); if (r) { $('projDlg').close(); alertBox(r.message, true); await loadState(); open(null); } };
$('settingsBtn').onclick = () => {
  $('setMode').value = S.settings.approval_mode; $('setPar').value = String(S.max_parallel || 2); $('setIterPause').checked = !!S.settings.iteration_pause;
  $('setPipeAuto').checked = S.settings.pipeline_auto !== false; $('setTray').checked = !!S.settings.close_to_tray;
  $('setName').value = S.settings.user_name || '';
  const onTab = document.querySelector('.stab.on')?.dataset.s;
  if (onTab === 'brains') renderBrains();
  if (onTab === 'setup') renderSetupTab();
  $('setDlg').showModal();
};
$('setCancel').onclick = () => { $('setDlg').close(); renderSide(); };
$('setSave').onclick = async e => {
  e.preventDefault();
  const body = {approval_mode: $('setMode').value, max_parallel: parseInt($('setPar').value), iteration_pause: $('setIterPause').checked,
    pipeline_auto: $('setPipeAuto').checked, close_to_tray: $('setTray').checked, user_name: $('setName').value.trim()};
  try { await api('/api/settings', body); $('setDlg').close(); await loadState(); renderSide(); } catch (err) { alertBox(err.message); }
};
/* ---------------------------------------------- Setup & connections tab */
function renderSetupTab() {
  $('subOut').hidden = S.auth_mode !== 'subscription';
  const gh = S.github_connected;
  $('clearGh').hidden = !gh;
  const rows = [
    ['How Fred thinks', S.auth_mode === 'subscription' ? 'Your Claude subscription' : (S.api_key_saved ? 'An Anthropic API key' : 'Not connected'), true],
    ['Projects folder', S.settings.workspace_dir, true],
    ['GitHub', gh ? 'Connected as ' + esc(S.settings.github_login || '?') : 'Not connected', gh],
    ['Helper brains', (S.brains_count || 0) + ' added', (S.brains_count || 0) > 0],
  ];
  $('connList').innerHTML = rows.map(([k, v, ok]) =>
    `<div class="env"><span>${esc(k)}</span><span class="${ok ? 'ok' : 'no'}">${v}</span><span></span></div>`).join('');
  $('setInfo').textContent = 'Version ' + (S.version || '') + '. Your saved keys and sign-in stay in Windows and are never shown to the AI or put in a shared copy.';
  renderShareBox();
}
async function renderShareBox() {
  const box = $('shareBox'); box.innerHTML = '<p class="hint">Checking…</p>';
  let u; try { u = await api('/api/app/update-status'); } catch (e) { box.innerHTML = '<p class="hint">Could not check sharing.</p>'; return; }
  if (u.is_owner) {
    let line = `You share AI Team Lite from <a href="${esc(u.repo)}" target="_blank" rel="noopener">this repository</a>. When you install a newer version it is published there automatically, so your friends can update.`;
    box.innerHTML = `<p class="hint">${line}</p>`;
  } else if (u.sharing || u.repo) {
    if (u.available) box.innerHTML = `<p class="hint"><b style="color:var(--warn)">Update available: ${esc(u.latest)}</b> (you have ${esc(u.current)}).${u.busy ? ' Finish or pause current work first, then get it from' : ' Get it from'} <a href="${esc(u.repo)}" target="_blank" rel="noopener">the repository</a>.</p>`;
    else box.innerHTML = `<p class="hint">You're up to date (version ${esc(u.current)}). Updates come from <a href="${esc(u.repo)}" target="_blank" rel="noopener">the author's repository</a>.</p>`;
  } else {
    box.innerHTML = `<p class="hint">Publish AI Team Lite to a GitHub repository once, to share it with friends. After that, every time you install a newer version it updates there automatically — friends can get your updates but cannot publish the app themselves. Nothing private (keys, sign-in, your notes) is ever included; AI Team Lite checks before publishing.</p>
      <div class="row" style="flex-wrap:wrap"><button type="button" id="shareSetup">Create a new repository</button><label class="row" style="gap:6px;width:auto"><input type="checkbox" id="sharePriv" style="width:auto"> Private repository (only you)</label></div>
      <div class="row" style="flex-wrap:wrap;margin-top:6px"><input type="text" id="shareLinkRepo" placeholder="or use one you already have, e.g. XaeriS/ai-team-lite" style="flex:1;min-width:220px"><button type="button" id="shareLink">Link this repository</button></div>
      <p id="shareMsg" class="hint" style="min-height:16px"></p>`;
    $('shareLink').onclick = async () => {
      const repo = $('shareLinkRepo').value.trim(); if (!repo) { $('shareMsg').textContent = 'Type the repository, e.g. XaeriS/ai-team-lite.'; return; }
      if (!S.github_connected) { $('shareMsg').textContent = 'Connect GitHub first (the button above).'; return; }
      $('shareLink').disabled = true; $('shareMsg').textContent = 'Checking the repository and publishing this version…';
      try { const r = await api('/api/app/link-sharing', {repo});
        $('shareMsg').innerHTML = `Done: AI Team Lite ${esc(S.version)} is published at <a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.full_name)}</a>. New versions you install will be published there automatically.`;
        await loadState(); renderShareBox();
      } catch (e) { $('shareMsg').textContent = e.message; $('shareLink').disabled = false; }
    };
    $('shareSetup').onclick = async () => {
      if (!S.github_connected) { $('shareMsg').textContent = 'Connect GitHub first (the button above).'; return; }
      const name = prompt('Name for the GitHub repository:', 'ai-team-lite'); if (!name) return;
      $('shareSetup').disabled = true; $('shareMsg').textContent = 'Setting up… this creates the repository and publishes this version.';
      try { const r = await api('/api/app/setup-sharing', {name, private: $('sharePriv').checked});
        $('shareMsg').innerHTML = `Done — shared at <a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.url)}</a>.`;
        await loadState(); renderShareBox();
      } catch (e) { $('shareMsg').textContent = e.message; $('shareSetup').disabled = false; }
    };
  }
}
$('setupFredBtn').onclick = async () => {
  if (!confirm('Reopen setup with Fred to change something (folder, GitHub, approvals)?')) return;
  await api('/api/setup/restart', {});
  $('setDlg').close(); await loadState(); open('setup');
};
$('replaceKey').onclick = () => { $('setDlg').close(); $('onboard').hidden = false; $('keyIn').focus(); };
$('subOut').onclick = async () => { if (confirm('Sign out of your Claude subscription in AI Team Lite?')) { await api('/api/auth/subscription/logout', {}); $('setDlg').close(); await loadState(); } };
let subTimer = null;
$('subBtn').onclick = async () => {
  $('keyErr').textContent = ''; $('subBtn').disabled = true;
  try { await api('/api/auth/subscription/start', {}); }
  catch (e) { $('keyErr').textContent = e.message; $('subBtn').disabled = false; return; }
  $('subStatus').innerHTML = '<span class="spin"></span> Finish signing in in your browser… (a small window also opened; leave it open)';
  let tries = 0;
  clearInterval(subTimer);
  subTimer = setInterval(async () => {
    tries++;
    let st = {}; try { st = await api('/api/auth/subscription/check', {}); } catch (e) {}
    if (st.signed_in) {
      clearInterval(subTimer); $('subStatus').textContent = 'Signed in.'; $('subBtn').disabled = false;
      await loadState(); open(S.setup_complete ? (cur || null) : 'setup');
    } else if (tries > 100) {
      clearInterval(subTimer); $('subBtn').disabled = false;
      $('subStatus').textContent = 'Not signed in yet. Click the button to try again.';
    }
  }, 3000);
};
$('clearGh').onclick = async () => { if (confirm('Remove the saved GitHub token from this PC?')) { await api('/api/secrets/clear', {name: 'github_token'}); $('setDlg').close(); loadState(); } };

/* ------------------------------------------------------------------ onboarding */
$('keyBtn').onclick = async () => {
  const key = $('keyIn').value.trim(); $('keyErr').textContent = '';
  if (!key) return;
  $('keyBtn').disabled = true; $('keyBtn').textContent = 'Checking…';
  try {
    await api('/api/onboarding/key', {key}); $('keyIn').value = '';
    await loadState(); open(S.setup_complete ? (cur || null) : 'setup');
  } catch (e) { $('keyErr').textContent = e.message; }
  $('keyBtn').disabled = false; $('keyBtn').textContent = 'Save and continue';
};
$('keyIn').onkeydown = e => { if (e.key === 'Enter') $('keyBtn').click(); };
