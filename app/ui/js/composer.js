/* AI Team Lite - message box.
   Sending, attachments, pasted screenshots, talking and the microphone test.
   Classic scripts loaded in order by index.html; they share globals (TOKEN, $, S, cur, view...). */
/* ------------------------------------------------------------------ composer */
async function send() {
  const t = $('input').value.trim(); if ((!t && !attachments.length) || !cur) return;
  const text = t + (attachments.length ? '\n\n' + attachments.map(a => `[Attached file: ${a}]`).join('\n') : '');
  $('input').value = ''; autosize(); attachments = []; renderChips();
  try { await api(`/api/projects/${cur}/message`, {text}); } catch (e) { alertBox(e.message); $('input').value = t; }
}
function autosize() { const i = $('input'); i.style.height = 'auto'; i.style.height = Math.min(i.scrollHeight, 200) + 'px'; }
function renderChips() { $('chips').innerHTML = attachments.map(a => chipHtml(a, false)).join(''); wireChips($('chips'), false); }
$('input').addEventListener('input', e => { autosize(); if (e.isTrusted) voiceTalk = false; });
$('input').onkeydown = e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } };
$('sendBtn').onclick = send;
$('stopBtn').onclick = () => cur && api(`/api/projects/${cur}/stop`, {});
$('attachBtn').onclick = () => cur && $('fileIn').click();
async function uploadFile(f, name) {
  if (!cur) return;
  if (f.size > 15 * 1024 * 1024) { alertBox(`${name || f.name} is larger than 15 MB.`); return; }
  const b64 = await new Promise(res => { const r = new FileReader(); r.onload = () => res(String(r.result).split(',')[1]); r.readAsDataURL(f); });
  try { const r = await api(`/api/projects/${cur}/attach`, {name: name || f.name, data_b64: b64}); attachments.push(r.path); renderChips(); } catch (e) { alertBox(e.message); }
}
$('fileIn').onchange = async () => {
  for (const f of $('fileIn').files) await uploadFile(f);
  $('fileIn').value = '';
};
/* L10: paste a screenshot (Win+Shift+S, then Ctrl+V) or drop image files straight into the message box */
const shotName = (f, i) => { const d = new Date(), z = n => String(n).padStart(2, '0');
  const ext = ((f.type || '').split('/')[1] || 'png').replace('jpeg', 'jpg').replace(/[^a-z0-9]/g, '') || 'png';
  return `screenshot-${d.getFullYear()}${z(d.getMonth() + 1)}${z(d.getDate())}-${z(d.getHours())}${z(d.getMinutes())}${z(d.getSeconds())}${i ? '-' + (i + 1) : ''}.${ext}`; };
