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

  /* ── Navigazione interna ──────────────────────────────────────────────────
     L'SDK non tocca la history del browser, di proposito.

     Verificato empiricamente in Blink: un `pushState` da un iframe
     `sandbox="allow-scripts"` a origine opaca *riesce* (hash, query, path
     relativo). Il problema non è che fallisca: è che le entry finiscono nella
     joint session history del WebView, condivisa col main frame e non potabile
     dal parent. Due pushState del figlio portano `history.length` da 2 a 4, e
     dopo `iframe.remove()` resta 4: `closeApp()` smonta l'overlay ma le entry
     restano, quindi dopo la ✕ l'utente si trovava una o più pressioni di
     Indietro completamente morte (nessun popstate, nessun cambio di URL).

     Qui la profondità è quindi solo contabile: la joint history resta proprietà
     esclusiva di `pushNav`/`replaceNav` della SPA, e `back()` risintetizza un
     evento `popstate` per l'app — che non deve cambiare una riga. */

  // Pila degli stati logici dell'app: l'indice 0 è la schermata iniziale.
  const navStack = [null];
  // Numero di <dialog> aperti: il parent non può vederli (origine opaca).
  let dialogsOpen = 0;

  function postNavState() {
    /* `depth` somma schermate e dialog aperti: per la SPA sono livelli
       equivalenti, ed è il ramo `depth > 1` di AppsController.handleBack() a
       instradare la pressione qui dentro invece di chiudere tutta l'app. */
    window.parent.postMessage(
      {
        type: 'jenny:nav-state',
        slug,
        // Solo `depth`: il parent non legge altro. `screens`/`dialogs` erano
        // nati come dettaglio diagnostico ed erano già campi morti alla
        // nascita, per la stessa ragione per cui il flag booleano che stava
        // qui prima è stato tolto.
        depth: navStack.length + dialogsOpen,
      },
      '*'
    );
  }

  /* `url` resta nella firma per le app già scritte, ma non viene scritto da
     nessuna parte: vale come etichetta leggibile della schermata. Cambiare
     davvero l'URL significherebbe tornare a sporcare la joint history. Chi
     vuole portarsi dietro dei dati usi `state`, che torna indietro
     nell'evento `popstate` sintetico. */
  function navigate(url, state = null) {
    navStack.push(state);
    postNavState();
  }

  function back() {
    if (navStack.length <= 1) return;
    navStack.pop();
    // Le app esistenti reagiscono a `popstate`: l'evento sintetico le lascia
    // intatte pur avendo tolto di mezzo la history vera.
    window.dispatchEvent(
      new PopStateEvent('popstate', { state: navStack[navStack.length - 1] })
    );
    postNavState();
  }

  /* Chiude il <dialog> più in alto con la semantica di Esc: evento `cancel`
     annullabile, `close()` solo se nessuno l'ha rifiutato. Ritorna true anche
     quando la chiusura è stata rifiutata: la pressione è comunque stata
     consumata dall'app, non deve proseguire e chiudere l'app intera. */
  function closeTopDialog() {
    const open = document.querySelectorAll('dialog[open]');
    if (!open.length) return false;
    const top = open[open.length - 1];
    if (top.dispatchEvent(new Event('cancel', { cancelable: true }))) top.close();
    syncDialogs();
    return true;
  }

  function syncDialogs() {
    const count = document.querySelectorAll('dialog[open]').length;
    // L'observer vede ogni render dell'app: si parla solo quando cambia.
    if (count === dialogsOpen) return;
    dialogsOpen = count;
    postNavState();
  }

  /* Un dialog si apre togliendo/mettendo l'attributo `open`, ma può anche
     essere inserito nel DOM già aperto: si osservano entrambe le cose. */
  new MutationObserver(syncDialogs).observe(document.documentElement, {
    subtree: true,
    childList: true,
    attributes: true,
    attributeFilter: ['open'],
  });
  document.addEventListener('DOMContentLoaded', syncDialogs);

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
      // Prima il dialog più in alto, poi la schermata: l'ordine è quello della
      // catena dei livelli della SPA, che qui dentro non arriva a guardare.
      if (!closeTopDialog()) back();
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
