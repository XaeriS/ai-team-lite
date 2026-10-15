/* AI Team Lite - side panel.
   Team graph, requirements, architecture, plan, diagrams and tests tabs.
   Classic scripts loaded in order by index.html; they share globals (TOKEN, $, S, cur, view...). */
/* ------------------------------------------------------------------ side panel */
function renderSide() {
  const tabs = cur === 'setup' ? [['setup', 'Setup checklist']] : [['team', 'Team'], ...(view?.project?.existing_folder ? [['status', 'Status']] : []), ['req', 'Requirements'], ['arch', 'Architecture'], ['plan', 'Plan'], ['diagrams', 'Diagrams'], ['tests', 'Tests']];
  if (!tabs.some(t => t[0] === tab)) tab = tabs[0][0];
  $('tabs').innerHTML = tabs.map(([k, l]) => `<button class="tab ${k === tab ? 'on' : ''}" data-t="${k}">${l}</button>`).join('');
  $('tabs').querySelectorAll('button').forEach(b => b.onclick = () => { tab = b.dataset.t; renderSide(); });
  const p = $('pane');
  if (!cur || !view) { p.innerHTML = '<p class="empty">Nothing here yet.</p>'; return; }
  if (tab === 'setup') {
    const s = S || {settings: {}};
    const rows = [[s.has_api_key, 'Connected to Claude', s.auth_mode === 'subscription' ? 'Your Claude subscription' : 'API key, saved securely'], [!!s.settings.user_name, 'Your name', s.settings.user_name || ''],
      [!!(s.settings.workspace_dir), 'Projects folder', s.settings.workspace_dir || ''], [s.github_connected, 'GitHub (optional)', s.settings.github_login ? 'Connected as ' + s.settings.github_login : 'Not connected'],
      [true, 'Approvals', s.settings.approval_mode === 'strict' ? 'Ask before every action' : 'Approve each plan'], [s.setup_complete, 'Setup finished', '']];
    p.innerHTML = rows.map(([ok, t, d]) => `<div class="check"><div class="dot ${ok ? 'ok' : ''}">${ok ? '✓' : ''}</div><div><b>${esc(t)}</b><div class="hint">${esc(d)}</div></div></div>`).join('') +
      '<p class="hint" style="margin-top:12px">Fred goes through these with you one at a time and asks before changing anything.</p>';
  } else if (tab === 'team') { renderTeam();
  } else if (tab === 'req') { renderReq();
  } else if (tab === 'status') {
    p.innerHTML = view.docs.status ? `<div class="doc">${md(view.docs.status)}</div>` : `<p class="empty">Fred scans your folder and writes where the project stands here.</p><p class="hint" style="text-align:center">Folder: <code>${esc(view.project.folder || '')}</code></p>`;
  } else if (tab === 'arch') {
    const ok = view.project.architecture_approved;
    p.innerHTML = view.docs.architecture ? `<div class="row" style="margin-bottom:6px">${ok ? '<span class="pill ok">approved</span>' : '<span class="pill warn">not approved yet</span>'}</div><div class="doc">${md(view.docs.architecture)}</div>`
      : '<p class="empty">Once the requirements are agreed, Mat, the lead architect, asks how things should work (data sources and refresh, storage, encryption, where it runs) and writes the architecture here for you to approve.</p>';
  } else if (tab === 'plan') {
    const its = view.project.iterations || [];
    const list = its.length ? `<h4 style="margin:0 0 6px">Iterations ${view.project.plan_approved ? '' : '<span class="pill warn">waiting for your approval</span>'}</h4><div class="itlist">${its.map(it => `<div class="itrow"><div class="itnum ${esc(it.status)}">${it.status === 'done' ? '✓' : it.n}</div>
      <div style="flex:1"><b>${esc(it.name)}</b> <span class="pill ${it.status === 'done' ? 'ok' : it.status === 'building' ? 'acc' : view.project.waiting_release === it.n ? 'warn' : ''}">${esc(it.status === 'building' ? 'in progress' : view.project.waiting_release === it.n ? 'waiting for release' : it.status)}</span>${it.commit ? ` <code class="hint">${esc(it.commit)}</code>` : ''}
      ${view.project.waiting_release === it.n ? ` <button type="button" class="primary" data-release="${it.n}" style="padding:2px 10px">Release</button>` : ''}
      <div class="hint">${esc(it.goal || '')}${(it.requirements || []).length ? ' · ' + esc(it.requirements.join(', ')) : ''}</div>
      ${it.status !== 'done' && it.n !== its[its.length - 1].n ? `<label class="hint">When finished: <select data-after="${it.n}" style="width:auto;padding:2px 6px"><option value="continue" ${it.after !== 'pause' ? 'selected' : ''}>continue automatically</option><option value="pause" ${it.after === 'pause' ? 'selected' : ''}>pause for my release</option></select></label>` : ''}</div></div>`).join('')}</div>` : '';
    const est = view.project.estimate || {};
    const estLine = est.so_far || est.remaining ? `<p class="hint">💲 So far ${usd(est.so_far)}${est.remaining ? ` · rest of the plan about ${usd(est.remaining.low)}–${usd(est.remaining.high)}` : ''} at the providers' prices${S?.auth_mode === 'subscription' ? ' (not billed with your subscription)' : ''}.</p>` : '';
    p.innerHTML = view.docs.plan ? `${list}${estLine}<div class="doc">${md(its.length ? view.docs.plan.split('\n## Iterations\n')[0] : view.docs.plan)}</div>` : '<p class="empty">The plan, split into iterations, appears here once the requirements are agreed.</p>';
    p.querySelectorAll('[data-after]').forEach(sel => sel.onchange = () => api(`/api/projects/${cur}/iteration-after`, {n: +sel.dataset.after, after: sel.value}).then(refreshView).catch(e => alertBox(e.message)));
    p.querySelectorAll('[data-release]').forEach(b => b.onclick = () => api(`/api/projects/${cur}/release`, {n: +b.dataset.release}).then(refreshView).catch(e => alertBox(e.message)));
  } else if (tab === 'diagrams') { renderDiagrams();
  } else { renderTests(); }
}

