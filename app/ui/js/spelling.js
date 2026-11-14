/* L38: spelling help in the message box. The app checks the words (dictionaries on this PC, in the languages chosen in
   Settings) and lists possible typos under the box; click one, or right-click the word, to pick a correction. */
let spellIssues = [], spellTimer = null, spellSeq = 0;
function spellSoon() { clearTimeout(spellTimer); spellTimer = setTimeout(checkSpelling, 500); }
async function checkSpelling() {
  const text = $('input').value, mine = ++spellSeq;
  if (!S?.settings?.spell_check || !text.trim()) { spellIssues = []; renderSpell(); return; }
  let r; try { r = await api('/api/spell', {text}); } catch (e) { return; }
  if (mine !== spellSeq || $('input').value !== text) return;   // the text changed meanwhile: a newer check is on its way
  spellIssues = r.issues || []; renderSpell();
}
function spellIssueAt(pos) { return spellIssues.find(i => pos >= i.start && pos <= i.end && $('input').value.slice(i.start, i.end) === i.word) || null; }
function spellReplace(issue, word) {
  const f = $('input');
  if (f.value.slice(issue.start, issue.end) !== issue.word) return;
  f.setRangeText(word, issue.start, issue.end, 'end'); f.focus();
  f.dispatchEvent(new Event('input', {bubbles: true})); clearTimeout(spellTimer); checkSpelling();
}
function spellEntries(issue) {
  const e = issue.suggestions.map(s => [s, () => spellReplace(issue, s)]);
  if (!e.length) e.push(['No suggestion', () => {}, true]);
  e.push([`Add "${issue.word}" to my dictionary`, async () => { try { await api('/api/spell/add', {word: issue.word}); } catch (err) {} checkSpelling(); }]);
  return e;
}
function renderSpell() {
  const bar = $('spellBar'), seen = new Set(), list = spellIssues.filter(i => !seen.has(i.word.toLowerCase()) && seen.add(i.word.toLowerCase()));
  bar.hidden = !list.length; bar.innerHTML = '';
  if (!list.length) return;
  bar.append(el('<span class="hint">Possible typos:</span>'));
  list.slice(0, 8).forEach(i => {
    const b = el(`<button type="button" class="typo" title="Click to see corrections">${esc(i.word)}</button>`);
    b.onmousedown = e => e.preventDefault();
    b.onclick = () => { const r = b.getBoundingClientRect(); openCtx(r.left, Math.max(4, r.top - 8 - 32 * (Math.max(1, i.suggestions.length) + 1)), spellEntries(i)); };
    bar.append(b);
  });
  if (list.length > 8) bar.append(el(`<span class="hint">+${list.length - 8} more</span>`));
}
