/** Mobile Apps Controller — Jenny Apps / Skill / App Android accordion sections. */

import { api } from './shared/api-client.js';
import { escapeHtml, showToast } from './shared/utils.js';
import { confirmDialog } from './shared/dialog.js';
import { i18n } from './shared/i18n.js';
import { wsManager } from './shared/ws-manager.js';
import { currentTheme, themeTokens } from './shared/theme.js';
import { setupLongPress } from './shared/longpress.js';
import { advancedMode } from './shared/advanced-mode.js';

const SECTIONS = [
  { key: 'jenny', titleKey: 'apps.jennyApps', icon: 'ti-apps' },
  { key: 'skills', titleKey: 'apps.skill', icon: 'ti-puzzle' },
  { key: 'android', titleKey: 'apps.androidApp', icon: 'ti-apps' },
];

export class AppsController {
  constructor() {
    this.contentEl = document.getElementById('apps-content');
    this.searchInput = document.getElementById('apps-search-input');
    this.skills = [];
    this.androidApps = [];
    this.jennyApps = [];
    this._skillsLoaded = false;
    this._androidAppsLoaded = false;
    this._jennyAppsLoaded = false;
    this.collapsedSections = new Set();
    this._openApp = null;
    // Skill in apertura nell'editor del Workspace (v. _openSkillFile): il
    // percorso ha due await, e una seconda apertura concorrente scriverebbe
    // sopra `currentDir`/`_returnMode` della prima.
    this._openingSkill = null;
    // Richieste di HTML all'app aperta in volo: nonce → { resolve, timer }.
    this._appHtmlWaiters = new Map();
    this._appHtmlSeq = 0;
    this.hiddenPackages = new Set();
    this._hiddenLoaded = false;
    this._showHidden = false;
    this._pendingReload = false;
    this._androidRefreshTimer = null;
    this._androidLoadSeq = 0;
    this._removalsAnnounced = new Set();

    this.searchInput?.addEventListener('input', () => this.render());
    // Bridge from sandboxed app iframes (opaque origin → origin check is
    // meaningless; we validate the source window instead).
    window.addEventListener('message', e => this._onAppMessage(e));
    /* Agent-side storage mutations → refresh the open app iframe.

       Stream non filtrato per `chat_id`, **e deve restare così**: questi due
       frame sono broadcast a ogni connessione e non portano `chat_id` affatto
       (`ws_sender.send_app_data_changed` / `send_apps_list_changed`), quindi
       l'evento per-chat (`chat:<id>:message`) non scatterebbe mai per loro. Non
       sono nemmeno per-conversazione: i dati di una app sono gli stessi
       qualunque chat li abbia cambiati, e una app aperta va aggiornata comunque.
       Il filtro sul `chat_id` riguarda i frame che *dipingono una
       conversazione*, che stanno in `mobile-chat.js`. */
    wsManager.addEventListener('chat:message', e => {
      if (e.detail?.event === 'app_data_changed') this.notifyAppDataChanged(e.detail.slug);
      if (e.detail?.event === 'apps_list_changed') this._reloadJennyApps();
    });
    // SPA theme toggled while an app is open → restamp the iframe's theme.
    // Apps receive the resolved binary scheme, the theme accent and the rest of
    // the palette (`themeTokens`): senza quest'ultima l'app resterebbe alla
    // copia statica di jenny-kit.css e solo l'accent seguirebbe il tema.
    new MutationObserver(() => {
      const t = currentTheme();
      this._openApp?.iframe.contentWindow?.postMessage(
        { type: 'jenny:theme', theme: t.scheme, accent: t.accent, onAccent: t.onAccent,
          tokens: themeTokens() }, '*');
    }).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    window.addEventListener('advancedmodechange', () => this.render());
  }

  async loadSkills() {
    try {
      const data = await api.getSkills();
      this.skills = (data.skills || []).filter(s => s.source === 'workspace');
    } catch {
      this.skills = [];
    }
    this._skillsLoaded = true;
    this.render();
  }