/* ------------------------------------------------------------------ team graph (request C) */
const ST = {idle: {c: '#868e96', bg: 'var(--code)', label: 'idle'}, working: {c: '#2f9e44', bg: '#d3f9d8', label: 'working'},
            question: {c: '#f59f00', bg: '#fff3bf', label: 'has a question'}};
let teamSel = null, teamChecked = Date.now();
function badge(x, y, status) {
  const s = ST[status] || ST.idle;
  let icon = '';
  if (status === 'working') icon = `<g class="spinning"><path d="M${x - 4} ${y} a4 4 0 1 1 1.2 2.9" fill="none" stroke="#fff" stroke-width="1.6" stroke-linecap="round"/><path d="M${x - 5.5} ${y + 3.2} l1.6 -0.4 l-0.3 -1.7" fill="none" stroke="#fff" stroke-width="1.4"/></g>`;
  else if (status === 'question') icon = `<text x="${x}" y="${y + 4}" text-anchor="middle" font-size="11" font-weight="700" fill="#fff">?</text>`;
  else icon = `<rect x="${x - 3}" y="${y - 3}" width="2" height="6" fill="#fff"/><rect x="${x + 1}" y="${y - 3}" width="2" height="6" fill="#fff"/>`;
  return `<circle cx="${x}" cy="${y}" r="9" fill="${s.c}" stroke="var(--panel)" stroke-width="2"/>${icon}`;
}
const shortName = r => { const n = r.kind === 'brain' ? r.name.split(' · ')[0] : r.name; return n.length > 12 ? n.slice(0, 11) + '…' : n; };
let stallChecked = 0;
function renderTeam() {
  const p = $('pane'); if (!view) return;
  const team = view.team || [];
  if (team.some(r => r.stalled) && Date.now() - stallChecked > 5 * 60 * 1000 && cur && cur !== 'setup') {
    stallChecked = Date.now(); api(`/api/projects/${cur}/watch-check`, {}).catch(() => {});
  }
  const lead = team.find(r => r.kind === 'lead') || {name: 'Fred', status: 'idle'};
  const arch = team.find(r => r.kind === 'architect');
  const workers = team.filter(r => r.kind === 'worker'), brainsN = team.filter(r => r.kind === 'brain');
  const W = 340, bw = 96, bh = 46;
  const rowX = (n, i) => (W - n * bw - (n - 1) * 12) / 2 + i * (bw + 12);
  const node = (r, x, y) => {
    const s = ST[r.status] || ST.idle, stalled = r.stalled;
    return `<g class="tnode" data-n="${esc(r.name)}"><rect class="box" x="${x}" y="${y}" width="${bw}" height="${bh}" rx="10" fill="${s.bg}" stroke="${s.c}" stroke-width="1.5" ${stalled ? 'stroke-dasharray="4 3"' : ''}/>
      <text x="${x + bw / 2}" y="${y + 19}" text-anchor="middle" font-size="12.5" font-weight="600" fill="#1b1f24">${esc(shortName(r))}</text>
      <text x="${x + bw / 2}" y="${y + 34}" text-anchor="middle" font-size="10.5" fill="#495057">${esc(stalled ? 'no activity 5 min' : s.label)}</text>${badge(x + bw - 4, y + bh - 4, r.status)}</g>`;
  };
  const fx = (W - bw) / 2, fy = 8, wy = 96, by = 186;
  let svg = '', lines = '';
  workers.forEach((r, i) => { const x = rowX(workers.length, i); lines += `<path d="M${W / 2} ${fy + bh} C ${W / 2} ${wy - 20}, ${x + bw / 2} ${fy + bh + 20}, ${x + bw / 2} ${wy}" fill="none" stroke="${r.status === 'idle' ? 'var(--line)' : (ST[r.status] || ST.idle).c}" stroke-width="1.6"/>`; svg += node(r, x, wy); });
  brainsN.forEach((r, i) => { const n = Math.min(brainsN.length, 3), x = rowX(n, i % 3), y = by + Math.floor(i / 3) * 60; lines += `<path d="M${W / 2} ${fy + bh} C ${W / 2 - 160} ${wy + 30}, ${x + bw / 2} ${y - 40}, ${x + bw / 2} ${y}" fill="none" stroke="var(--line)" stroke-width="1.2" stroke-dasharray="4 4"/>`; svg += node(r, x, y); });
  const H = brainsN.length ? by + Math.ceil(brainsN.length / 3) * 60 : wy + bh + 10;
  let top = node(lead, fx, fy);
  if (arch) { const ax = W - bw - 4; lines += `<path d="M${fx + bw} ${fy + bh / 2} L${ax} ${fy + bh / 2}" stroke="${arch.status === 'idle' ? 'var(--line)' : (ST[arch.status] || ST.idle).c}" stroke-width="1.6" stroke-dasharray="5 3"/>`; top += node(arch, ax, fy); }
  svg = `<svg class="team-svg" viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Team status">${lines}${top}${svg}</svg>`;
  const ago = s => { if (!s) return ''; const m = Math.round((Date.now() / 1000 - s) / 60); return m < 1 ? 'just now' : m + ' min ago'; };
  const list = [lead, ...(arch ? [arch] : []), ...workers, ...brainsN].map(r => `<div class="trow" data-n="${esc(r.name)}"><div class="tdot" style="background:${(ST[r.status] || ST.idle).c}"></div>
    <div style="flex:1"><b>${esc(r.name)}</b> <span class="hint">${esc(r.stalled ? 'working, but no activity for 5 minutes' : (ST[r.status] || ST.idle).label)}</span>
    <div>${esc(r.task || (r.status === 'idle' ? 'Waiting for work' : ''))}</div>${r.last ? `<div class="hint">Last: ${esc(r.last)} · ${ago(r.last_time)}</div>` : ''}
    ${teamSel === r.name && (r.recent || []).length ? `<ul class="hint" style="margin:4px 0;padding-left:18px">${r.recent.map(a => `<li>${esc(a.text)} · ${ago(a.time)}</li>`).join('')}</ul>` : ''}</div></div>`).join('');
  p.innerHTML = `${svg}<div class="legend" style="margin:6px 0 4px"><span><i style="background:#868e96"></i>idle</span><span><i style="background:#2f9e44"></i>working</span><span><i style="background:#f59f00"></i>has a question</span></div>
    <div class="hint">Live; re-checked every 5 minutes (last check ${new Date(teamChecked).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})}). Click a member to see its recent activity.</div><div class="tlist">${list}</div>`;
  p.querySelectorAll('[data-n]').forEach(e => e.onclick = () => { teamSel = teamSel === e.dataset.n ? null : e.dataset.n; renderTeam(); });
}
setInterval(async () => { if (cur && cur !== 'setup') { try { view.team = (await api(`/api/projects/${cur}/team`)).team; teamChecked = Date.now(); if (tab === 'team') renderTeam(); } catch (e) {} } }, 5 * 60 * 1000);

