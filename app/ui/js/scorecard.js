/* L49: the quality scorecard of a version, in plain words: a card in the chat and a block at the top of the Tests tab. */
const SC_WORD = {good: 'OK', attention: 'To look at', problem: 'To fix', unknown: 'Not checked'};
const SC_ICON = {good: '✓', attention: '!', problem: '✕', unknown: '?'};
function scorecardHtml(card) {
  const when = card.time ? new Date(card.time * 1000).toLocaleString() : '';
  return `<div class="sc-head"><span class="sc-dot ${card.level}">${SC_ICON[card.level] || ''}</span><b>${esc(card.headline || '')}</b></div>
    <div class="hint">${card.iteration ? `After iteration ${card.iteration} · ` : ''}${esc(when)}</div>
    <div class="sc-list">${(card.checks || []).map(c => `<div class="sc-row" data-k="${esc(c.key)}">
      <div class="row"><span class="sc-dot ${c.level}" title="${SC_WORD[c.level]}">${SC_ICON[c.level] || ''}</span><b class="sc-title">${esc(c.title)}</b><span class="pill ${{good: 'ok', attention: 'warn', problem: 'bad'}[c.level] || ''}">${SC_WORD[c.level] || ''}</span></div>
      <div class="sc-sum">${esc(c.summary)}</div>
      ${(c.details || []).length ? `<details><summary class="hint">Details (${c.details.length})</summary><ul class="sc-det">${c.details.map(d => `<li>${esc(d)}</li>`).join('')}</ul></details>` : ''}
    </div>`).join('')}</div>`;
}
function scorecardCard(ev) {
  return el(`<div class="card sc-card ${ev.level}"><h4>📋 Quality of this version</h4>${scorecardHtml(ev)}
    <div class="hint" style="margin-top:6px">Also in the Tests tab, and saved in your project as reports/QUALITY.md.</div></div>`);
}
async function scorecardBlock(slug) {
  let d; try { d = await api(`/api/projects/${slug}/scorecard`); } catch (e) { return ''; }
  if (!d || d.missing) return `<div class="sc-box"><b>Quality of this version</b><div class="hint">Not checked yet: Fred checks it before each new version.</div></div>`;
  return `<div class="sc-box ${d.level}"><b>📋 Quality of this version</b>${scorecardHtml(d)}</div>`;
}