$('input').addEventListener('paste', async e => {
  const files = [...(e.clipboardData?.items || [])].filter(it => it.kind === 'file' && /^image\//.test(it.type)).map(it => it.getAsFile()).filter(Boolean);
  if (!files.length) return;  // ordinary text: paste as usual
  e.preventDefault();
  if (!cur) { alertBox('Open a project first, then paste the screenshot.'); return; }
  for (const [i, f] of files.entries()) await uploadFile(f, f.name && f.name !== 'image.png' ? f.name : shotName(f, i));
});
const cbox = document.querySelector('.cbox');
['dragenter', 'dragover'].forEach(t => cbox.addEventListener(t, e => { if ([...(e.dataTransfer?.types || [])].includes('Files')) { e.preventDefault(); cbox.classList.add('drop'); } }));
['dragleave', 'drop'].forEach(t => cbox.addEventListener(t, () => cbox.classList.remove('drop')));
cbox.addEventListener('drop', async e => {
  const files = [...(e.dataTransfer?.files || [])]; if (!files.length) return;
  e.preventDefault();
  if (!cur) { alertBox('Open a project first, then drop the file.'); return; }
  for (const f of files) await uploadFile(f);
});
/* ------------------------------------------------------------------ talking (hands-free) */
let listening = false, voiceTalk = false, preVoiceText = '';
function setListening(on, hint) {
  listening = on; $('micBtn').classList.toggle('on', on);
  const vb = $('voiceBar'); vb.hidden = !on && !hint;
  if (on) {
    const s = S.settings, w = esc(s.voice_end_word || 'copy');
    const how = s.voice_end_mode === 'word' ? `say “<b>${w}</b>” to send` : s.voice_end_mode === 'silence' ? `pause ${s.voice_silence_seconds}s to send` : `say “<b>${w}</b>” or pause ${s.voice_silence_seconds}s to send`;
    vb.innerHTML = `<span class="spin" style="border-color:#d92d20;border-right-color:transparent"></span> Listening… ${how}<span class="sp"></span><button type="button" class="ghost" id="vCancel">Cancel</button>`;
    $('vCancel').onclick = () => { voiceTalk = false; api('/api/voice/stop', {cancel: true}); };
  } else if (hint) { vb.textContent = hint; setTimeout(() => { if (!listening) vb.hidden = true; }, 5000); }
}
async function startListening() {
  if (!cur || listening) return;
  preVoiceText = $('input').value;
  try { await api('/api/voice/start', {slug: cur}); setListening(true); }
  catch (e) {
    if (/needs-download/.test(e.message)) { $('vSize').textContent = S.voice.model_size || 'about 145 MB'; $('voiceDlg').showModal(); }
    else alertBox(e.message);
  }
}
function onVoice(ev) {
  if (ev.state === 'listening') { setListening(true); return; }
  if (ev.state === 'partial') { $('input').value = (preVoiceText ? preVoiceText + ' ' : '') + ev.text; autosize(); return; }
  if (ev.state === 'downloaded') { setListening(false, 'Speech model ready.'); loadState().then(startListening); return; }
  if (ev.state === 'final') {
    setListening(false);
    $('input').value = (preVoiceText ? preVoiceText + ' ' : '') + ev.text; autosize();
    if (!ev.text.trim()) return;
    if (S.settings.voice_review) { $('input').focus(); setListening(false, 'Check the text, then press Enter to send.'); }
    else { voiceTalk = true; send(); }
    return;
  }
  if (ev.state === 'cancelled') { setListening(false, ev.reason || ''); return; }
  if (ev.state === 'error') { setListening(false); voiceTalk = false; voiceProblem(ev.text, ev.code); }
}
/* L11: show the real reason when listening fails, with the fix */
function voiceProblem(text, code) {
  const vb = $('voiceBar'); vb.hidden = false;
  vb.innerHTML = `<span style="color:var(--bad)">🎙 ${esc(text)}</span><span class="sp"></span>${code === 'blocked' ? '<button type="button" data-a="privacy">Open Windows microphone settings</button>' : ''}<button type="button" data-a="test">Test microphone</button><button type="button" class="ghost" data-a="x">✕</button>`;
  vb.querySelectorAll('[data-a]').forEach(b => b.onclick = () => {
    if (b.dataset.a === 'privacy') api('/api/voice/open-privacy', {});
    else if (b.dataset.a === 'test') micTest();
    else vb.hidden = true;
  });
}
async function micTest() {
  if (listening) { alertBox('Stop listening first.'); return; }
  $('micLevel').style.width = '0%'; $('micResult').textContent = 'Listening for 4 seconds…'; $('micResult').style.color = '';
  $('micAgain').disabled = true; if (!$('micDlg').open) $('micDlg').showModal();
  try { const r = await api('/api/voice/test', {slug: cur || 'setup'}); $('micDev').textContent = r.device ? 'Microphone: ' + r.device : 'Microphone: the Windows default'; }
  catch (e) { $('micResult').textContent = e.message; $('micResult').style.color = 'var(--bad)'; $('micAgain').disabled = false; }
}
function onMicTest(ev) {
  if (ev.state === 'test_level') { $('micLevel').style.width = Math.round(100 * Math.min(1, ev.level)) + '%'; return; }
  $('micAgain').disabled = false; $('micLevel').style.width = '0%';
  if (ev.device) $('micDev').textContent = 'Microphone: ' + ev.device;
  $('micResult').textContent = (ev.ok ? '✓ ' : '✗ ') + ev.reason;
  $('micResult').style.color = ev.ok && ev.code === 'ok' ? 'var(--ok)' : ev.ok ? 'var(--warn)' : 'var(--bad)';
  $('micPrivacy').classList.toggle('primary', ev.code === 'blocked');
}
$('micAgain').onclick = micTest;
$('micPrivacy').onclick = () => api('/api/voice/open-privacy', {});
$('setMicTest').onclick = micTest;
function autoListen() {
  if (!voiceTalk || !S?.settings.voice_auto_listen || listening) return;
  if ($('msgs').querySelector('.card.pending')) return;  // answer the card first
  setTimeout(startListening, 400);
}
$('micBtn').onclick = () => { if (listening) { api('/api/voice/stop', {cancel: false}); } else { voiceTalk = true; startListening(); } };
$('vDl').onclick = async () => { $('voiceDlg').close(); setListening(false, 'Downloading the speech model… this can take a few minutes.'); $('voiceBar').hidden = false; await api('/api/voice/download', {slug: cur}); };
