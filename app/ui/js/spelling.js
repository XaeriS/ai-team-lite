/* L38 + L66: spelling help in the message box, like Word: misspelt words get a red wavy underline (drawn on a layer
   behind the box, so the text itself is untouched) and a right-click on one shows the corrections. The words are
   checked with the dictionaries on this PC, in the languages chosen in Settings > General. */
let spellIssues = [], spellTimer = null, spellSeq = 0;
function spellSoon() { clearTimeout(spellTimer); spellTimer = setTimeout(checkSpelling, 500); renderSpell(); }
async function checkSpelling() {
  const text = $('input').value, mine = ++spellSeq;
  if (!S?.settings?.spell_check || !text.trim()) { spellIssues = []; renderSpell(); return; }
  let r; try { r = await api('/api/spell', {text}); } catch (e) { return; }
  if (mine !== spellSeq || $('input').value !== text) return;   // the text changed meanwhile: a newer check is on its way
  spellIssues = r.issues || []; renderSpell();
}
// issues still where they were (typing elsewhere keeps them; text changed under one hides it until the next check)
const spellLive = () => spellIssues.filter(i => $('input').value.slice(i.start, i.end) === i.word);
function spellIssueAt(pos) { return spellLive().find(i => pos >= i.start && pos <= i.end) || null; }
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
  const layer = $('spellLayer'), f = $('input');
  if (!layer) return;
  const cs = getComputedStyle(f);   // the layer copies the box's text layout so the lines fall exactly under the words
  ['fontFamily', 'fontSize', 'fontWeight', 'lineHeight', 'letterSpacing', 'paddingTop', 'paddingRight', 'paddingBottom',
   'paddingLeft', 'borderTopWidth', 'borderLeftWidth', 'textIndent', 'tabSize'].forEach(k => layer.style[k] = cs[k]);
  layer.style.width = f.offsetWidth + 'px'; layer.style.height = f.offsetHeight + 'px';
  const text = f.value, live = spellLive().sort((a, b) => a.start - b.start);
  let html = '', at = 0;
  live.forEach((i, n) => {
    if (i.start < at) return;
    html += esc(text.slice(at, i.start)) + `<mark data-i="${n}">${esc(i.word)}</mark>`; at = i.end;
  });
  layer.innerHTML = html + esc(text.slice(at)) + '\n';
  layer.scrollTop = f.scrollTop;
}
// the misspelt word under the mouse (right-click), found from the underline drawn on the layer
function spellIssueAtPoint(x, y) {
  const live = spellLive().sort((a, b) => a.start - b.start);
  for (const m of $('spellLayer')?.querySelectorAll('mark') || []) {
    for (const r of m.getClientRects()) if (x >= r.left - 2 && x <= r.right + 2 && y >= r.top - 2 && y <= r.bottom + 4) return live[+m.dataset.i] || null;
  }
  return null;
}
addEventListener('DOMContentLoaded', () => {
  const f = $('input');
  f.addEventListener('scroll', () => { $('spellLayer').scrollTop = f.scrollTop; });
  addEventListener('resize', renderSpell);
});
