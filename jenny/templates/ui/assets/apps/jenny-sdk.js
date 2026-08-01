/* ── Jenny SDK — bridge between a Jenny App iframe and the gateway/SPA ──
   The iframe is sandboxed without allow-same-origin (opaque origin), so:
   - auth travels as the ?token= query param (an Authorization header would
     require a CORS preflight the gateway's GET-only HTTP layer cannot answer);
   - jenny.action() always issues a simple GET — never add custom headers;
   - theme/lang arrive via the iframe src query string and are stamped here. */
(function () {
  'use strict';

  const qs = new URLSearchParams(location.search);
  const token = qs.get('token') || '';
  const theme = qs.get('theme') === 'light' ? 'light' : 'dark';
  const lang = qs.get('lang') || 'it';
  const slug = (location.pathname.match(/^\/apps\/([^/]+)/) || [])[1] || '';

  // Accent colors cross an origin boundary via the query string: validate
  // strictly as hex before injecting into styles.
  const HEX = /^#[0-9a-f]{3,8}$/i;
  function applyAccent(accent, onAccent) {
    if (!HEX.test(accent || '')) return;
    const root = document.documentElement.style;
    root.setProperty('--accent', accent);
    if (HEX.test(onAccent || '')) root.setProperty('--on-accent', onAccent);
    const full = accent.length === 4
      ? '#' + [...accent.slice(1)].map(c => c + c).join('')
      : accent.slice(0, 7);
    const rgb = [1, 3, 5].map(i => parseInt(full.slice(i, i + 2), 16));
    if (rgb.every(Number.isFinite)) {
      root.setProperty('--accent-rgb', rgb.join(', '));
      root.setProperty('--accent-subtle', `rgba(${rgb.join(', ')}, 0.15)`);
    }
    window.jenny.accent = accent;
  }

  document.documentElement.setAttribute('data-theme', theme);
  document.documentElement.lang = lang;

  async function action(name, params = {}) {
    const url = '/api/apps/' + encodeURIComponent(slug)
      + '/actions/' + encodeURIComponent(name)
      + '?params=' + encodeURIComponent(JSON.stringify(params))
      + '&token=' + encodeURIComponent(token);
    const res = await fetch(url);
    let data = null;
    try { data = await res.json(); } catch { /* non-JSON error body */ }
    if (!res.ok || (data && data.ok === false)) {
      throw new Error((data && data.error) || name + ': HTTP ' + res.status);
    }
    return data;
  }

  function discuss(text) {
    window.parent.postMessage(
      { type: 'jenny:discuss', slug, text: String(text || '') },
      '*'
    );
  }

  let depth = 1;

  function postNavState() {
    window.parent.postMessage(
      { type: 'jenny:nav-state', slug, depth, canGoBack: depth > 1 },
      '*'
    );
  }

  function navigate(url, state = null) {
    history.pushState(state, '', url);
    depth++;
    postNavState();
  }

  function back() {
    if (depth > 1) {
      history.back();
    }
  }

  window.addEventListener('popstate', () => {
    if (depth > 1) {
      depth--;
      postNavState();
    }
  });

  window.addEventListener('message', (event) => {
    const msg = event.data;
    if (!msg || typeof msg !== 'object') return;
    if (msg.type === 'jenny:data-changed') {
      window.dispatchEvent(
        new CustomEvent('jenny:data-changed', { detail: { slug: msg.slug } })
      );
    } else if (msg.type === 'jenny:theme') {
      const next = msg.theme === 'light' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', next);
      window.jenny.theme = next;
      applyAccent(msg.accent, msg.onAccent);
    } else if (msg.type === 'jenny:go-back') {
      back();
    } else if (msg.type === 'jenny:ui-query') {
      // Jenny chiede cosa mostra questa app: l'app ha pieno accesso al proprio
      // DOM e lo rimanda al parent (che lo pota). Nonce per correlare.
      window.parent.postMessage(
        { type: 'jenny:ui-result', nonce: msg.nonce,
          html: document.documentElement.outerHTML },
        '*'
      );
    }
  });

  window.jenny = { slug, theme, lang, accent: null, action, discuss, navigate, back };
  applyAccent(qs.get('accent'), qs.get('onAccent'));
})();
