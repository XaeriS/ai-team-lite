/* AI Team Lite - core.
   Server calls, safe markdown, app state and navigation between projects.
   Classic scripts loaded in order by index.html; they share globals (TOKEN, $, S, cur, view...). */
const $ = id => document.getElementById(id);
let S = null, cur = null, view = null, tab = 'team', attachments = [], es = null, lastSeq = 0;

/* ------------------------------------------------------------------ api */
async function api(path, body) {
  const opts = {headers: {'X-Token': TOKEN, 'Content-Type': 'application/json'}};
  if (body !== undefined) { opts.method = 'POST'; opts.body = JSON.stringify(body); }
  const r = await fetch(path, opts); const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || ('Error ' + r.status));
  return j;
}

/* ------------------------------------------------------------------ safe markdown */
const esc = s => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
function inline(s) {
  return esc(s).replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
    .replace(/(^|[^*\w])\*([^*\n]+)\*/g, '$1<i>$2</i>')
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>');
}
function md(src) {
  src = String(src || '').replace(/^---\n[\s\S]*?\n---\n/, '');
  const out = [], L = src.split('\n'); let i = 0; const st = [];
  const close = (n = 0) => { while (st.length > n) out.push('</' + st.pop() + '>'); };
  while (i < L.length) {
    const line = L[i];
    if (line.startsWith('```')) { close(); const b = []; i++; while (i < L.length && !L[i].startsWith('```')) b.push(L[i++]); out.push('<pre><code>' + esc(b.join('\n')) + '</code></pre>'); i++; continue; }
    let m = line.match(/^(#{1,6})\s+(.*)$/);
    if (m) { close(); const n = Math.min(m[1].length, 4); out.push(`<h${n}>${inline(m[2])}</h${n}>`); i++; continue; }
    if (/^\s*\|.*\|\s*$/.test(line) && /^\s*\|[\s:|-]+\|\s*$/.test(L[i + 1] || '')) {
      close(); const h = line.trim().slice(1, -1).split('|'); i += 2; const rows = [];
      while (i < L.length && /^\s*\|.*\|\s*$/.test(L[i])) rows.push(L[i++].trim().slice(1, -1).split('|'));
      out.push('<table><tr>' + h.map(c => `<th>${inline(c.trim())}</th>`).join('') + '</tr>' + rows.map(r => '<tr>' + r.map(c => `<td>${inline(c.trim())}</td>`).join('') + '</tr>').join('') + '</table>'); continue;
    }
    m = line.match(/^(\s*)([-*]|\d+\.)\s+(.*)$/);
    if (m) {
      const d = Math.floor(m[1].length / 2) + 1, tag = /\d/.test(m[2]) ? 'ol' : 'ul';
      while (st.length > d) out.push('</' + st.pop() + '>');
      while (st.length < d) { out.push('<' + tag + '>'); st.push(tag); }
      let t = m[3]; const cb = t.match(/^\[( |x)\]\s+(.*)$/i); if (cb) t = (cb[1] === ' ' ? '☐ ' : '☑ ') + cb[2];
      out.push('<li>' + inline(t) + '</li>'); i++; continue;
    }
    close(); if (line.trim()) out.push('<p>' + inline(line) + '</p>'); i++;
  }
  close(); return out.join('\n');
}

/* ------------------------------------------------------------------ state & navigation */
// Claude reports plan usage only when its status changes (getting close, limit hit, back to normal), so when there
// is no warning we say so instead of showing an old number (items 61, 62).
function planPct(v) {
  if (v.percent != null) return Math.round(v.percent) + '%';
  if (v.status === 'rejected') return 'limit reached';
  if (v.below_warning && !v.no_report && !v.stale) return 'below the warning level';
  return 'not reported yet';
}
const resetOf = v => v.reset_text ? 'resets ' + v.reset_text : resetTxt(v.resets_at);
let USE_AT = Date.now();
async function loadState() {
  S = await api('/api/state'); USE_AT = Date.now();
  $('onboard').hidden = S.has_api_key;
  const ul = $('plist'); ul.innerHTML = '';
  if (!S.setup_complete) {
    const li = document.createElement('li'); li.className = cur === 'setup' ? 'sel' : '';
    li.innerHTML = '<span class="nm">⚙ First-time setup</span><span class="pill warn">to do</span>'; li.onclick = () => open('setup'); ul.appendChild(li);
  }
  const nHidden = S.projects.filter(p => p.hidden).length;
  if (!nHidden) showHidden = false;
  S.projects.filter(p => showHidden || !p.hidden || p.slug === cur).forEach(p => {
    const li = document.createElement('li'); li.className = (p.slug === cur ? 'sel ' : '') + (p.hidden ? 'hid' : '');
    const sub = p.phase === 'execute' && p.progress && p.progress.building ? 'Iteration ' + p.progress.current + '/' + p.progress.total : phaseLabel(p.phase);
    li.innerHTML = `<span class="nmw"><span class="nm">${esc(p.name)}${p.hidden ? ' (hidden)' : ''}</span><span class="sub">${esc(sub)}</span></span>${stateHtml(p.state)}`;
    li.title = p.existing ? 'Started from an existing folder' : '';
    li.onclick = () => open(p.slug); ul.appendChild(li);
  });
  (S.broken || []).forEach(b => {
    const li = document.createElement('li'); li.className = 'broken';
    li.innerHTML = `<span class="nmw"><span class="nm">⚠ ${esc(b.name)}</span><span class="sub">can't be opened — click to fix</span></span>`;
    li.title = b.reason; li.onclick = () => openBroken(b); ul.appendChild(li);
  });
  $('hiddenBtn').hidden = !nHidden; $('hiddenBtn').textContent = showHidden ? `Hide hidden projects` : `Show hidden projects (${nHidden})`;
  renderMeter(); renderCost(view?.project); updateStateP();
  const nb = S.projects.filter(p => p.busy).length; $('workCount').textContent = nb ? `${nb} working` : ''; $('workCount').className = 'pill ok'; $('workCount').hidden = !nb;
  const fn = S.can_manage_features ? (S.features_new || 0) : 0; $('featCount').textContent = fn ? `${fn} new` : ''; $('featCount').className = 'pill warn'; $('featCount').hidden = !fn;
  $('newBtn').disabled = !S.setup_complete;
  $('newBtn').title = S.setup_complete ? '' : 'Finish the first-time setup with Fred first';
  if (!lastSeq) lastSeq = S.seq;
  return S;
}
let showHidden = false, lsTimer = null;
const softState = () => { clearTimeout(lsTimer); lsTimer = setTimeout(loadState, 300); };
function updateStateP() {
  const p = view?.project, sp = $('stateP');
  if (!p || p.slug === 'setup') { sp.hidden = true; return; }
  const live = (S?.projects || []).find(x => x.slug === p.slug);
  sp.outerHTML = stateHtml(live?.state || p.state || 'idle').replace('<span ', '<span id="stateP" ');
}
const STATE_LABEL = {working: 'Working', 'needs you': 'Needs you', paused: 'Paused', limit: 'Usage limit', waiting: 'Waiting', release: 'Waiting for release', idle: 'Idle'};
const stateHtml = st => { st = st || 'idle'; return `<span class="state ${esc(st.split(' ')[0])}" title="${esc(st === 'idle' ? 'Nothing is in progress right now' : st === 'needs you' ? 'Waiting for your answer in the chat' : STATE_LABEL[st])}">${esc(STATE_LABEL[st] || st)}</span>`; };
const leadName = () => view?.project?.phase === 'architecture' ? 'Mat' : 'Fred';
const iterLabel = (pr, phase) => pr && pr.total ? (phase === 'execute' && pr.building ? ` · iteration ${pr.current} of ${pr.total}` : ` · ${pr.done}/${pr.total} iterations done`) : '';
const phaseLabel = p => ({discover: 'Requirements', architecture: 'Architecture', plan: 'Planning', execute: 'Building', review: 'Your review', setup: 'Setup'})[p] || p;

let early = [];  // events of the project being opened that arrive while its history is loading
async function open(slug) {
  cur = slug || null; attachments = []; renderChips();
  view = null; early = [];
  if (cur) { try { view = await api('/api/projects/' + cur); } catch (e) {
    view = null;
    if (cur !== 'setup') {  // never leave an empty chat that answers "Unknown project" (item 66)
      cur = null; await loadState();
      alertBox('This project could not be opened. If it shows in the list with ⚠, click it to reload it from its folder or remove it.');
    }
  } }
  renderHeader(); renderAll();
  // the history was read at one moment: add what arrived while it loaded (e.g. Fred's first greeting)
  const have = new Set(items.map(x => x.seq)), late = early.filter(e => e.slug === cur && !have.has(e.seq)); early = [];
  late.forEach(onEvent);
  renderSide(); loadState();
  $('input').focus();
}
function renderHeader() {
  const p = view?.project;
  $('title').textContent = p ? p.name : 'AI Team Lite';
  const ph = $('phase'); ph.hidden = !p; if (p) { ph.textContent = phaseLabel(p.phase) + iterLabel(view.progress, p.phase); ph.className = 'pill ' + (p.phase === 'execute' ? 'acc' : p.phase === 'review' ? 'ok' : ''); }
  renderCost(p);
  $('folderBtn').hidden = !p || p.slug === 'setup';
  $('pauseBtn').hidden = !p || p.slug === 'setup';
  $('menuBtn').hidden = !p || p.slug === 'setup';
  $('launchBtn').hidden = !p || !p.launch;
  if (p && p.launch) $('launchBtn').title = `Start ${p.launch.version || 'the app'} (${p.launch.command}${p.launch.url ? ', ' + p.launch.url : ''})`;
  updateStateP();
  $('input').placeholder = `Message ${p ? leadName() : 'Fred'}…  (Enter to send, Shift+Enter for a new line)`;
  if (p) { $('pauseBtn').textContent = p.paused ? '▶ Resume' : '⏸ Pause'; if (p.paused) { ph.textContent = 'Paused'; ph.className = 'pill warn'; } }
  setBusy(view?.busy);
}
