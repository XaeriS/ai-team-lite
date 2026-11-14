/* L48: what an iteration changed, in plain words, and going back to an earlier iteration. */
const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
function changeLine(f) {
  const size = f.added === null ? '' : f.change === 'new' ? plural(f.added, 'line') : f.change === 'removed' ? '' :
    [f.added ? `${plural(f.added, 'line')} added` : '', f.removed ? `${plural(f.removed, 'line')} removed` : ''].filter(Boolean).join(', ');
  return `<div class="chgfile" data-path="${esc(f.path)}"><div class="row"><span class="pill ${f.change === 'new' ? 'ok' : f.change === 'removed' ? 'bad' : 'acc'}">${esc(f.change)}</span>
    <code>${esc(f.path)}</code>${f.from ? `<span class="hint">(was ${esc(f.from)})</span>` : ''}<span class="sp"></span><span class="hint">${esc(size)}</span>
    ${f.change !== 'removed' || f.removed ? '<button type="button" data-lines style="padding:1px 8px;font-size:12px">Show the lines</button>' : ''}</div>
    ${f.what ? `<div class="hint" style="margin:2px 0 0 4px">${esc(f.what)}</div>` : ''}<pre class="chglines" hidden></pre></div>`;
}
async function showChanges(n) {
  const dlg = $('chgDlg'), body = $('chgBody');
  $('chgTitle').textContent = `What iteration ${n} changed`;
  body.innerHTML = '<p class="hint"><span class="spin"></span> Looking at the project history…</p>';
  if (!dlg.open) dlg.showModal();
  let c; try { c = await api(`/api/projects/${cur}/changes?n=${n}`); } catch (e) { body.innerHTML = `<p class="err">${esc(e.message)}</p>`; return; }
  $('chgTitle').textContent = `What iteration ${c.n} changed: ${c.name}`;
  const reqs = c.requirements.length ? `<div class="chgbox"><b>Why: the requirements it serves</b>${c.requirements.map(r => `<div><span class="pill">${esc(r.id)}</span> ${esc(r.title || '')}</div>`).join('')}</div>` : '';
  const total = `${plural(c.files, 'file')} touched${c.added || c.removed ? `: ${plural(c.added, 'line')} added, ${plural(c.removed, 'line')} removed` : ''}`;
  const later = c.later.length ? `It undoes ${c.later.map(x => `iteration ${x.n} (${esc(x.name)})`).join(', ')}${c.unsaved ? ' and the work in progress' : ''}: they become "planned" again.`
    : c.unsaved ? 'It sets aside the work in progress that was not saved yet.' : '';
  body.innerHTML = `${c.summary ? `<p style="margin:10px 0 6px">${esc(c.summary)}</p>` : ''}${reqs}
    <p class="hint" style="margin:8px 0 4px">${esc(total)}${c.finished ? ' · finished ' + esc(new Date(c.finished * 1000).toLocaleString([], {dateStyle: 'medium', timeStyle: 'short'})) : ''}</p>
    ${c.groups.map(g => `<h4 style="margin:12px 0 4px">${esc(g.label)} <span class="hint">(${g.files.length})</span></h4>${g.files.map(changeLine).join('')}`).join('') || '<p class="empty">No file changed in this iteration.</p>'}
    ${c.can_go_back ? `<div class="chgbox goback"><b>Go back to this point</b>
      <div class="hint" style="margin:4px 0 8px">Puts the project's files back exactly as they were when iteration ${c.n} was finished. ${later} If the team is working, it is stopped first. Nothing is deleted: everything is kept in the project history and you can undo this right after.</div>
      <button type="button" id="chgBack">Go back to the end of iteration ${c.n}…</button><span class="hint" id="chgBackSt" style="margin-left:8px"></span></div>` : ''}`;
  body.querySelectorAll('[data-lines]').forEach(b => b.onclick = async () => {
    const box = b.closest('.chgfile'), pre = box.querySelector('.chglines');
    if (!pre.hidden) { pre.hidden = true; b.textContent = 'Show the lines'; return; }
    if (!pre.dataset.done) {
      let d; try { d = await api(`/api/projects/${cur}/changes-file?n=${c.n}&path=${encodeURIComponent(box.dataset.path)}`); } catch (e) { pre.textContent = e.message; pre.hidden = false; return; }
      pre.innerHTML = d.binary ? 'This file is not text (an image, a database…), so there are no lines to show.'
        : d.lines.map(l => `<span class="${l.startsWith('+') ? 'ladd' : l.startsWith('-') ? 'ldel' : l.startsWith('@@') ? 'lhunk' : ''}">${esc(l.startsWith('@@') ? '⋯' : l)}</span>`).join('\n') + (d.more ? `\n… ${d.more} more lines` : '');
      pre.dataset.done = '1';
    }
    pre.hidden = false; b.textContent = 'Hide the lines';
  });
  const back = $('chgBack');
  if (back) back.onclick = async () => {
    if (!back.dataset.sure) { back.dataset.sure = '1'; back.textContent = `Yes, go back to iteration ${c.n}`; back.classList.add('primary'); $('chgBackSt').textContent = 'Click again to confirm.'; return; }
    back.disabled = true; $('chgBackSt').innerHTML = '<span class="spin"></span> Going back…';
    try { await api(`/api/projects/${cur}/go-back`, {n: c.n}); dlg.close(); await open(cur); }
    catch (e) { $('chgBackSt').textContent = e.message; back.disabled = false; delete back.dataset.sure; back.textContent = `Go back to the end of iteration ${c.n}…`; back.classList.remove('primary'); }
  };
}
function wentBackCard(ev) {
  const done = ev.status === 'done';
  const n = el(`<div class="card"><div class="row"><b>↩ You went back to the end of iteration ${ev.n}: ${esc(ev.name || '')}</b>${done ? '' : '<span class="pill">undone</span>'}</div>
    <div class="hint" style="margin-top:6px">${done ? `The project's files are as they were then.${(ev.undone || []).length ? ' Planned again: ' + ev.undone.map(x => `iteration ${x.n} (${esc(x.name)})`).join(', ') + '.' : ''}${ev.kept_unsaved ? ' The work in progress was kept in a safety copy.' : ''} Nothing was deleted. Fred waits for you to say what to change before the team builds again.`
      : 'This was undone: everything is as it was before.'}</div>
    ${done ? '<div class="acts"><button type="button" data-a="undo">Undo: bring everything back</button></div><div class="hint" data-st></div>' : ''}</div>`);
  const u = n.querySelector('[data-a=undo]');
  if (u) u.onclick = async () => { u.disabled = true; try { await api(`/api/projects/${cur}/go-back-undo`, {}); await open(cur); } catch (e) { n.querySelector('[data-st]').textContent = e.message; u.disabled = false; } };
  return n;
}
