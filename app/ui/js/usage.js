/* AI Team Lite - usage.
   The usage meter and the Usage window (cost, periods, agents, phases, time, plan room).
   Classic scripts loaded in order by index.html; they share globals (TOKEN, $, S, cur, view...). */
/* ------------------------------------------------------------------ usage */
const pctBar = v => `<div class="bar"><i class="${v >= 90 ? 'max' : v >= 75 ? 'hi' : ''}" style="width:${Math.min(100, v || 0)}%"></i></div>`;
const resetTxt = r => r ? 'resets ' + new Date(r * 1000).toLocaleString([], {weekday: 'short', hour: '2-digit', minute: '2-digit'}) : '';
const hm = t => new Date(t * 1000).toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'});
const pctTxt = v => v && v.percent != null ? `${Math.round(v.percent)}%` : v && v.status === 'rejected' ? 'limit reached' : 'OK';
const usd = v => v == null ? '–' : v < 0.01 && v > 0 ? '<$0.01' : '$' + Number(v).toFixed(2);
function renderCost(p) {
  const c = $('cost'), est = p && p.estimate;
  if (S?.auth_mode === 'subscription') {
    const pu = S.plan_usage || {}, five = pu.five_hour, week = pu.seven_day;
    const plan = five || week ? [five && `Session ${pctTxt(five)}`, week && `Week ${pctTxt(week)}`].filter(Boolean).join(' · ') : 'Using your Claude subscription';
    c.textContent = plan;
    c.title = 'Covered by your subscription: nothing is billed. Click the usage meter (bottom left) for details.';
  } else {
    c.textContent = p && (p.cost_usd || (est && est.so_far)) ? `API cost for this project: ${usd(p.cost_usd || est.so_far)}` : '';
    c.title = 'Billed to your Anthropic API account (helper brains by their providers)';
  }
}
function renderMeter() {
  const m = $('usageMeter'); if (!S || !S.has_api_key) { m.hidden = true; return; }
  m.hidden = false;
  if (S.auth_mode === 'subscription') {
    const pu = S.plan_usage || {};
    const rows = ['five_hour', 'seven_day'].map(k => { const v = pu[k] || {status: 'allowed', percent: null};
      const when = v.percent == null ? '' : resetOf(v) ? ` <span style="opacity:.7">· ${esc(resetOf(v))}</span>` : v.percent_at ? ` <span style="opacity:.7">· reported ${hm(v.percent_at)}</span>` : '';
      return `${k === 'five_hour' ? 'Session (5 h)' : 'Week'} ${planPct(v)}${when}${pctBar(v.percent)}`; });
    m.innerHTML = rows.join('');
  } else m.innerHTML = `API cost today: <b>$${Number(S.cost_today || 0).toFixed(2)}</b>`;
  m.innerHTML += `<div class="row" style="margin-top:3px;gap:4px"><span style="opacity:.7;flex:1">Refreshed ${hm(USE_AT / 1000)}</span><button type="button" class="ghost" id="meterRefresh" title="Refresh now" style="padding:0 6px;font-size:12px">↻</button></div>`;
  $('meterRefresh').onclick = e => { e.stopPropagation(); refreshUsage(true); };
}
async function refreshUsage(manual) {
  // Every 2 minutes (and on ↻): ask Claude for the real plan figures (no tokens), then re-read counts and costs.
  if (S && S.auth_mode === 'subscription') { try { await api('/api/usage/refresh', {manual: !!manual}); } catch (e) {} }
  try { await loadState(); } catch (e) {}
  if ($('useDlg').open) { try { await openUsage(); } catch (e) {} }
  if (manual) alertBox('Usage refreshed.', true);
}
setInterval(() => refreshUsage(false), 120000);
let USE = null;
/* ------------------------------------------------------------------ usage in plain words (L6, L9, L13, L14, L15) */
const fmtTok = n => { n = n || 0; return n >= 1e6 ? (n / 1e6).toFixed(n >= 1e7 ? 0 : 1) + ' M' : n >= 1e3 ? (n / 1e3).toFixed(n >= 1e4 ? 0 : 1) + ' k' : String(n); };
let UQ = {scope: null, unit: 'week', split: 'agent', period: null, open: null};
const isSub = () => (USE?.mode || S?.auth_mode) === 'subscription';
const costNote = () => isSub() ? 'At Anthropic\'s API prices. Not billed: your Claude subscription covers it.' : 'At Anthropic\'s list prices; your Anthropic invoice has the exact figure.';
function barsSvg(vals, labels, w, h, opts = {}) {
  const max = Math.max(1, ...vals), n = vals.length, gap = 2, bw = Math.max(1, (w - gap * (n - 1)) / n);
  const bars = vals.map((v, i) => { const bh = v ? Math.max(2, (h - 14) * v / max) : 0, x = i * (bw + gap);
    return `<rect x="${x.toFixed(1)}" y="${(h - 14 - bh).toFixed(1)}" width="${bw.toFixed(1)}" height="${bh.toFixed(1)}" rx="${Math.min(3, bw / 2)}" fill="var(--accent)" ${opts.flatFrom != null && i >= opts.flatFrom ? 'opacity=".35"' : ''}/>
      <rect x="${x.toFixed(1)}" y="0" width="${(bw + gap).toFixed(1)}" height="${h - 14}" fill="transparent" data-tip="${esc(labels[i])}: ${fmtTok(v)} tokens"/>`; }).join('');
  const base = `<line x1="0" x2="${w}" y1="${h - 14}" y2="${h - 14}" stroke="var(--line)"/>`;
  const ticks = (opts.ticks || []).map(([i, t], k, all) => `<span style="position:absolute;left:${(100 * (i + 0.5) / n).toFixed(1)}%;transform:translateX(${k === 0 ? '-10%' : k === all.length - 1 ? '-90%' : '-50%'})">${esc(t)}</span>`).join('');
  return `<div class="chartbox"><svg viewBox="0 0 ${w} ${h - 14}" width="100%" height="${h - 14}" preserveAspectRatio="none" role="img" aria-label="${esc(opts.label || '')}">${base}${bars}</svg><div class="hint" style="position:relative;height:16px;font-size:11px">${ticks}</div></div>`;
}
function wireTips(root) {
  root.querySelectorAll('.chartbox').forEach(box => {
    let tip = null;
    box.querySelectorAll('[data-tip]').forEach(r => {
      r.addEventListener('mouseenter', () => { tip = tip || box.appendChild(el('<div class="tip"></div>')); tip.textContent = r.dataset.tip; tip.hidden = false; });
      r.addEventListener('mousemove', e => { const b = box.getBoundingClientRect(); tip.style.left = (e.clientX - b.left) + 'px'; tip.style.top = (e.clientY - b.top) + 'px'; });
      r.addEventListener('mouseleave', () => { if (tip) tip.hidden = true; });
    });
  });
}
function headroomHtml(hr) {
  if (!hr || !hr.length) return '';
  const rows = hr.map(h => {
    const when = h.reset_text ? 'resets ' + h.reset_text : h.resets_at ? resetTxt(h.resets_at) : '';
    const src = h.percent == null ? 'not reported yet' : h.from_claude ? `read from Claude at ${hm(h.at)}` : `last reported ${h.at ? hm(h.at) : ''} (may be out of date)`;
    return `<div class="gauge"><b>${esc(h.label)}</b><div>${pctBar(h.percent)}</div><span>${h.percent == null ? '–' : `${Math.round(h.percent)}% used · <b>${Math.round(h.left)}% left</b>`}</span></div>
      <div class="hint" style="margin:-4px 0 4px 118px">${esc([when, src].filter(Boolean).join(' · '))}</div>`; }).join('');
  const low = hr.some(h => h.percent != null && ((h.kind === 'five_hour' && h.percent >= 80) || (h.kind === 'seven_day' && h.percent >= 90)));
  return `<div class="usec"><h4>Your Claude plan</h4>${rows}${low ? '<div class="hint" style="color:var(--warn)">Not much room left: Fred warns you before a big step, and work that hits the limit continues by itself after the reset.</div>' : ''}</div>`;
}
function splitTable(sp, total, kind = UQ.split) {
  const list = kind === 'agent' ? (sp.by_agent || []) : (sp.by_stage || []);
  if (!list.length) return '<p class="hint">Nothing used in this period.</p>';
  const max = Math.max(1, ...list.map(x => x.tokens));
  return `<table class="ut"><tr><th>${kind === 'agent' ? 'Agent' : 'Phase'}</th><th style="width:38%"></th><th class="n">Tokens</th><th class="n">${isSub() ? 'Would cost' : 'Cost'}</th><th class="n">Share</th></tr>
    ${list.map(x => `<tr><td>${esc(x.name || x.label)}</td><td><div class="sbar" style="width:${(100 * x.tokens / max).toFixed(1)}%" title="${fmtTok(x.tokens)} tokens"></div></td><td class="n">${fmtTok(x.tokens)}</td><td class="n">${usd(x.cost)}</td><td class="n">${total ? Math.round(100 * x.tokens / total) + '%' : ''}</td></tr>`).join('')}</table>`;
}
function timeHtml(t) {
  const st = (t && t.stages) || [];
  if (!st.length) return '<p class="hint">No time recorded yet (time is recorded from version 1.0.0 on).</p>';
  const max = Math.max(1, ...st.map(x => x.work + x.wait));
  return `<table class="ut"><tr><th>Phase</th><th style="width:44%"></th><th class="n">Team working</th><th class="n">Waiting for you</th></tr>
    ${st.map(x => `<tr><td>${esc(x.label)}</td><td><div style="display:flex;gap:2px"><div class="sbar" style="width:${(100 * x.work / max).toFixed(1)}%" title="Team working: ${took(x.work)}"></div>${x.wait ? `<div class="sbar wait" style="width:${(100 * x.wait / max).toFixed(1)}%" title="Waiting for you: ${took(x.wait)}"></div>` : ''}</div></td><td class="n">${took(x.work)}</td><td class="n">${x.wait ? took(x.wait) : '–'}</td></tr>`).join('')}
    <tr><td><b>Total</b></td><td><span class="legend"><span><i style="background:var(--accent)"></i>team working</span><span><i style="background:var(--mute);opacity:.55"></i>waiting for you</span></span></td><td class="n"><b>${took(t.work)}</b></td><td class="n"><b>${t.wait ? took(t.wait) : '–'}</b></td></tr></table>`;
}
function renderUsage() {
  const u = USE, b = $('useBody'); if (!u) return;
  $('useChecked').textContent = u.checked ? ' · refreshed ' + hm(u.checked) : '';
  const proj = !!u.project, units = [['day', 'Day'], ['week', 'Week'], ['month', 'Month'], ['year', 'Year'], ['all', 'All time']];
  const scopeBtns = `<div class="useg">${cur && cur !== 'setup' ? `<button type="button" data-scope="${esc(cur)}" class="${proj ? 'on' : ''}">This project</button>` : ''}<button type="button" data-scope="" class="${proj ? '' : 'on'}">All projects</button></div>`;
  let h = `<div class="row" style="flex-wrap:wrap;gap:10px;margin-top:6px">${scopeBtns}<span class="sp"></span><span class="hint">Summary by</span><div class="useg">${units.map(([k, l]) => `<button type="button" data-unit="${k}" class="${u.unit === k ? 'on' : ''}">${l}</button>`).join('')}</div></div>`;
  if (u.mode === 'subscription') h += headroomHtml(u.headroom);
  // headline
  if (proj) {
    const est = u.estimate || {};
    h += `<p class="headline"><b>${esc(u.project.name)}</b> ${isSub() ? 'would have cost' : 'has cost'} <b>${usd(u.total.cost)}</b> so far${u.done ? ` over <b>${u.done}</b> finished iteration${u.done > 1 ? 's' : ''}` : ''} (${fmtTok(u.total.tokens)} tokens).</p>
      ${est.remaining ? `<p class="hint">The rest of the plan (${u.planned - u.done} iteration${u.planned - u.done > 1 ? 's' : ''}) ${isSub() ? 'would cost' : 'should cost'} about ${usd(est.remaining.low)}–${usd(est.remaining.high)}, from the iterations finished so far.</p>` : ''}`;
  } else {
    h += `<p class="headline">This month the team has used <b>${fmtTok(u.month.tokens)}</b> tokens, which ${isSub() ? 'would cost' : 'cost'} <b>${usd(u.month.cost)}</b>.</p>`;
  }
  h += `<p class="hint" style="margin-top:0">${costNote()}</p>`;
  // live activity
  const a = u.activity || {minutes: [], hours: []}, working = u.state === 'working', flat = working && a.flat_minutes >= 5;
  const minLabels = a.minutes.map((_, i) => i === 59 ? 'this minute' : `${59 - i} min ago`), hrLabels = a.hours.map((_, i) => i === 23 ? 'this hour' : `${23 - i} h ago`);
  h += `<div class="usec"><h4>Activity${proj ? '' : ' (all projects)'} ${working ? '<span class="pill ok">working</span>' : ''}</h4>
    ${flat ? `<div class="hint" style="color:var(--warn);margin-bottom:4px">⚠ No tokens used for ${a.flat_minutes} min while the project says it is working: something may be stuck.</div>` : ''}
    <div class="hint">Tokens per minute, last 60 minutes</div>${barsSvg(a.minutes, minLabels, 600, 70, {ticks: [[0, '60 min ago'], [30, '30 min'], [59, 'now']], flatFrom: flat ? 60 - a.flat_minutes : null, label: 'Tokens per minute'})}
    <div class="hint" style="margin-top:6px">Tokens per hour, last 24 hours</div>${barsSvg(a.hours, hrLabels, 600, 70, {ticks: [[0, '24 h ago'], [12, '12 h'], [23, 'now']], label: 'Tokens per hour'})}</div>`;
  // periods + split
  const per = u.periods || [];
  if (UQ.period && !per.some(x => x.start === UQ.period)) UQ.period = null;
  const sel = per.find(x => x.start === UQ.period);
  const sp = sel || u.split, spTotal = sel ? sel.tokens : u.total.tokens;
  h += `<div class="usec"><h4>By ${units.find(x => x[0] === u.unit)[1].toLowerCase()}</h4>
    <table class="ut"><tr><th>Period</th><th style="width:40%"></th><th class="n">Tokens</th><th class="n">${isSub() ? 'Would cost' : 'Cost'}</th></tr>
    ${per.map(x => { const mx = Math.max(1, ...per.map(y => y.tokens)); return `<tr class="pick ${x.start === UQ.period ? 'sel' : ''}" data-period="${esc(x.start)}" title="Click to split this period by agent or phase"><td>${esc(x.label)}</td><td><div class="sbar" style="width:${(100 * x.tokens / mx).toFixed(1)}%"></div></td><td class="n">${fmtTok(x.tokens)}</td><td class="n">${usd(x.cost)}</td></tr>`; }).join('')}</table>
    <div class="row" style="margin:10px 0 4px;flex-wrap:wrap"><b style="font-size:13px">${sel ? esc(sel.label) : (proj ? 'Whole project' : 'All time')}: split by</b><div class="useg"><button type="button" data-split="agent" class="${UQ.split === 'agent' ? 'on' : ''}">Agent</button><button type="button" data-split="stage" class="${UQ.split === 'stage' ? 'on' : ''}">Phase</button></div>${sel ? '<button type="button" class="ghost" data-period="" style="font-size:12px">Show the whole ' + (proj ? 'project' : 'history') + '</button>' : '<span class="hint">click a period above to see only that one</span>'}</div>
    ${splitTable(sp, spTotal)}</div>`;
  if (proj) {
    const its = u.iterations || [];
    h += `<div class="usec"><h4>Per iteration</h4>${its.length ? `<table class="ut"><tr><th>Iteration</th><th class="n">Tokens</th><th class="n">${isSub() ? 'Would cost' : 'Cost'}</th><th class="n">Team working</th><th class="n">Waiting for you</th></tr>
      ${its.map(it => `<tr class="pick ${UQ.open === it.n ? 'sel' : ''}" data-it="${it.n}" title="Click for the detail by agent, phase and time"><td>${it.n ? it.n + '. ' : ''}${esc(it.name)} ${it.n ? `<span class="pill ${it.status === 'done' ? 'ok' : it.status === 'building' ? 'acc' : ''}">${esc(it.status === 'building' ? 'in progress' : it.status)}</span>` : ''}</td><td class="n">${fmtTok(it.tokens)}</td><td class="n">${usd(it.cost)}</td><td class="n">${it.n ? took(it.work) : ''}</td><td class="n">${it.n && it.wait ? took(it.wait) : (it.n ? '–' : '')}</td></tr>
        ${UQ.open === it.n ? `<tr><td colspan="5" style="background:var(--panel)"><div class="setgrid" style="grid-template-columns:1fr 1fr;gap:10px"><div><b class="hint">By agent</b>${splitTable(it, it.tokens, 'agent')}</div><div><b class="hint">By phase</b>${splitTable(it, it.tokens, 'stage')}</div></div>${it.n ? `<b class="hint">Time by phase</b>${timeHtml({stages: it.time_stages, work: it.work, wait: it.wait})}` : ''}</td></tr>` : ''}`).join('')}
      <tr><td><b>Whole prototype</b></td><td class="n"><b>${fmtTok(u.total.tokens)}</b></td><td class="n"><b>${usd(u.total.cost)}</b></td><td class="n"><b>${took(u.time.work)}</b></td><td class="n"><b>${u.time.wait ? took(u.time.wait) : '–'}</b></td></tr></table>` : '<p class="hint">No iterations yet: they appear here once the plan is approved.</p>'}</div>`;
  } else if ((u.projects || []).length) {
    const mx = Math.max(1, ...u.projects.map(x => x.tokens));
    h += `<div class="usec"><h4>Per project</h4><table class="ut"><tr><th>Project</th><th style="width:40%"></th><th class="n">Tokens</th><th class="n">${isSub() ? 'Would cost' : 'Cost'}</th></tr>
      ${u.projects.map(x => `<tr><td>${esc(x.name)}</td><td><div class="sbar" style="width:${(100 * x.tokens / mx).toFixed(1)}%"></div></td><td class="n">${fmtTok(x.tokens)}</td><td class="n">${usd(x.cost)}</td></tr>`).join('')}</table></div>`;
  }
  h += `<div class="usec"><h4>Time spent per phase${proj ? '' : ' (all projects)'}</h4>${timeHtml(u.time)}<div class="hint">The team's working time and the time it waited for your answers are counted apart.</div></div>`;
  b.innerHTML = h;
  wireTips(b);
  b.querySelectorAll('[data-scope]').forEach(x => x.onclick = () => { UQ.scope = x.dataset.scope; UQ.period = null; UQ.open = null; openUsage(); });
  b.querySelectorAll('[data-unit]').forEach(x => x.onclick = () => { UQ.unit = x.dataset.unit; UQ.period = null; openUsage(); });
  b.querySelectorAll('[data-split]').forEach(x => x.onclick = () => { UQ.split = x.dataset.split; renderUsage(); });
  b.querySelectorAll('[data-period]').forEach(x => x.onclick = () => { UQ.period = x.dataset.period || null; renderUsage(); });
  b.querySelectorAll('[data-it]').forEach(x => x.onclick = () => { UQ.open = UQ.open === +x.dataset.it ? null : +x.dataset.it; renderUsage(); });
}
async function openUsage() {
  if (UQ.scope === null) UQ.scope = cur && cur !== 'setup' ? cur : '';
  if (UQ.scope && !(S?.projects || []).some(p => p.slug === UQ.scope)) UQ.scope = '';
  USE = await api(`/api/analytics?unit=${UQ.unit}${UQ.scope ? '&project=' + encodeURIComponent(UQ.scope) : ''}`);
  renderUsage(); if (!$('useDlg').open) $('useDlg').showModal();
}
$('useRefresh').onclick = () => refreshUsage(true);
$('usageMeter').onclick = () => { UQ.scope = null; UQ.period = null; UQ.open = null; openUsage(); };
$('folderBtn').onclick = () => cur && api(`/api/projects/${cur}/open-folder`, {});
$('pauseBtn').onclick = async () => {
  if (!view) return;
  await api(`/api/projects/${cur}/${view.project.paused ? 'resume' : 'pause'}`, {}); await refreshView(); loadState();
};