  /** Ricarica la lista delle app Android.
   *
   *  Con ``announceRemovals`` le app che sono sparite rispetto alla lista
   *  precedente vengono annunciate con un toast: è la conferma visibile di una
   *  disinstallazione andata a buon fine (il dialog di sistema non ce ne dà
   *  nessuna). Il confronto si fa solo se la fetch è riuscita, altrimenti una
   *  lista vuota per errore verrebbe letta come "disinstallato tutto". */
  async loadAndroidApps({ announceRemovals = false } = {}) {
    const token = ++this._androidLoadSeq;
    const previous = this.androidApps;
    let apps = null;
    try {
      const data = await api.getAndroidApps();
      apps = data.apps || [];
    } catch {
      apps = null;
    }
    // Le risposte possono tornare fuori ordine: se nel frattempo è partita una
    // fetch più recente, questa è vecchia e riscriverebbe la griglia con uno
    // stato stantio (proprio l'app appena disinstallata tornerebbe su).
    if (token !== this._androidLoadSeq) return;
    this.androidApps = apps || [];
    this._androidAppsLoaded = true;
    if (announceRemovals && apps) {
      const present = new Set(apps.map(a => a.packageName));
      // `_removalsAnnounced` scarta le rimozioni per cui il toast è già uscito
      // dalla via del broadcast: senza questo, una fetch partita prima di quella
      // notifica annuncerebbe la stessa disinstallazione una seconda volta. Da
      // qui in poi il set è inutile: qualunque fetch più vecchia perde comunque
      // il controllo sul token e non annuncia niente.
      const gone = previous.filter(a =>
        !present.has(a.packageName) && !this._removalsAnnounced.has(a.packageName));
      this._removalsAnnounced.clear();
      if (gone.length) {
        this._forgetHiddenPackages(gone.map(a => a.packageName));
        this._announceUninstalled(gone.map(a => a.label));
      }
    }
    this.render();
  }

  /** Conferma visibile di una disinstallazione: il dialog di sistema non ne dà
   *  nessuna. Un solo toast anche per più app — i toast sono sovrapposti, non
   *  impilati, quindi due insieme si coprirebbero. */
  _announceUninstalled(labels) {
    const message = labels.length === 1
      ? i18n.t('apps.uninstalled', { name: labels[0] })
      : i18n.t('apps.uninstalledMany', { names: labels.join(', ') });
    showToast(message, 'success');
  }

  /** Un pacchetto disinstallato non deve restare nell'elenco delle nascoste:
   *  altrimenti una futura reinstallazione ricomparirebbe già invisibile. */
  _forgetHiddenPackages(packageNames) {
    const removed = packageNames.filter(pkg => this.hiddenPackages.delete(pkg));
    if (removed.length) this._persistHiddenApps();
  }

  async loadHiddenApps() {
    try {
      const data = await api.getHiddenApps();
      this.hiddenPackages = new Set(data.packages || []);
    } catch {
      this.hiddenPackages = new Set();
    }
    this._hiddenLoaded = true;
    this.render();
  }

  async _persistHiddenApps() {
    try {
      await api.setHiddenApps([...this.hiddenPackages]);
    } catch {
      // best-effort: the in-memory set still reflects the user's choice
    }
  }

  async loadJennyApps() {
    try {
      const data = await api.getJennyApps();
      this.jennyApps = data.apps || [];
    } catch {
      this.jennyApps = [];
    }
    this._jennyAppsLoaded = true;
    this.render();
  }

  _reloadJennyApps() {
    this._jennyAppsLoaded = false;
    this.loadJennyApps();
  }

  render() {
    if (!this.contentEl) return;
    const q = (this.searchInput?.value || '').toLowerCase().trim();
    this.contentEl.innerHTML = SECTIONS.map(section => this._renderSection(section, q)).join('');
    this.wireEvents();
  }

  _renderSection(section, q) {
    let bodyHtml;
    if (section.key === 'skills') {
      bodyHtml = this._renderSkillsGrid(this.skills.filter(s =>
        (!q || s.name.toLowerCase().includes(q)) && (advancedMode() || !s.internal)));
    } else if (section.key === 'android') {
      bodyHtml = this._renderAndroidGrid(this.androidApps.filter(a => !q || a.label.toLowerCase().includes(q)));
    } else {
      bodyHtml = this._renderJennyGrid(this.jennyApps.filter(a => !q || (a.name || a.slug).toLowerCase().includes(q)));
    }

    const forceExpanded = q.length > 0;
    const collapsed = !forceExpanded && this.collapsedSections.has(section.key);

    return `<div class="apps-section${collapsed ? ' collapsed' : ''}" data-section="${section.key}">
      <div class="apps-section-header">
        <i class="ti ${section.icon}"></i>
        <span>${escapeHtml(i18n.t(section.titleKey))}</span>
        <i class="ti ti-chevron-down apps-chevron"></i>
      </div>
      <div class="apps-section-body">${bodyHtml}</div>
    </div>`;
  }

  _renderSkillsGrid(filtered) {
    let html = '<div class="apps-grid">';
    filtered.forEach(skill => {
      const isActive = skill.available && !skill.disabled;
      const badge = isActive ? 'ab-active' : 'ab-idle';
      const badgeText = skill.disabled ? i18n.t('apps.disabled') : (skill.available ? i18n.t('apps.active') : i18n.t('apps.idle'));
      html += `<div class="app-cell" data-skill="${escapeHtml(skill.name)}">
        <div class="app-icon"><i class="ti ti-puzzle"></i></div>
        <div class="app-label">${escapeHtml(skill.name)}</div>
        <div class="app-badge ${badge}">${badgeText}</div>
      </div>`;
    });
    html += `<div class="app-cell" id="app-add">
      <div class="app-icon" style="border: 1.5px dashed var(--border-strong); background: transparent;">
        <i class="ti ti-plus" style="color: var(--text-faint)"></i>
      </div>
      <div class="app-label" style="color: var(--text-faint)">${i18n.t('apps.newSkill')}</div>
    </div>`;
    html += '</div>';

    if (!filtered.length && this._skillsLoaded) {
      html += this._emptyMessage(i18n.t('apps.noResults'));
    }
    return html;
  }