/* ------------------------------------------------------------------ code changes between iterations (item 45) */
function openDiagram(slug, id, which, title) {
  $('graphTitle').textContent = (which === 'compare' ? 'What changed: ' : '') + (title || 'Diagram');
  $('graphHint').textContent = which === 'compare' ? 'Parts added, removed, moved or connected differently since the previous version.'
    : 'Click a part to see what it connects to; use Flow to watch the path. Export saves a picture.';
  $('graphFrame').src = `/diagram/${slug}/${id}?which=${which}&token=${encodeURIComponent(TOKEN)}`;
  $('graphDlg').showModal();
}
async function renderDiagrams() {
  // "How it works" diagrams drawn with Archify by the team's diagrammer (item 78)
  const p = $('pane'), slug = cur;
  let d; try { d = await api(`/api/projects/${slug}/diagrams`); } catch (e) { p.innerHTML = '<p class="empty">Could not load the diagrams.</p>'; return; }
  if (slug !== cur || tab !== 'diagrams') return;
  const ask = `<div class="row" style="margin:10px 0;gap:6px"><input type="text" id="dgQ" placeholder="Ask for a diagram, e.g. How does a file get imported?" style="flex:1"><button type="button" class="primary" id="dgAsk">Ask Fred</button></div>
    <p class="hint">Each diagram is drawn from the real code and checked, so it uses some Claude tokens.</p>`;
  const why = !d.available ? '<p class="hint">Diagrams need the Archify drawing tool, which comes with the AI Team Lite installer. It is missing or could not draw on this PC, so diagrams are switched off; everything else works normally.</p>'
    : '';
  const items = (d.items || []).slice().reverse(), latest = {}, older = [];
  items.forEach(x => { if (!latest[x.topic]) latest[x.topic] = x; else older.push(x); });
  const when = x => new Date(x.created * 1000).toLocaleDateString([], {month: 'short', day: 'numeric'}) + (x.iteration ? ` · after iteration ${x.iteration}` : '');
  const card = x => `<div class="itcard" style="margin:6px 0"><b>${esc(x.title)}</b> <span class="pill">${esc(x.kind)}</span>
      <div class="hint">${esc(x.question || '')} ${when(x)}</div>
      <div class="acts" style="margin-top:6px"><button class="primary" data-open="${esc(x.id)}">Open</button>${x.compare ? `<button data-cmp="${esc(x.id)}">What changed</button>` : ''}</div></div>`;
  const tops = Object.values(latest).sort((a, b) => (b.topic === 'architecture') - (a.topic === 'architecture'));
  p.innerHTML = why + (tops.length ? tops.map(card).join('') : (why ? '' : '<p class="empty">No diagram yet. Fred asks for the overall picture once the architecture is approved, and after iterations that change it. You can also ask for one below.</p>'))
    + (older.length ? `<details style="margin-top:8px"><summary class="hint" style="cursor:pointer">Earlier versions (${older.length})</summary>${older.map(card).join('')}</details>` : '')
    + (d.available && d.enabled ? ask : '');
  const byId = Object.fromEntries((d.items || []).map(x => [x.id, x]));
  p.querySelectorAll('[data-open]').forEach(b => b.onclick = () => openDiagram(slug, b.dataset.open, 'html', byId[b.dataset.open].title));
  p.querySelectorAll('[data-cmp]').forEach(b => b.onclick = () => openDiagram(slug, b.dataset.cmp, 'compare', byId[b.dataset.cmp].title));
  if ($('dgAsk')) $('dgAsk').onclick = async () => {
    const q = $('dgQ').value.trim(); if (!q) return;
    try { await api(`/api/projects/${slug}/message`, {text: `Please draw a diagram that shows: ${q}`}); $('dgQ').value = ''; alertBox('Asked Fred for the diagram. It appears here when it is ready.', true); }
    catch (e) { alertBox(e.message); }
  };
}

