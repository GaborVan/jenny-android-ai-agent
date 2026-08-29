// Motore lato pagina della sessione di navigazione (browser_* tools).
//
// Kotlin lo inietta con evaluateJavascript sostituendo __ARGS__ con un oggetto
// JSON. Vive nella pagina, quindi `window.__jenny` sopravvive fra una chiamata e
// l'altra ma **muore a ogni navigazione**: è esattamente ciò che rende un
// riferimento vecchio un errore invece di un click sull'elemento sbagliato.
//
// Il tetto sui caratteri si applica **qui**: se il muro di testo attraversa il
// bridge il danno è già fatto — nessuno tronca il risultato di un tool a valle
// (context_governor taglia la cronologia, non la singola risposta).
(function (ARGS) {
  var J = (window.__jenny = window.__jenny || { v: 0, refs: {}, prev: null });

  // ---------------------------------------------------------------- visibilità

  // checkVisibility risolve display/visibility/opacity **ereditati**, che è il
  // punto in cui una camminata ingenua sbaglia: getComputedStyle(figlio).display
  // non eredita il `none` di un antenato. Misurato il 29/08 su it.wikipedia.org:
  // senza questo, 2.502 elementi "visibili" di cui la gran parte in sezioni
  // chiuse.
  function visible(el) {
    if (el.getAttribute('aria-hidden') === 'true') return false;
    if (typeof el.checkVisibility === 'function') {
      try {
        if (!el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) return false;
      } catch (e) { /* browser vecchio: si ricade sui controlli sotto */ }
    }
    var r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    return !clipped(el, r);
  }

  // Il secondo modo di essere invisibili: dentro un contenitore che ritaglia
  // (`overflow:hidden` + altezza a zero). È come Wikipedia mobile chiude le
  // sezioni: i figli hanno un rettangolo valido, ma non si vedono.
  function clipped(el, r) {
    var p = el.parentElement, hops = 0;
    while (p && hops < 6) {
      var s;
      try { s = getComputedStyle(p); } catch (e) { return false; }
      if (s.overflow === 'hidden' || s.overflow === 'clip' ||
          s.overflowY === 'hidden' || s.overflowY === 'clip') {
        var pr = p.getBoundingClientRect();
        if (pr.height <= 1 || pr.width <= 1) return true;
        if (r.bottom <= pr.top + 1 || r.top >= pr.bottom - 1) return true;
      }
      p = p.parentElement; hops++;
    }
    return false;
  }

  // ---------------------------------------------------------------- ruolo/nome

  // Solo ruoli su cui si può agire, più le intestazioni (che orientano) e i
  // landmark (che dividono). Tutto il resto — presentation, none, rowgroup,
  // document — è rumore: misurato il 29/08, arrivava fino a un terzo delle righe.
  var ACTIONABLE = {
    link: 1, button: 1, textbox: 1, searchbox: 1, checkbox: 1, radio: 1,
    combobox: 1, listbox: 1, option: 1, slider: 1, spinbutton: 1, switch: 1,
    tab: 1, menuitem: 1, password: 1,
  };
  var LANDMARK = {
    navigation: 1, main: 1, search: 1, form: 1, banner: 1,
    contentinfo: 1, complementary: 1, region: 1,
  };

  function role(el) {
    var explicit = el.getAttribute('role');
    if (explicit) {
      var first = explicit.split(/\s+/)[0].toLowerCase();
      if (ACTIONABLE[first] || LANDMARK[first] || first === 'heading') return first;
      return null;
    }
    var t = el.tagName.toLowerCase();
    if (t === 'a') return el.hasAttribute('href') ? 'link' : null;
    if (t === 'button') return 'button';
    if (t === 'summary') return 'button';
    if (t === 'input') {
      var ty = (el.getAttribute('type') || 'text').toLowerCase();
      if (ty === 'hidden') return null;
      if (ty === 'checkbox') return 'checkbox';
      if (ty === 'radio') return 'radio';
      if (ty === 'submit' || ty === 'button' || ty === 'reset' || ty === 'image') return 'button';
      if (ty === 'search') return 'searchbox';
      if (ty === 'password') return 'password';
      return 'textbox';
    }
    if (t === 'textarea') return 'textbox';
    if (t === 'select') return 'combobox';
    if (/^h[1-6]$/.test(t)) return 'heading';
    if (t === 'nav') return 'navigation';
    if (t === 'main') return 'main';
    if (t === 'form') return 'form';
    if (t === 'header') return 'banner';
    if (t === 'footer') return 'contentinfo';
    if (t === 'aside') return 'complementary';
    return null;
  }

  function clean(s) {
    return (s || '').replace(/\s+/g, ' ').trim();
  }

  function accessibleName(el, r) {
    var n = clean(el.getAttribute('aria-label'));
    if (n) return n;
    var lb = el.getAttribute('aria-labelledby');
    if (lb) {
      var p = document.getElementById(lb.split(/\s+/)[0]);
      if (p) { n = clean(p.innerText || p.textContent); if (n) return n; }
    }
    var id = el.getAttribute('id');
    if (id) {
      try {
        var l = document.querySelector('label[for="' + id.replace(/["\\]/g, '') + '"]');
        if (l) { n = clean(l.innerText || l.textContent); if (n) return n; }
      } catch (e) { /* id non selezionabile: si prosegue */ }
    }
    if (el.closest) {
      var cl = el.closest('label');
      if (cl && cl !== el) { n = clean(cl.innerText || cl.textContent); if (n) return n; }
    }
    n = clean(el.getAttribute('placeholder')); if (n) return n;
    n = clean(el.getAttribute('alt')); if (n) return n;
    n = clean(el.getAttribute('title')); if (n) return n;
    // Il valore è un nome solo per i controlli che non sono segreti.
    if (r !== 'password' && el.value && typeof el.value === 'string') {
      n = clean(el.value); if (n) return n;
    }
    n = clean(el.innerText || el.textContent); if (n) return n;
    // Ripieghi per gli elementi senza testo (icone, immagini linkate): un nome
    // brutto orienta comunque, una riga `link ""` no.
    var img = el.querySelector && el.querySelector('img[alt]');
    if (img) { n = clean(img.getAttribute('alt')); if (n) return n; }
    var href = el.getAttribute && el.getAttribute('href');
    if (href) {
      var tail = href.split('#')[0].split('?')[0].replace(/\/+$/, '').split('/').pop();
      if (tail) return '/' + decodeURIComponent(tail).slice(0, 40);
    }
    return '';
  }

  function state(el, r) {
    var bits = [];
    if (el.getAttribute('aria-expanded')) bits.push('expanded=' + el.getAttribute('aria-expanded'));
    if (el.disabled === true || el.getAttribute('aria-disabled') === 'true') bits.push('disabled');
    if (r === 'checkbox' || r === 'radio' || r === 'switch') {
      var c = el.checked !== undefined ? el.checked : el.getAttribute('aria-checked') === 'true';
      bits.push(c ? 'checked' : 'unchecked');
    }
    if (r === 'combobox' && el.options && el.selectedIndex >= 0) {
      var o = el.options[el.selectedIndex];
      if (o) bits.push('value=' + JSON.stringify(clean(o.text).slice(0, 30)));
    }
    if (r === 'textbox' || r === 'searchbox') {
      var v = clean(el.value);
      if (v) bits.push('value=' + JSON.stringify(v.slice(0, 40)));
    }
    if (r === 'password') bits.push(el.value ? 'filled' : 'empty');
    if (r === 'heading') {
      var lv = el.getAttribute('aria-level') || (/^h([1-6])$/.test(el.tagName.toLowerCase())
        ? el.tagName.charAt(1) : '');
      if (lv) bits.push('level=' + lv);
    }
    return bits;
  }

  // ---------------------------------------------------------------- raccolta

  function collect() {
    var out = [];
    var all = document.body ? document.body.querySelectorAll('*') : [];
    var vh = window.innerHeight || 800;
    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      var r = role(el);
      if (!r) continue;
      if (!visible(el)) continue;
      var rect = el.getBoundingClientRect();
      var nm = accessibleName(el, r);
      if (!nm && !ACTIONABLE[r]) continue;          // landmark/heading muti: rumore
      out.push({
        el: el, role: r, name: nm.slice(0, 80),
        state: state(el, r),
        inView: rect.bottom > 0 && rect.top < vh,
        top: rect.top,
        actionable: !!ACTIONABLE[r],
      });
    }
    return out;
  }

  function lineFor(item, ref) {
    var s = '- ' + item.role;
    if (item.name) s += ' "' + item.name + '"';
    var bits = item.state.slice();
    if (ref) bits.unshift('ref=' + ref);
    if (bits.length) s += ' [' + bits.join(' ') + ']';
    return s;
  }

  // L'identita' di un elemento **non comprende il suo ref**: il ref porta il
  // numero di versione, che cambia a ogni snapshot, quindi confrontare le righe
  // gia' composte fa risultare nuova ogni riga di una pagina ferma. Misurato sul
  // telefono il 29/08: "0 invariate, 72 sparite" su due snapshot identici.
  function keyFor(item) {
    return item.role + '|' + item.name + '|' + item.state.join(',');
  }

  // ---------------------------------------------------------------- snapshot

  function snapshot(args) {
    var maxChars = args.maxChars || 2000;
    var filter = clean(args.filter).toLowerCase();
    var version = args.version;
    J.v = version;
    J.refs = {};

    var items = collect();
    var n = 0;
    var lines = [];
    var keys = [];
    var used = 0;
    var deferred = {};      // ruolo -> quante righe non mostrate
    var truncated = false;

    function push(item) {
      // Il ref si assegna **dopo** il tetto: allocarlo prima lascia buchi nella
      // numerazione e mette nella mappa elementi che il modello non ha mai visto.
      var probe = lineFor(item, item.actionable ? version + ':e' + n : null);
      if (used + probe.length + 1 > maxChars) { truncated = true; return false; }
      var ref = null;
      if (item.actionable) { ref = version + ':e' + (n++); J.refs[ref] = item.el; }
      lines.push(lineFor(item, ref)); keys.push(keyFor(item));
      used += probe.length + 1;
      return true;
    }

    // Con un filtro si cerca: contano gli elementi che portano quel testo, ovunque
    // siano. Senza, si guarda: conta quel che si vede adesso, e il resto si conta
    // invece di elencarlo (e' cio' che tiene una pagina vera dentro il tetto).
    for (var i = 0; i < items.length; i++) {
      var it = items[i];
      var wanted = filter
        ? it.name.toLowerCase().indexOf(filter) >= 0
        : it.inView;
      if (!wanted) { deferred[it.role] = (deferred[it.role] || 0) + 1; continue; }
      if (!push(it)) { deferred[it.role] = (deferred[it.role] || 0) + 1; }
    }

    // Il resto si conta, non si elenca. Elencarlo è quello che porta una pagina
    // vera a decine di migliaia di caratteri (misurato: 102.510 su Wikipedia).
    var rest = [];
    for (var k in deferred) if (deferred[k]) rest.push(deferred[k] + ' ' + k);
    var trailer = '';
    if (rest.length) {
      trailer = (filter ? '… senza "' + filter + '" nel nome: ' : '… fuori schermo: ') +
        rest.sort().join(', ') +
        (filter ? '.' : ' — usa filter="<testo>" per raggiungerli, o scroll.');
    }
    if (truncated) {
      trailer = (trailer ? trailer + '\n' : '') +
        '… snapshot troncato a ' + maxChars + ' caratteri.';
    }

    var text = lines.join('\n') + (trailer ? '\n' + trailer : '');

    // Modalità differenza: si mandano solo le righe nuove. Dopo una navigazione
    // J.prev non c'è (documento nuovo) e si ricade sul pieno, che è corretto.
    var diff = null;
    if (args.mode === 'diff' && J.prev) {
      var before = {};
      for (var b = 0; b < J.prev.length; b++) before[J.prev[b]] = 1;
      var added = [];
      for (var c = 0; c < lines.length; c++) if (!before[keys[c]]) added.push(lines[c]);
      var kept = lines.length - added.length;
      var removed = J.prev.length - kept;
      diff = (added.length ? added.join('\n') : '(niente di nuovo sulla pagina)') +
        '\n… ' + kept + ' invariate, ' + removed + ' sparite.' +
        (trailer ? '\n' + trailer : '');
    }
    J.prev = keys;

    return {
      url: location.href,
      title: clean(document.title).slice(0, 100),
      version: version,
      refs: n,
      total: items.length,
      chars: text.length,
      text: diff !== null ? diff : text,
      mode: diff !== null ? 'diff' : 'full',
    };
  }

  // ---------------------------------------------------------------- azioni

  function resolve(ref) {
    if (!ref) return { error: 'ref mancante' };
    var v = String(ref).split(':')[0];
    if (String(J.v) !== v) {
      return { error: 'ref "' + ref + '" è della versione ' + v + ', lo snapshot corrente è ' +
        J.v + ' — la pagina è cambiata, rifai browser_snapshot' };
    }
    var el = J.refs[ref];
    if (!el) return { error: 'ref "' + ref + '" sconosciuto in questo snapshot' };
    if (!el.isConnected) {
      return { error: 'ref "' + ref + '" non è più nel documento — rifai browser_snapshot' };
    }
    return { el: el };
  }

  function fire(el, type) {
    el.dispatchEvent(new Event(type, { bubbles: true }));
  }

  function act(args) {
    var steps = args.steps || [];
    var results = [];
    var navHint = false;
    for (var i = 0; i < steps.length; i++) {
      var st = steps[i];
      var a = (st.action || '').toLowerCase();
      var r = { i: i, action: a, ok: false };
      try {
        if (a === 'wait') {
          r.ok = true;                       // l'attesa vera la fa Kotlin
        } else if (a === 'scroll') {
          var amount = (st.amount || 1) * (window.innerHeight || 800) * 0.85;
          if ((st.direction || 'down') === 'up') amount = -amount;
          window.scrollBy(0, amount);
          r.ok = true; r.scrollY = window.scrollY;
        } else if (a === 'press') {
          var target = document.activeElement || document.body;
          var key = st.key || 'Enter';
          ['keydown', 'keyup'].forEach(function (t) {
            target.dispatchEvent(new KeyboardEvent(t, { key: key, bubbles: true }));
          });
          if (key === 'Enter' && target.form) { target.form.requestSubmit ?
            target.form.requestSubmit() : target.form.submit(); navHint = true; }
          r.ok = true;
        } else {
          var res = resolve(st.ref);
          if (res.error) { r.error = res.error; results.push(r); break; }
          var el = res.el;
          if (a === 'click') {
            el.scrollIntoView({ block: 'center' });
            if (el.tagName === 'A' && el.getAttribute('href')) navHint = true;
            if (el.type === 'submit' || el.tagName === 'BUTTON') navHint = true;
            el.click();
            r.ok = true;
          } else if (a === 'type') {
            el.focus();
            el.value = st.text || '';
            fire(el, 'input'); fire(el, 'change');
            r.ok = true;
          } else if (a === 'select') {
            var want = String(st.value == null ? '' : st.value).toLowerCase();
            var picked = -1;
            for (var o = 0; o < (el.options || []).length; o++) {
              var opt = el.options[o];
              if (String(opt.value).toLowerCase() === want ||
                  clean(opt.text).toLowerCase() === want) { picked = o; break; }
            }
            if (picked < 0) {
              r.error = 'nessuna opzione "' + st.value + '" in questo elenco';
              results.push(r); break;
            }
            el.selectedIndex = picked;
            fire(el, 'input'); fire(el, 'change');
            r.ok = true; r.selected = clean(el.options[picked].text);
          } else {
            r.error = 'azione sconosciuta: ' + a;
            results.push(r); break;
          }
        }
      } catch (e) {
        r.error = String(e && e.message ? e.message : e);
        results.push(r); break;
      }
      results.push(r);
    }
    var failed = results.length && !results[results.length - 1].ok;
    return { results: results, failed: failed, navHint: navHint, done: results.length };
  }

  // ---------------------------------------------------------------- lettura

  function read(args) {
    var maxChars = args.maxChars || 4000;
    var el = document.body;
    if (args.ref) {
      var res = resolve(args.ref);
      if (res.error) return { error: res.error };
      el = res.el;
    } else {
      var main = document.querySelector('main, [role="main"], article');
      if (main) el = main;
    }
    var txt = clean(el.innerText || el.textContent || '');
    return {
      url: location.href,
      chars: txt.length,
      truncated: txt.length > maxChars,
      text: txt.slice(0, maxChars),
    };
  }

  // ---------------------------------------------------------------- dispatch

  try {
    if (ARGS.op === 'snapshot') return JSON.stringify(snapshot(ARGS));
    if (ARGS.op === 'act') return JSON.stringify(act(ARGS));
    if (ARGS.op === 'read') return JSON.stringify(read(ARGS));
    return JSON.stringify({ error: 'op sconosciuta: ' + ARGS.op });
  } catch (e) {
    return JSON.stringify({ error: 'JS: ' + String(e && e.message ? e.message : e) });
  }
})(__ARGS__);