  _renderJennyGrid(filtered) {
    let html = '<div class="apps-grid">';
    filtered.forEach(app => {
      const icon = app.broken ? 'ti-alert-triangle' : (app.icon || 'ti-apps');
      const badge = app.broken ? `<div class="app-badge ab-idle">${i18n.t('apps.broken')}</div>` : '';
      const error = app.broken
        ? `<div class="app-error">${escapeHtml(app.error || i18n.t('apps.invalidManifest'))}</div>` : '';
      html += `<div class="app-cell" data-jenny-slug="${escapeHtml(app.slug)}">
        <div class="app-icon"><i class="ti ${escapeHtml(icon)}"></i></div>
        <div class="app-label">${escapeHtml(app.name || app.slug)}</div>
        ${badge}${error}
      </div>`;
    });
    html += `<div class="app-cell" id="jenny-app-add">
      <div class="app-icon" style="border: 1.5px dashed var(--border-strong); background: transparent;">
        <i class="ti ti-plus" style="color: var(--text-faint)"></i>
      </div>
      <div class="app-label" style="color: var(--text-faint)">${i18n.t('apps.newApp')}</div>
    </div>`;
    html += '</div>';

    if (!filtered.length && this._jennyAppsLoaded) {
      html += this._emptyMessage(i18n.t('apps.noResults'));
    }
    return html;
  }

  _renderAndroidGrid(filtered) {
    // Wait for the hidden-apps list before rendering any cell, so hidden apps
    // are never shown (not even briefly) on the initial load race.
    const ready = this._androidAppsLoaded && this._hiddenLoaded;
    const visible = ready
      ? filtered.filter(app => this._showHidden || !this.hiddenPackages.has(app.packageName))
      : [];
    let html = '<div class="apps-grid">';
    visible.forEach(app => {
      const icon = app.icon ? `<img src="${escapeHtml(app.icon)}" alt="">` : '<i class="ti ti-apps"></i>';
      const isHidden = this.hiddenPackages.has(app.packageName);
      const hiddenBadge = isHidden ? '<div class="app-hidden-badge"><i class="ti ti-eye-off"></i></div>' : '';
      html += `<div class="app-cell${isHidden ? ' app-cell--hidden' : ''}" data-android-package="${escapeHtml(app.packageName)}"${app.system ? ' data-android-system="1"' : ''}>
        <div class="app-icon">${icon}${hiddenBadge}</div>
        <div class="app-label">${escapeHtml(app.label)}</div>
      </div>`;
    });
    html += '</div>';

    if (!visible.length) {
      html += this._emptyMessage(ready ? i18n.t('apps.noAppsFound') : i18n.t('apps.loading'));
    }
    return html;
  }

  _emptyMessage(text) {
    return `<div style="padding: 20px; color: var(--text-faint); font-style: italic; text-align: center;">${escapeHtml(text)}</div>`;
  }