/* ------------------------------------------------------------------ tests view (request I) */
async function renderTests() {
  const p = $('pane'); const slug = cur;
  let d; try { d = await api(`/api/projects/${slug}/tests`); } catch (e) { p.innerHTML = '<p class="empty">Could not load the tests.</p>'; return; }
  if (slug !== cur || tab !== 'tests') return;
  const res = d.results.slice().reverse();
  const count = k => d.results.filter(r => r.status === k).length;
  const pill = s => `<span class="pill ${s === 'passed' ? 'ok' : s === 'failed' ? 'bad' : ''}">${esc(s)}</span>`;
  let h = `<div class="row" style="margin-bottom:8px"><span class="pill ok">${count('passed')} passed</span><span class="pill bad">${count('failed')} failed</span><span class="sp"></span>
    <button type="button" id="openTests">📂 Open tests folder</button></div>`;
  h += res.length ? res.map(r => `<div class="trow-test"><div class="row">${pill(r.status)}<b style="flex:1">${esc(r.name || 'Test')}</b>${r.requirement ? `<span class="pill acc">${esc(r.requirement)}</span>` : ''}</div>
      <div class="hint">${r.time ? new Date(r.time * 1000).toLocaleString() : ''}${r.caption ? ' · ' + esc(r.caption) : ''}</div>
      ${r.details ? `<details><summary class="hint" style="cursor:pointer">Details</summary><pre style="white-space:pre-wrap;font-size:12px">${esc(r.details)}</pre></details>` : ''}
      ${r.screenshot ? `<img src="${fileUrl(r.screenshot)}" alt="">` : ''}</div>`).join('')
    : '<p class="empty">No test results yet. The Tester records each test here with its screenshot.</p>';
  if (d.screenshots.length) h += `<h4 style="margin:14px 0 4px">Other screenshots</h4>` + d.screenshots.slice().reverse().map(s => `<div class="trow-test"><div class="hint">${esc(s.split('/').pop())}</div><img src="${fileUrl(s)}" alt=""></div>`).join('');
  p.innerHTML = h;
  p.querySelectorAll('img').forEach(i => i.onclick = () => lightbox(i.src));
  $('openTests').onclick = () => api(`/api/projects/${slug}/open-reports`, {});
}
