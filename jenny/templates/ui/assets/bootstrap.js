// Pre-render bootstrap — must run synchronously before the body renders to
// avoid a flash of the wrong theme / locale (FOUC). Extracted from two inline
// <script> blocks in index.html so the SPA can be served with a
// script-src 'self' CSP (M1). Load WITHOUT defer, in <head>, so it still
// executes before the rest of the document (same timing as the old inline).
(function () {
  var THEMES = ['chanel', 'synthwave', 'kyoto', 'sticker', 'fumetto', 'y2k', 'pietra'];
  var MIGRATION = { dark: 'chanel', light: 'pietra', match: 'chanel' };
  var t = localStorage.getItem('tc-theme') || 'chanel';
  t = MIGRATION[t] || t;
  if (THEMES.indexOf(t) === -1) t = 'chanel';
  localStorage.setItem('tc-theme', t);
  document.documentElement.setAttribute('data-theme', t);

  var savedLocale = localStorage.getItem('locale');
  if (savedLocale) document.documentElement.lang = savedLocale;

  // Mascotte: anti-flash come il tema. La verità a runtime resta in
  // shared/mascot.js (localStorage + evento 'mascotchange'); qui solo
  // l'attributo iniziale su <html> per evitare che lampeggi visibile prima
  // che mobile-jenny.js applichi la preferenza "non visibile" (il lato non
  // serve: la mascotte è creata da JS e posizionata prima del primo paint).
  var mascotVisible = localStorage.getItem('jenny-mascotte-visible');
  if (mascotVisible === '0') {
    document.documentElement.setAttribute('data-mascotte-hidden', '1');
  }
})();