  wireEvents() {
    // Tastiera fisica (Titan 2): celle e intestazioni sono <div>, quindi senza
    // tabindex/role non esistono né per Tab né per TalkBack e la sezione App
    // non è utilizzabile da tastiera per nulla. Invio e Spazio sintetizzano il
    // click, così l'attivazione resta un percorso solo.
    this.contentEl.querySelectorAll('.app-cell, .apps-section-header').forEach(el => {
      el.setAttribute('tabindex', '0');
      el.setAttribute('role', 'button');
      el.addEventListener('keydown', (e) => {
        if (e.key !== 'Enter' && e.key !== ' ' && e.key !== 'Spacebar') return;
        e.preventDefault();  // lo Spazio scorrerebbe la lista sotto
        el.click();
      });
    });
    this.contentEl.querySelectorAll('.apps-section-header').forEach(header => {
      header.setAttribute(
        'aria-expanded',
        String(!header.closest('.apps-section')?.classList.contains('collapsed')),
      );
      header.addEventListener('click', () => {
        const section = header.closest('.apps-section');
        const key = section?.dataset.section;
        if (!key) return;
        const isCollapsed = section.classList.toggle('collapsed');
        header.setAttribute('aria-expanded', String(!isCollapsed));
        if (isCollapsed) this.collapsedSections.add(key);
        else this.collapsedSections.delete(key);
      });
    });
    this.contentEl.querySelectorAll('.app-cell[data-skill]').forEach(cell => {
      cell.addEventListener('click', () => {
        if (cell.dataset.longpress) { delete cell.dataset.longpress; return; }
        const name = cell.dataset.skill;
        const skill = this.skills.find(s => s.name === name);
        // Le skill "locked" aprono la scheda info (sola lettura) al tap, non
        // l'editor del file — l'apertura diretta resta per le skill proprie
        // dell'utente e, in Modalità avanzata, anche per quelle di sistema.
        if (skill?.locked && !advancedMode()) this.showSkillSheet(name);
        else this._openSkillFile(name);
      });
      setupLongPress(cell, () => this.showSkillSheet(cell.dataset.skill));
    });
    this.contentEl.querySelector('#app-add')?.addEventListener('click', () => this._startSkillCreation());
    this.contentEl.querySelectorAll('.app-cell[data-android-package]').forEach(cell => {
      cell.addEventListener('click', () => {
        if (cell.dataset.longpress) { delete cell.dataset.longpress; return; }
        this.launchAndroidApp(cell.dataset.androidPackage);
      });
      setupLongPress(cell, () => this.showAndroidAppSheet(cell.dataset.androidPackage));
    });
    this.contentEl.querySelectorAll('.app-cell[data-jenny-slug]').forEach(cell => {
      cell.addEventListener('click', () => {
        if (cell.dataset.longpress) { delete cell.dataset.longpress; return; }
        this.openApp(cell.dataset.jennySlug);
      });
      setupLongPress(cell, () => this.showJennyAppSheet(cell.dataset.jennySlug));
    });
    this.contentEl.querySelector('#jenny-app-add')?.addEventListener('click', () => this._startAppCreation());
  }

  // ── Jenny Apps ──

  async openApp(slug) {
    const app = this.jennyApps.find(a => a.slug === slug);
    if (!app) return;
    if (app.broken) {
      const ok = await confirmDialog(
        i18n.t('apps.brokenConfirm', { name: app.name || slug, error: app.error || i18n.t('apps.invalidManifest') })
      );
      if (ok) {
        this._sendChatPrompt(i18n.t('apps.brokenPrompt', { slug, error: app.error || i18n.t('apps.invalidManifest') }));
      }
      return;
    }

    if (!api.getSecret()) {
      try { await api.bootstrap(); } catch { return; }
    }
    const t = currentTheme();
    const lang = document.documentElement.lang || 'it';
    const src = `/apps/${encodeURIComponent(slug)}/index.html`
      + `?token=${encodeURIComponent(api.getSecret())}`
      + `&theme=${encodeURIComponent(t.scheme)}&lang=${encodeURIComponent(lang)}`
      + `&accent=${encodeURIComponent(t.accent)}&onAccent=${encodeURIComponent(t.onAccent)}`
      + `&tokens=${encodeURIComponent(themeTokens())}`;

    this.closeApp();
    const overlay = document.createElement('div');
    overlay.className = 'app-frame-overlay';
    overlay.innerHTML = `
      <div class="app-frame-header">
        <span class="app-frame-title">${escapeHtml(app.name || slug)}</span>
        <button class="app-frame-close" title="${i18n.t('apps.close')}"><i class="ti ti-x"></i></button>
      </div>
    `;
    const iframe = document.createElement('iframe');
    // Opaque origin on purpose: the app must not reach the SPA DOM/localStorage.
    iframe.setAttribute('sandbox', 'allow-scripts');
    iframe.src = src;
    overlay.appendChild(iframe);
    overlay.querySelector('.app-frame-close').addEventListener('click', () => this.closeApp());

    document.body.appendChild(overlay);
    requestAnimationFrame(() => overlay.classList.add('visible'));
    this._openApp = { slug, overlay, iframe, depth: 1 };
  }

  handleBack() {
    const open = this._openApp;
    if (!open) return false;
    /* `depth` è ciò che l'app dichiara via `jenny:nav-state`: schermate interne
       più <dialog> aperti. I dialog contano perché l'iframe ha origine opaca e
       il livello `dialog` della catena, che interroga solo il documento del
       parent, non li vede: senza questo ramo Indietro chiudeva tutta l'app
       portandosi via il form a metà. */
    if (open.depth > 1) {
      open.iframe.contentWindow?.postMessage({ type: 'jenny:go-back' }, '*');
      return true;
    }
    // Back out of the mini-app: reveal the Apps tab underneath. Do not push a
    // forward history entry (we are going *back*); switchMode is only needed on
    // the off chance the app was opened from another mode.
    this.closeApp();
    /* Col cassetto aperto, l'app è stata lanciata da lì: la destinazione del
       ritorno è il foglio, non la scheda. Senza questa uscita anticipata lo
       `switchMode` qui sotto chiuderebbe il foglio (v. MobileApp.switchMode),
       e una pressione di Indietro smonterebbe due livelli invece di uno —
       proprio ciò che l'ordine `miniapp` → `launcher` promette di non fare. */
    if (window.mobileApp.launcher?.isOpen()) return true;
    if (window.mobileApp.currentMode !== 'apps') window.mobileApp.switchMode('apps', false);
    return true;
  }

