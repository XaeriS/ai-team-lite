/* L28: copy and paste. The window allows selecting text (main.py text_select=True); pywebview hides the browser's
   right-click menu, so this file brings a small one, plus a Copy button under each message. */
async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch (e) { /* older WebView2: use the fallback */ }
  const t = document.createElement('textarea');
  t.value = text; t.style.position = 'fixed'; t.style.opacity = '0';
  document.body.appendChild(t); t.select();
  let ok = false; try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
  t.remove();
  return ok;
}
async function readClipboard() {
  try { return await navigator.clipboard.readText(); } catch (e) { /* not allowed here: ask the app */ }
  try { return (await api('/api/clipboard/read', {})).text || ''; } catch (e) { return null; }
}
function pasteInto(field, text) {
  const a = field.selectionStart ?? field.value.length, b = field.selectionEnd ?? a;
  field.setRangeText(text, a, b, 'end');
  field.dispatchEvent(new Event('input', {bubbles: true}));
  field.focus();
}
function copiedFeedback(btn) {
  const was = btn.textContent; btn.textContent = 'Copied'; btn.disabled = true;
  setTimeout(() => { btn.textContent = was; btn.disabled = false; }, 1200);
}
/* the Copy button under a message (added by userMsg / fredMsg) */
function copyButton(getText) {
  const b = el('<button class="copy" type="button" title="Copy this message">Copy</button>');
  b.onclick = async e => { e.stopPropagation(); if (await copyText(getText())) copiedFeedback(b); };
  return b;
}

let ctxMenu = null;
function closeCtx() { ctxMenu?.remove(); ctxMenu = null; }
function openCtx(x, y, entries) {
  closeCtx();
  if (!entries.length) return;
  ctxMenu = el('<div class="ctxmenu" role="menu"></div>');
  entries.forEach(([label, run, disabled]) => {
    const b = el(`<button type="button" role="menuitem">${esc(label)}</button>`);
    b.disabled = !!disabled;
    b.onmousedown = e => e.preventDefault();   // keep the selection and the cursor where they are
    b.onclick = async () => { closeCtx(); await run(); };
    ctxMenu.appendChild(b);
  });
  document.body.appendChild(ctxMenu);
  const r = ctxMenu.getBoundingClientRect();
  ctxMenu.style.left = Math.min(x, innerWidth - r.width - 4) + 'px';
  ctxMenu.style.top = Math.min(y, innerHeight - r.height - 4) + 'px';
}
document.addEventListener('contextmenu', e => {
  const field = e.target.closest('textarea, input[type=text], input[type=password], input:not([type])');
  const sel = String(getSelection());
  if (field) {
    e.preventDefault();
    const picked = field.value.slice(field.selectionStart, field.selectionEnd), secret = field.type === 'password';
    openCtx(e.clientX, e.clientY, [
      ['Cut', async () => { if (await copyText(picked)) pasteInto(field, ''); }, !picked || secret],
      ['Copy', () => copyText(picked), !picked || secret],
      ['Paste', async () => { const t = await readClipboard(); if (t === null) alertBox('Press Ctrl+V to paste.'); else pasteInto(field, t); }],
      ['Select all', () => { field.focus(); field.select(); }],
    ]);
    return;
  }
  const msg = e.target.closest('.m, .card, .notice, .itcard, .doc');
  if (!sel && !msg) return;   // nothing to copy here
  e.preventDefault();
  const entries = [];
  if (sel) entries.push(['Copy', () => copyText(sel)]);
  if (msg) entries.push([msg.classList.contains('m') ? 'Copy message' : 'Copy all of this', () => copyText(msg.innerText.trim())]);
  openCtx(e.clientX, e.clientY, entries);
});
['mousedown', 'scroll', 'resize', 'blur'].forEach(t => addEventListener(t, ev => {
  if (ctxMenu && !(ev.target instanceof Node && ctxMenu.contains(ev.target))) closeCtx();
}, true));
addEventListener('keydown', e => { if (e.key === 'Escape') closeCtx(); });
