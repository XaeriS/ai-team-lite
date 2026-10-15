/* AI Team Lite - start.
   Opens the app at the right place.
   Classic scripts loaded in order by index.html; they share globals (TOKEN, $, S, cur, view...). */
/* ------------------------------------------------------------------ start */
(async () => {
  applyLayout();
  await loadState(); connect();
  if (S.auth_mode === 'subscription') refreshUsage(false);  // real plan figures right away
  const interrupted = (S.interrupted || []);
  if (S.has_api_key && S.setup_complete && interrupted.length && S.settings.auto_resume) {
    for (const x of interrupted) { try { await api(`/api/interrupted/${x.slug}/resume`, {}); } catch (e) {} }
    await loadState(); await open(interrupted[0].slug);
  } else if (S.has_api_key && S.setup_complete && interrupted.length) {
    await open(null);  // show the "Work was interrupted" banner first
  } else if (S.has_api_key) {
    await open(!S.setup_complete ? 'setup' : (S.projects.find(p => !p.hidden)?.slug || null));
  } else { renderAll(); renderSide(); }
})();