  /* Smontare l'overlay basta perché l'SDK non scrive la history: la profondità
     dell'app è pura contabilità (v. jenny-sdk.js). Quando invece l'SDK spingeva
     le schermate nella history con `pushState`, ognuna lasciava una entry nella
     joint session history del WebView che nemmeno `iframe.remove()` toglieva —
     e dopo la ✕ restavano pressioni di Indietro morte. */
  closeApp() {
    const open = this._openApp;
    if (!open) return;
    this._openApp = null;
    open.overlay.classList.remove('visible');
    setTimeout(() => open.overlay.remove(), 200);
  }

  notifyAppDataChanged(slug) {
    const open = this._openApp;
    if (!open || (slug && open.slug !== slug)) return;
    open.iframe.contentWindow?.postMessage({ type: 'jenny:data-changed', slug: open.slug }, '*');
  }

  _onAppMessage(event) {
    const open = this._openApp;
    if (!open || event.source !== open.iframe.contentWindow) return;
    const msg = event.data;
    if (!msg || typeof msg !== 'object') return;
    if (msg.type === 'jenny:nav-state') {
      // Il valore arriva da codice dell'app: si accetta solo un intero in un
      // intervallo sensato, altrimenti un NaN (o un numero enorme) renderebbe
      // Indietro inutile fino alla ✕.
      const depth = Math.floor(Number(msg.depth));
      open.depth = Number.isFinite(depth) ? Math.min(99, Math.max(1, depth)) : 1;
      return;
    }
    if (msg.type === 'jenny:discuss') {
      const app = this.jennyApps.find(a => a.slug === open.slug);
      const name = app?.name || open.slug;
      const text = String(msg.text || '').slice(0, 4000);
      this.closeApp();
      this._sendChatPrompt(i18n.t('apps.chatAboutApp', { name, text }));
      return;
    }
    if (msg.type === 'jenny:ui-result') {
      // Risposta al round-trip di requestAppHtml: risolve il waiter del nonce.
      const waiter = this._appHtmlWaiters.get(msg.nonce);
      if (waiter) {
        this._appHtmlWaiters.delete(msg.nonce);
        clearTimeout(waiter.timer);
        waiter.resolve(String(msg.html || ''));
      }
    }
  }

  /* Chiede all'iframe dell'app aperta il proprio HTML (l'SDK legge il suo DOM
     e lo rimanda: il parent non può leggerlo, l'iframe ha origin opaca). Ritorna
     null se non c'è un'app aperta o se l'app non risponde entro il timeout. */
  requestAppHtml(timeoutMs = 2000) {
    const open = this._openApp;
    if (!open || !open.iframe.contentWindow) return Promise.resolve(null);
    const nonce = 'app-html-' + (++this._appHtmlSeq);
    return new Promise(resolve => {
      const timer = setTimeout(() => {
        this._appHtmlWaiters.delete(nonce);
        resolve(null);
      }, timeoutMs);
      this._appHtmlWaiters.set(nonce, { resolve, timer });
      open.iframe.contentWindow.postMessage({ type: 'jenny:ui-query', nonce }, '*');
    });
  }

  _startAppCreation() {
    this._sendChatPrompt(i18n.t('apps.createAppPrompt'));
  }

  async launchAndroidApp(packageName) {
    try {
      await api.launchAndroidApp(packageName);
    } catch {
      // best-effort: nothing to recover client-side if the launch failed
    }
  }

  /** Il PackageManager ha annunciato un cambio di pacchetto (notifica da
   *  MainActivity). È la via principale per tenere il launcher aggiornato: il
   *  dialog di disinstallazione di sistema è spesso un'activity translucida,
   *  quindi la WebView non passa mai per ``hidden`` e il fallback su
   *  ``visibilitychange`` qui sotto non scatta.
   *
   *  L'icona viene rimossa subito (la lista in memoria ha già tutto il
   *  necessario) e la fetch che riallinea con PackageManager — costosa, ricodifica
   *  tutte le icone in base64 — è posticipata e accorpata. */
  onPackageChanged(kind, packageName) {
    if (kind === 'removed') {
      const app = this.androidApps.find(a => a.packageName === packageName);
      // Pacchetto senza activity di launcher: mai stato in lista, niente da dire.
      if (app) {
        this.androidApps = this.androidApps.filter(a => a.packageName !== packageName);
        this._forgetHiddenPackages([packageName]);
        this.render();
        this._removalsAnnounced.add(packageName);
        this._announceUninstalled([app.label]);
      }
    }
    clearTimeout(this._androidRefreshTimer);
    // `announceRemovals` anche qui: il broadcast può mancare per un'app (il
    // filtro di visibilità dei pacchetti non garantisce la consegna), e in quel
    // caso la sua sparizione la vede solo il confronto con la lista precedente.
    this._androidRefreshTimer = setTimeout(
      () => this.loadAndroidApps({ announceRemovals: true }), 500);
  }

  /** Reload the Android app list once, when the WebView next regains focus
   * (e.g. after returning from the system uninstall / app-info screen).
   * Seconda linea di difesa dietro ``onPackageChanged``: copre il caso in cui
   * il broadcast di sistema non arrivi (es. disinstallazione fatta da "Info
   * app"), quindi annuncia anche lei le app sparite. */
  _reloadAndroidAppsOnReturn() {
    if (this._pendingReload) return;
    this._pendingReload = true;
    const onVisible = () => {
      if (document.visibilityState !== 'visible') return;
      document.removeEventListener('visibilitychange', onVisible);
      this._pendingReload = false;
      this.loadAndroidApps({ announceRemovals: true });
    };
    document.addEventListener('visibilitychange', onVisible);
  }

  // ── App Android context sheet ──

  showAndroidAppSheet(packageName) {
    const app = this.androidApps.find(a => a.packageName === packageName);
    if (!app) return;

    const sheet = document.getElementById('android-app-sheet');
    if (!sheet) return;

    const isHidden = this.hiddenPackages.has(packageName);
    const icon = app.icon
      ? `<img src="${escapeHtml(app.icon)}" alt="">`
      : '<i class="ti ti-apps"></i>';
    document.getElementById('android-app-title').innerHTML =
      `<div class="app-sheet-head">
        <div class="app-sheet-icon">${icon}</div>
        <div class="app-sheet-name">${escapeHtml(app.label)}</div>
      </div>`;

    const actions = [
      { icon: 'ti-player-play', label: i18n.t('apps.open'), action: 'launch' },
      { icon: 'ti-info-circle', label: i18n.t('apps.appInfo'), action: 'info' },
    ];
    if (!app.system) {
      actions.push({ icon: 'ti-trash', label: i18n.t('apps.uninstall'), action: 'uninstall', danger: true });
    }
    actions.push(isHidden
      ? { icon: 'ti-eye', label: i18n.t('apps.show'), action: 'unhide' }
      : { icon: 'ti-eye-off', label: i18n.t('apps.hide'), action: 'hide' });

    const actionsEl = document.getElementById('android-app-actions');
    actionsEl.innerHTML = actions.map(a =>
      `<button class="oc-sheet-action${a.danger ? ' danger' : ''}" data-action="${a.action}">
        <i class="ti ${a.icon}"></i>${a.label}
      </button>`
    ).join('');

    const close = () => sheet.close();

    actionsEl.querySelectorAll('.oc-sheet-action').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        sheet.close();
        await this._handleAndroidSheetAction(btn.dataset.action, app);
      });
    });

    const cancelBtn = document.getElementById('android-app-cancel');
    cancelBtn.onclick = close;
    // Ignore the synthetic tap that follows a touch long-press for a moment,
    // so it doesn't immediately close the freshly-opened sheet via the backdrop.
    const openedAt = Date.now();
    sheet.onclick = (e) => { if (e.target === sheet && Date.now() - openedAt > 400) close(); };

    sheet.showModal();
  }

  async _handleAndroidSheetAction(action, app) {
    const pkg = app.packageName;
    if (action === 'launch') {
      this.launchAndroidApp(pkg);
    } else if (action === 'info') {
      try { await api.openAndroidAppInfo(pkg); } catch {}
      // Da "Info app" si può disinstallare: al rientro la lista va riallineata.
      this._reloadAndroidAppsOnReturn();
    } else if (action === 'uninstall') {
      // Nessuna conferma nostra: quella di Android arriva comunque e non è
      // aggirabile, quindi la nostra era solo un tap in più prima della
      // domanda vera. Stesso comportamento dei launcher di sistema.
      try { await api.uninstallAndroidApp(pkg); } catch {}
      this._reloadAndroidAppsOnReturn();
    } else if (action === 'hide') {
      this.hiddenPackages.add(pkg);
      this._persistHiddenApps();
      this.render();
    } else if (action === 'unhide') {
      this.hiddenPackages.delete(pkg);
      this._persistHiddenApps();
      this.render();
    }
  }

  // ── Skill file editor + context sheet ──

  /** Apre SKILL.md nell'editor del Workspace.
   *
   *  Fra il cambio di sezione e l'apertura vera ci sono due `await` (la
   *  `ready` del controller e la lettura del file): una finestra lunga
   *  abbastanza da riceverci dentro un secondo tap — la scheda skill si chiude
   *  con `sheet.close()` *prima* di invocare l'azione, quindi la griglia è di
   *  nuovo sotto il dito. Due aperture concorrenti si sovrascrivevano a
   *  vicenda `currentDir` e `_returnMode` e potevano lasciare l'editor su un
   *  file con il breadcrumb dell'altro. Il guard dichiara "apertura in corso" e
   *  scarta la seconda; il flag vive in `finally`, quindi un errore non lo
   *  lascia acceso. Il tasto Indietro non c'entra e non va toccato: qui non si
   *  consuma nessuna pressione. */
  async _openSkillFile(name) {
    if (this._openingSkill) return;
    this._openingSkill = name;
    const path = `skills/${name}/SKILL.md`;
    try {
      window.mobileApp.switchMode('workspace');
      const ws = window.mobileApp.controllers.workspace;
      await ws.ready;
      ws.currentDir = `skills/${name}`;
      await ws.openFile(path, 'md');
      ws._returnMode = 'apps';  // editor "back" returns to Apps, not the explorer
    } catch (err) {
      console.error('Failed to open skill file:', err);
      showToast(i18n.t('apps.cannotOpen', { name }), 'error');
    } finally {
      this._openingSkill = null;
    }
  }

  _skillUserSummary(skill) {
    const s = skill.user_summary;
    if (!s) return skill.description || skill.name;
    return s[i18n.locale] || s.it || s.en || skill.description || skill.name;
  }

  showSkillSheet(name) {
    const skill = this.skills.find(s => s.name === name);
    if (!skill) return;

    const sheet = document.getElementById('skill-sheet');
    if (!sheet) return;

    document.getElementById('skill-sheet-title').innerHTML =
      `<div class="app-sheet-head">
        <div class="app-sheet-icon"><i class="ti ti-puzzle"></i></div>
        <div class="app-sheet-name">${escapeHtml(skill.name)}</div>
      </div>`;

    const bodyEl = document.getElementById('skill-sheet-body');
    const actionsEl = document.getElementById('skill-sheet-actions');
    const close = () => sheet.close();

    // Le skill "locked" (bundle di sistema tipo cron/llm-wiki) mostrano solo
    // la spiegazione d'uso fuori dalla Modalità avanzata: niente azioni che
    // permetterebbero di modificarle/disabilitarle/eliminarle per sbaglio.
    if (skill.locked && !advancedMode()) {
      bodyEl.textContent = this._skillUserSummary(skill);
      actionsEl.innerHTML = '';
    } else {
      bodyEl.textContent = '';
      const actions = [
        { icon: 'ti-edit', label: i18n.t('apps.edit'), action: 'edit' },
        skill.disabled
          ? { icon: 'ti-eye', label: i18n.t('apps.enable'), action: 'enable' }
          : { icon: 'ti-eye-off', label: i18n.t('apps.disable'), action: 'disable' },
        { icon: 'ti-trash', label: i18n.t('apps.delete'), action: 'delete', danger: true },
      ];

      actionsEl.innerHTML = actions.map(a =>
        `<button class="oc-sheet-action${a.danger ? ' danger' : ''}" data-action="${a.action}">
          <i class="ti ${a.icon}"></i>${a.label}
        </button>`
      ).join('');

      actionsEl.querySelectorAll('.oc-sheet-action').forEach(btn => {
        btn.addEventListener('click', async (e) => {
          e.stopPropagation();
          sheet.close();
          await this._handleSkillSheetAction(btn.dataset.action, skill);
        });
      });
    }

    document.getElementById('skill-sheet-cancel').onclick = close;
    // Ignore the synthetic tap that follows a touch long-press for a moment,
    // so it doesn't immediately close the freshly-opened sheet via the backdrop.
    const openedAt = Date.now();
    sheet.onclick = (e) => { if (e.target === sheet && Date.now() - openedAt > 400) close(); };

    sheet.showModal();
  }

  async _handleSkillSheetAction(action, skill) {
    const name = skill.name;
    if (action === 'edit') {
      this._openSkillFile(name);
    } else if (action === 'disable' || action === 'enable') {
      try {
        await this._setSkillDisabled(name, action === 'disable');
        await this.loadSkills();
        this.render();
      } catch {
        showToast(i18n.t('apps.operationFailed'), 'error');
      }
    } else if (action === 'delete') {
      const ok = await confirmDialog(i18n.t('apps.deleteSkillConfirm', { name }));
      if (!ok) return;
      try {
        await this._deleteSkill(name);
        await this.loadSkills();
        this.render();
      } catch {
        showToast(i18n.t('apps.deleteFailed'), 'error');
      }
    }
  }

  // ── Jenny app context sheet ──

  showJennyAppSheet(slug) {
    const app = this.jennyApps.find(a => a.slug === slug);
    if (!app) return;

    const sheet = document.getElementById('jenny-app-sheet');
    if (!sheet) return;

    const icon = app.broken ? 'ti-alert-triangle' : (app.icon || 'ti-apps');
    document.getElementById('jenny-app-sheet-title').innerHTML =
      `<div class="app-sheet-head">
        <div class="app-sheet-icon"><i class="ti ${escapeHtml(icon)}"></i></div>
        <div class="app-sheet-name">${escapeHtml(app.name || app.slug)}</div>
      </div>`;

    const actions = [
      { icon: 'ti-player-play', label: i18n.t('apps.open'), action: 'open' },
      { icon: 'ti-edit', label: i18n.t('apps.edit'), action: 'edit' },
      { icon: 'ti-trash', label: i18n.t('apps.delete'), action: 'delete', danger: true },
    ];

    const actionsEl = document.getElementById('jenny-app-sheet-actions');
    actionsEl.innerHTML = actions.map(a =>
      `<button class="oc-sheet-action${a.danger ? ' danger' : ''}" data-action="${a.action}">
        <i class="ti ${a.icon}"></i>${a.label}
      </button>`
    ).join('');

    const close = () => sheet.close();

    actionsEl.querySelectorAll('.oc-sheet-action').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        sheet.close();
        await this._handleJennySheetAction(btn.dataset.action, app);
      });
    });

    document.getElementById('jenny-app-sheet-cancel').onclick = close;
    // Ignore the synthetic tap that follows a touch long-press for a moment,
    // so it doesn't immediately close the freshly-opened sheet via the backdrop.
    const openedAt = Date.now();
    sheet.onclick = (e) => { if (e.target === sheet && Date.now() - openedAt > 400) close(); };

    sheet.showModal();
  }

  async _handleJennySheetAction(action, app) {
    const slug = app.slug;
    if (action === 'open') {
      this.openApp(slug);
    } else if (action === 'edit') {
      this._startAppModification(app);
    } else if (action === 'delete') {
      const ok = await confirmDialog(
        i18n.t('apps.deleteAppConfirm', { name: app.name || slug })
      );
      if (!ok) return;
      try {
        await api.deleteJennyApp(slug);
        await this.loadJennyApps();
        this.render();
        showToast(i18n.t('apps.appDeleted'), 'success');
      } catch {
        showToast(i18n.t('apps.deleteFailed'), 'error');
      }
    }
  }

  _startAppModification(app) {
    this._sendChatPrompt(i18n.t('apps.editAppPrompt', { name: app.name || app.slug, slug: app.slug }));
  }

  // ── API calls ──

  async _startSkillCreation() {
    this._sendChatPrompt(i18n.t('apps.createSkillPrompt'));
  }

  async _sendChatPrompt(prompt) {
    window.mobileApp.switchMode('chat');
    const chat = window.mobileApp?.controllers?.chat;
    if (!chat) return;

    chat.input.value = prompt;
    chat._autoResize?.();
    chat._updateSendState?.();

    for (let i = 0; i < 40; i++) {
      await new Promise(r => setTimeout(r, 50));
      if (wsManager.chatWs?.readyState === WebSocket.OPEN) break;
    }

    chat.sendMessage();
  }

  async _setSkillDisabled(name, disabled) {
    const res = await fetch(
      `/api/webui/skills/${encodeURIComponent(name)}/update?disabled=${disabled}`,
      { headers: { 'Authorization': `Bearer ${api.getSecret()}` } });
    if (!res.ok) throw new Error(i18n.t('apps.updateFailed'));
  }

  async _deleteSkill(name) {
    const res = await fetch(`/api/webui/skills/${encodeURIComponent(name)}/delete`, {
      headers: { 'Authorization': `Bearer ${api.getSecret()}` }
    });
    if (!res.ok) throw new Error(i18n.t('apps.deleteSkillFailed'));
  }

  activate() {
    if (!this._skillsLoaded) this.loadSkills();
    if (!this._androidAppsLoaded) this.loadAndroidApps();
    if (!this._jennyAppsLoaded) this.loadJennyApps();
    if (!this._hiddenLoaded) this.loadHiddenApps();
    if (this._skillsLoaded && this._androidAppsLoaded && this._jennyAppsLoaded) this.render();
  }

  deactivate() {
    this.closeApp();
    // Header actions are re-rendered (eye-off) on re-entry, so reset the
    // toggle state to stay in sync with the freshly-rendered icon.
    this._showHidden = false;
    // Force re-fetch of Jenny Apps on next activate (app may have been
    // created/modified by the agent while this tab was inactive).
    this._jennyAppsLoaded = false;
  }

  handleAction(action) {
    if (action === 'add-skill') this._startSkillCreation();
    else if (action === 'toggle-hidden') this._toggleShowHidden();
  }

  _toggleShowHidden() {
    this._showHidden = !this._showHidden;
    const iconEl = document.querySelector('#title-apps [data-action="toggle-hidden"] i');
    if (iconEl) {
      iconEl.classList.toggle('ti-eye', this._showHidden);
      iconEl.classList.toggle('ti-eye-off', !this._showHidden);
    }
    this.render();
  }
}
