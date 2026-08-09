/** Mobile App — Entry point and orchestration. */

import { AppState } from './shared/state.js';
import { sessionManager } from './shared/session-manager.js';
import { showToast } from './shared/utils.js';
import { i18n } from './shared/i18n.js';
import { api } from './shared/api-client.js';
import { ViewTitleController } from './mobile-header.js';
import { DrawerManager } from './mobile-drawer.js';
import { ChatController } from './mobile-chat.js';
import { WorkspaceController } from './mobile-workspace.js';
import { AppsController } from './mobile-apps.js';
import { GraphController } from './mobile-graph.js';
import { WikiController } from './mobile-wiki.js';
import { SettingsController } from './mobile-settings.js';
import { OnboardingController } from './mobile-onboarding.js';
import { JennyCompanion } from './mobile-jenny.js';
import { UiQueryResponder } from './mobile-ui-query.js';
import { keyboard } from './shared/keyboard.js';
import { homeView } from './shared/home-view.js';
import './shared/theme.js';

export { showToast };

/* ── Global Error Handling ── */
// Oltre al toast, l'errore viene inoltrato al log del gateway (/api/client-log):
// la console del WebView è visibile solo via adb, quindi senza inoltro un
// errore JS on-device è di fatto invisibile.
window.addEventListener('error', (e) => {
  console.error('Global error:', e.error);
  const detail = e.error && e.error.stack ? e.error.stack : `${e.message} @ ${e.filename}:${e.lineno}`;
  api.clientLog('error', 'window.onerror', detail);
  showToast(i18n.t('common.genericError'), 'error');
});

window.addEventListener('unhandledrejection', (e) => {
  console.error('Unhandled rejection:', e.reason);
  const detail = e.reason && e.reason.stack ? e.reason.stack : String(e.reason);
  api.clientLog('error', 'unhandledrejection', detail);
  showToast(i18n.t('common.networkError'), 'error');
});

/* ── Keyboard Helper ── */
function ensureVisible(el) {
  setTimeout(() => {
    el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }, 300);
}

class MobileApp {
  constructor() {
    this.header = new ViewTitleController();
    this.drawer = new DrawerManager();
    this.jenny = new JennyCompanion();
    this.uiQuery = new UiQueryResponder();

    // Lazy controller factories
    this.controllerFactories = {
      chat:      () => new ChatController(),
      workspace: () => new WorkspaceController(),
      apps:      () => new AppsController(),
      settings:  () => new SettingsController(),
      graph:     () => new GraphController(),
      wiki:      () => new WikiController(),
      onboarding: () => new OnboardingController(),
    };
    this.controllers = {};

    this.currentMode = null;
    // Posizione nello stack di navigazione *nostro* (0 = radice). Ogni entry
    // spinta da pushNav se la porta dietro, così il back sa quando è arrivato
    // in fondo. `history.length` non risponde alla domanda: conta l'intera
    // sessione del WebView (iframe delle mini-app, reload) e non cala mai.
    this._navPos = 0;
    // Gate di readiness dello shell nativo: alcune animazioni d'ingresso (es. la
    // caduta della mini Jenny nell'onboarding) devono partire solo quando il
    // loading nativo è sparito e il WebView è visibile, altrimenti scorrono
    // dietro l'overlay e se ne vede solo la coda.
    this._shellReady = false;
    this._shellReadyCbs = [];
    window.mobileApp = this;
    this.init();
  }

  /* Invocato dal guscio Android (MainActivity.hideLoading) a fade completato.
     Fuori dallo shell nativo lo emula whenShellReady via requestAnimationFrame. */
  onNativeReady() {
    if (this._shellReady) return;
    this._shellReady = true;
    this._shellReadyCbs.splice(0).forEach((cb) => cb());
  }

  /* Esegue `cb` quando lo shell è pronto (loading nascosto). In un browser
     normale — senza il bridge JennyNative — parte al frame successivo. */
  whenShellReady(cb) {
    if (this._shellReady) return cb();
    this._shellReadyCbs.push(cb);
    if (!window.JennyNative) requestAnimationFrame(() => this.onNativeReady());
  }

  async init() {
    // Bootstrap auth token before anything else
    try {
      await api.bootstrap();
    } catch (err) {
      console.error('Bootstrap failed:', err);
    }

    // Load i18n and update sidebar
    i18n.load(i18n.locale).then(() => {
      this._updateSidebarTitles();
      this._applyStaticTranslations();
      this.header._refreshTitles();
    });
    i18n.onLocaleChange(() => {
      this._updateSidebarTitles();
      this._applyStaticTranslations();
    });

    // Sidebar navigation
    document.querySelectorAll('.dock-item[data-mode]').forEach(item => {
      item.addEventListener('click', () => this.switchMode(item.dataset.mode));
    });

    // Drawer open/close sync
    this.drawer.addEventListener('open', () => this.header.syncDrawerTabs());
    this.drawer.addEventListener('close', () => this.header.syncDrawerTabs());

    // Browser back/forward
    window.addEventListener('popstate', (e) => {
      const state = e.state;
      if (!state) return;
      // La entry ripristinata porta con sé la propria posizione nello stack:
      // è così che handleHardwareBack sa se sotto c'è ancora roba nostra.
      this._navPos = typeof state.pos === 'number' ? state.pos : 0;
      if (state.wikiPage) {
        this.switchMode('wiki', false);
        this.controllers.wiki.loadWikiPage(state.wiki, state.page || 'index.md', false);
      } else if (state.mode === 'wiki') {
        this.switchMode('wiki', false);
        this.controllers.wiki.loadHome(false);
      } else if (state.mode === 'graph') {
        this.switchMode('graph', false);
        this.controllers.graph.loadGraph(state.wiki || null, false);
      } else if (state.mode) {
        this.switchMode(state.mode, false);
      } else {
        return;
      }
      // Sync URL with restored state
      this.replaceNav(state);
    });

    // Persist mode changes
    AppState.on('currentMode', (mode) => {
      localStorage.setItem('mobile-last-mode', mode);
    });

    // Viewport height sync (Android keyboard fix)
    this.setupViewportHeight();

    // Keyboard scroll helpers
    this.setupKeyboardHelpers();

    // Horizontal swipe to navigate between dock tabs
    this.setupSwipeNav();

    // Determine initial mode
    const urlParams = new URLSearchParams(window.location.search);
    const urlMode = urlParams.get('mode');
    const savedMode = localStorage.getItem('mobile-last-mode');
    let initialMode = urlMode || savedMode || 'chat';
    const initialWiki = urlParams.get('wiki');
    const initialPage = urlParams.get('page');

    // If URL has wiki param but no explicit mode, force wiki mode
    if (!urlMode && initialWiki) {
      initialMode = 'wiki';
    }

    // Check first-run: redirect to onboarding (always when first_run is true)
    try {
      const settings = await api.getSettings();
      if (settings?.first_run) {
        this._firstRun = true;
        initialMode = 'onboarding';
        localStorage.removeItem('onboarding-complete');
        const navOnb = document.getElementById('nav-onboarding');
        if (navOnb) navOnb.style.display = '';
        // Disable all sidebar items except onboarding
        document.querySelectorAll('.dock-item[data-mode]').forEach(item => {
          if (item.dataset.mode !== 'onboarding') {
            item.classList.add('nav-disabled');
            item.style.pointerEvents = 'none';
            item.style.opacity = '0.4';
          }
        });
      }
    } catch { /* ignore — fall through to normal mode */ }

    // After onboarding completed: force chat mode, clear stale state
    if (!this._firstRun && localStorage.getItem('onboarding-complete')) {
      localStorage.removeItem('onboarding-complete');
      localStorage.setItem('mobile-last-mode', 'chat');
      initialMode = 'chat';
    }

    // Initialize sessions and load module
    await this._initSessions();

    // Radice dello stack. La entry iniziale *è* già la vista iniziale: va
    // riscritta, non impilata. Prima si faceva replaceState + switchMode(push)
    // e restavano due entry identiche, così il primo Indietro veniva ingoiato
    // da switchMode (`mode === currentMode`) senza cambiare niente a schermo —
    // da lì la sensazione che il tasto "salti" una pagina.
    this._navPos = 0;
    this.replaceNav(this._navStateFor(initialMode, initialWiki, initialPage));
    this.switchMode(initialMode, false);

    // After initial mode switch, load the specific wiki graph if needed
    if (initialMode === 'graph' && initialWiki) {
      this.controllers.graph.loadGraph(initialWiki, false);
    }

    // Register keyboard shortcuts
    this._initKeyboardShortcuts();

    console.log('Mobile app initialized');
  }

  _updateSidebarTitles() {
    const titleMap = {
      'chat': 'nav.chat',
      'workspace': 'nav.workspace',
      'apps': 'nav.apps',
      'settings': 'nav.settings',
      'graph': 'nav.wiki',
      'wiki': 'nav.wiki',
      'onboarding': 'nav.onboarding',
    };
    document.querySelectorAll('.dock-item[data-mode]').forEach(item => {
      const key = titleMap[item.dataset.mode];
      if (key) item.title = i18n.t(key);
    });
  }

  _applyStaticTranslations() {
    document.querySelectorAll('[data-i18n]').forEach(el => {
      el.textContent = i18n.t(el.dataset.i18n);
    });
    document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
      el.placeholder = i18n.t(el.dataset.i18nPlaceholder);
    });
    document.querySelectorAll('[data-i18n-title]').forEach(el => {
      el.title = i18n.t(el.dataset.i18nTitle);
    });
    document.querySelectorAll('[data-i18n-aria]').forEach(el => {
      el.setAttribute('aria-label', i18n.t(el.dataset.i18nAria));
    });
  }

  setupViewportHeight() {
    const app = document.querySelector('.app');
    const setH = () => {
      if (!window.visualViewport) return;
      app.style.height = window.visualViewport.height + 'px';
      window.scrollTo(0, 0);
    };
    window.visualViewport?.addEventListener('resize', setH);
    setH();
  }

  setupKeyboardHelpers() {
    // Ensure inputs stay visible when keyboard opens
    const modes = ['chat', 'workspace'];
    modes.forEach(mode => {
      const view = document.getElementById(`view-${mode}`);
      if (!view) return;
      const input = view.querySelector('input, textarea');
      if (input) {
        input.addEventListener('focus', () => ensureVisible(input));
      }
    });
  }

  async _initSessions() {
    try {
      await sessionManager.init();
    } catch (err) {
      console.error('Failed to init sessions:', err);
    }
  }

  _initKeyboardShortcuts() {
    // Cmd/Ctrl+,: open settings
    keyboard.register('mod+,', () => {
      this.switchMode('settings');
    });

    // Escape: close drawer/dialog
    keyboard.register('escape', () => {
      this.drawer.closeAll();
      // Also close any open dialogs
      document.querySelectorAll('dialog[open]').forEach(d => d.close());
    });
  }

  /* ── Navigazione: stack unico ──────────────────────────────────────────
     Unico punto di scrittura della history. L'URL è *derivato* dallo stato,
     mai il contrario, così una entry ripristinata da popstate riproduce
     esattamente la schermata che l'aveva spinta. */

  _navUrl(state) {
    const url = new URL(window.location);
    url.searchParams.set('mode', state.mode);
    if (state.wiki) url.searchParams.set('wiki', state.wiki);
    else url.searchParams.delete('wiki');
    if (state.page) url.searchParams.set('page', state.page);
    else url.searchParams.delete('page');
    return url;
  }

  /** Stato di navigazione per una vista (usato al boot e dai controller). */
  _navStateFor(mode, wiki, page) {
    if (mode === 'wiki' && wiki) return { mode: 'wiki', wikiPage: true, wiki, page: page || 'index.md' };
    if (mode === 'graph' && wiki) return { mode: 'graph', wiki };
    return { mode };
  }

  /** Impila una nuova schermata. Impilare due volte la stessa (ritap sullo
      stesso link, doppio tap sulla stessa voce) è la ricetta per una pressione
      di Indietro che non cambia niente: in quel caso si riscrive e basta. */
  pushNav(state) {
    const cur = history.state;
    if (cur && cur.mode === state.mode
        && (cur.wiki || null) === (state.wiki || null)
        && (cur.page || null) === (state.page || null)) {
      this.replaceNav(state);
      return;
    }
    this._navPos += 1;
    history.pushState({ ...state, pos: this._navPos }, '', this._navUrl(state));
  }

  /** Riscrive la schermata corrente senza impilarne una nuova. */
  replaceNav(state) {
    history.replaceState({ ...state, pos: this._navPos }, '', this._navUrl(state));
  }

  /* Tasto Indietro hardware (MainActivity lo inoltra sempre qui: il guscio
     nativo non ne gestisce nessun caso da solo).

     Catena di consumatori, dal livello più in alto verso il basso: il primo che
     consuma si ferma. L'invariante è che *una pressione = un cambiamento
     visibile*, altrimenti il tasto sembra saltare le schermate. */
  handleHardwareBack() {
    // 1. Top layer: <dialog> modali e sheet. Con showModal() vivono sopra tutto,
    //    quindi qualunque altra cosa facessimo resterebbe nascosta sotto.
    //    Si passa da un evento `cancel` annullabile invece di chiamare close():
    //    è la semantica di Esc, e c'è chi la rifiuta apposta (il dialog di
    //    riavvio dopo un restore non deve essere chiudibile). Consumata comunque
    //    la pressione: sotto la modale non si naviga.
    const dialogs = document.querySelectorAll('dialog[open]');
    if (dialogs.length) {
      const top = dialogs[dialogs.length - 1];
      if (top.dispatchEvent(new Event('cancel', { cancelable: true }))) top.close();
      return;
    }

    // 2. Lightbox immagini: overlay normale, si chiude col proprio handler.
    const lightbox = document.querySelector('.image-lightbox');
    if (lightbox) {
      if (typeof lightbox.__jennyClose === 'function') lightbox.__jennyClose();
      else lightbox.remove();
      return;
    }

    // 3. Mini-app aperta: gestisce la propria navigazione interna e, all'ultimo
    //    livello, si chiude. Ritorna false se non c'è nessuna app aperta.
    if (this.controllers.apps?.handleBack()) return;

    // 4. Minichat della mascotte.
    if (this.jenny?.handleBack()) return;

    // 5. Drawer aperto.
    if (this.drawer.activeDrawer) {
      this.drawer.closeAll();
      return;
    }

    // 6. Sotto-stato della sezione corrente (cartella del workspace, editor,
    //    step dell'onboarding): risalire di un livello dentro la sezione viene
    //    prima di uscirne.
    if (this.controllers[this.currentMode]?.handleBack?.()) return;

    // 7. Schermata precedente.
    if (this._navPos > 0) {
      window.history.back();
    }
    // Alla radice non c'è niente sopra di noi: non si fa niente. Questa app è
    // il launcher, quindi "indietro" non deve mai chiudere il task.
  }

  // Android Home button / home gesture. This app is the device launcher, so
  // Home means "collapse to the home screen": close any open Jenny mini-app and
  // dismiss transient UI (drawer, dialogs). A no-op when already home. Called
  // from MainActivity.onNewIntent.
  //
  // Which view counts as "home" is a preference (default chat ✿, as it always
  // was). 'last' means the user asked to be left wherever they were, so the
  // overlays close and the view stays put.
  goHome() {
    this.controllers.apps?.closeApp();
    this.drawer.closeAll();
    document.querySelectorAll('dialog[open]').forEach(d => d.close());
    const target = homeView();
    if (target !== 'last') this.switchMode(target);
  }

  // Un'app di sistema è stata installata o disinstallata (kind: 'added' |
  // 'removed'). Chiamato da MainActivity, che ascolta i broadcast del
  // PackageManager. Se la sezione App non è ancora stata aperta non c'è niente
  // da aggiornare: la lista verrà caricata fresca alla prima attivazione.
  onPackageChanged(kind, packageName) {
    this.controllers.apps?.onPackageChanged(kind, packageName);
  }

  switchMode(mode, pushState = true) {
    // Block ANY navigation if onboarding is not complete
    if (mode !== 'onboarding' && !localStorage.getItem('onboarding-complete') && this._firstRun) {
      console.warn('Onboarding not complete - redirecting to onboarding');
      this.switchMode('onboarding', pushState);
      return;
    }

    if (mode === this.currentMode) return;
    if (!this.controllerFactories[mode]) {
      console.warn(`Unknown mode: ${mode}`);
      return;
    }

    // Lazy init controller
    if (!this.controllers[mode]) {
      try {
        this.controllers[mode] = this.controllerFactories[mode]();
      } catch (err) {
        console.error(`Failed to init ${mode} controller:`, err);
        showToast(i18n.t('common.failedToLoadMode', { mode }), 'error');
        return;
      }
    }

    // Hide all views
    document.querySelectorAll('.view').forEach(v => {
      v.style.display = 'none';
    });

    // Show target view
    const view = document.getElementById(`view-${mode}`);
    if (view) {
      view.style.display = 'flex';
    }

    // Update sidebar active state. La sezione Wiki ha come dock-mode "graph"
    // (landing di default); resta attiva anche nella vista pagina ("wiki").
    document.querySelectorAll('.dock-item').forEach(item => {
      const isWikiSection = (mode === 'graph' || mode === 'wiki') && item.dataset.mode === 'graph';
      item.classList.toggle('active', item.dataset.mode === mode || isWikiSection);
    });

    // Update header
    this.header.setMode(mode);

    // Notify controllers
    if (this.controllers[this.currentMode]) {
      this.controllers[this.currentMode].deactivate();
    }
    this.currentMode = mode;
    const next = this.controllers[mode];
    if (next.ready) {
      next.ready.then(() => next.activate());
    } else {
      next.activate();
    }

    // Close any open drawer
    this.drawer.closeAll();

    // Update state and URL
    AppState.set('currentMode', mode);
    if (pushState) {
      // La vista graph ricorda il proprio wiki (activate() lo ricarica): senza
      // riportarlo nello stato, tornare qui col back mostrerebbe un grafo che
      // l'URL non descrive.
      const wiki = mode === 'graph' ? this.controllers.graph?.currentWiki : null;
      this.pushNav(wiki ? { mode, wiki } : { mode });
    }
  }

  // Ordered list of navigable modes, derived from the dock DOM order.
  // Skips hidden (onboarding when not first-run) and disabled items.
  _visibleModes() {
    return Array.from(document.querySelectorAll('.dock-item[data-mode]'))
      .filter(el => el.style.display !== 'none' && !el.classList.contains('nav-disabled'))
      .map(el => el.dataset.mode);
  }

  // Walk up from `target` to `boundary` looking for a horizontally scrollable
  // ancestor that can still scroll in the gesture direction. If found, the
  // gesture belongs to that scroller (native scroll), not to tab navigation.
  _insideHScroll(target, dx, boundary) {
    let el = target;
    while (el && el !== boundary && el !== document.body) {
      if (el.scrollWidth > el.clientWidth + 2) {
        const overflowX = getComputedStyle(el).overflowX;
        if (overflowX === 'auto' || overflowX === 'scroll') {
          const atStart = el.scrollLeft <= 0;
          const atEnd = el.scrollLeft + el.clientWidth >= el.scrollWidth - 1;
          // dx > 0 (finger right) scrolls content toward its start;
          // dx < 0 (finger left) scrolls toward its end.
          if (dx > 0 && !atStart) return true;
          if (dx < 0 && !atEnd) return true;
        }
      }
      el = el.parentElement;
    }
    return false;
  }

  // Global horizontal swipe on the content area to move between dock tabs.
  // The current view follows the finger (damped) as an affordance; on release
  // past a threshold it commits with a crisp slide-in of the target view,
  // otherwise it springs back. See plan: "carosello a step".
  setupSwipeNav() {
    const main = document.querySelector('.main');
    if (!main) return;

    // Grayout veil layered above the peeking view (see .swipe-scrim CSS).
    const scrim = document.createElement('div');
    scrim.className = 'swipe-scrim';
    scrim.setAttribute('aria-hidden', 'true');
    main.appendChild(scrim);
    const setScrim = (opacity, animate) => {
      scrim.style.transition = animate ? 'opacity .22s ease' : 'none';
      scrim.style.opacity = String(opacity);
    };

    const H_SLOP = 10;       // px of travel before deciding the gesture is horizontal
    const PEEK = 0.13;       // asymptotic peek offset toward a neighbor (fraction of width)
    const EDGE_PEEK = 0.05;  // asymptotic peek offset at the ends

    // Exponential rubber-band: responsive near 0, decelerating toward `max`.
    const rubber = (delta, max) => {
      if (!max) return 0;
      const sign = delta < 0 ? -1 : 1;
      return sign * max * (1 - Math.exp(-Math.abs(delta) / (max * 1.8)));
    };

    let startX = 0, startY = 0, startT = 0;
    let tracking = false;       // a candidate gesture is in progress
    let horizontal = null;      // null = undecided; true once committed to horizontal
    let view = null;            // the current view element being dragged
    let neighbors = null;       // { prev, next } modes
    let startTarget = null;

    const reset = () => {
      tracking = false; horizontal = null; view = null; neighbors = null; startTarget = null;
    };

    const clearView = (el) => {
      if (!el) return;
      el.style.transition = '';
      el.style.willChange = '';
      el.style.transform = '';
      el.style.filter = '';
    };

    main.addEventListener('touchstart', (e) => {
      reset();
      if (e.touches.length !== 1) return;
      // Guard: onboarding lock — navigation is blocked during first run.
      if (this._firstRun && !localStorage.getItem('onboarding-complete')) return;
      // Guard: an open drawer owns its own (vertical) swipe.
      if (this.drawer.activeDrawer) return;

      view = document.getElementById(`view-${this.currentMode}`);
      if (!view) return;

      const modes = this._visibleModes();
      const idx = modes.indexOf(this.currentMode);
      if (idx === -1) return; // e.g. graph/onboarding aren't in the dock — no swipe nav

      neighbors = {
        prev: idx > 0 ? modes[idx - 1] : null,
        next: idx < modes.length - 1 ? modes[idx + 1] : null,
      };
      const t = e.touches[0];
      startX = t.clientX; startY = t.clientY; startT = Date.now();
      startTarget = e.target;
      tracking = true;
    }, { passive: true });

    main.addEventListener('touchmove', (e) => {
      if (!tracking) return;
      const t = e.touches[0];
      const dx = t.clientX - startX;
      const dy = t.clientY - startY;

      if (horizontal === null) {
        if (Math.abs(dx) < H_SLOP && Math.abs(dy) < H_SLOP) return;
        if (Math.abs(dx) <= Math.abs(dy)) { reset(); return; } // vertical → let it scroll
        if (this._insideHScroll(startTarget, dx, main)) { reset(); return; }
        horizontal = true;
        view.style.transition = 'none';
        view.style.willChange = 'transform';
      }

      e.preventDefault(); // we own the gesture now (listener is passive:false)

      const goingPrev = dx > 0;
      const hasNeighbor = goingPrev ? neighbors.prev : neighbors.next;
      const w = main.clientWidth || window.innerWidth;
      const max = w * (hasNeighbor ? PEEK : EDGE_PEEK);
      const tx = rubber(dx, max);
      // Progress toward the asymptote drives the grayout + slight recede.
      const progress = hasNeighbor && max ? Math.min(1, Math.abs(tx) / max) : 0;
      view.style.transform = `translateX(${tx.toFixed(2)}px)`;
      setScrim(progress, false);
    }, { passive: false });

    const finish = (e) => {
      if (!tracking) return;
      const el = view;
      const nb = neighbors;
      const wasHorizontal = horizontal === true;
      if (!wasHorizontal || !el) { reset(); return; }

      const changed = (e.changedTouches && e.changedTouches[0]) || null;
      const endX = changed ? changed.clientX : startX;
      const dx = endX - startX;
      const dt = Math.max(1, Date.now() - startT);
      const vx = dx / dt; // px per ms
      const w = main.clientWidth || window.innerWidth;
      const threshold = Math.max(60, w * 0.22);
      const goingPrev = dx > 0;
      const target = goingPrev ? nb.prev : nb.next;
      const commit = !!target && (Math.abs(dx) > threshold || Math.abs(vx) > 0.5);

      reset();

      if (commit) {
        setScrim(0, false);         // new view must not inherit the veil
        clearView(el);              // old view is about to be hidden by switchMode
        this.switchMode(target);
        this._animateSlideIn(document.getElementById(`view-${target}`), goingPrev);
      } else {
        // Spring back to rest (offset and grayout fade together).
        setScrim(0, true);
        el.style.transition = 'transform .22s cubic-bezier(.22,.61,.36,1)';
        el.style.transform = 'translateX(0)';
        const onEnd = () => { clearView(el); el.removeEventListener('transitionend', onEnd); };
        el.addEventListener('transitionend', onEnd);
      }
    };

    main.addEventListener('touchend', finish, { passive: true });
    main.addEventListener('touchcancel', () => {
      if (horizontal && view) { setScrim(0, false); clearView(view); }
      reset();
    }, { passive: true });
  }

  // Slide the freshly-shown view in from the swipe direction.
  // goingPrev → came from the left; otherwise from the right.
  _animateSlideIn(view, goingPrev) {
    if (!view) return;
    const from = goingPrev ? '-100%' : '100%';
    view.style.filter = '';
    view.style.transition = 'none';
    view.style.willChange = 'transform';
    view.style.transform = `translateX(${from})`;
    void view.offsetWidth; // force reflow so the start transform sticks
    view.style.transition = 'transform .2s cubic-bezier(.22,.61,.36,1)';
    view.style.transform = 'translateX(0)';
    const onEnd = () => {
      view.style.transition = '';
      view.style.willChange = '';
      view.style.transform = '';
      view.removeEventListener('transitionend', onEnd);
    };
    view.addEventListener('transitionend', onEnd);
  }
}

// Initialize when DOM ready
document.addEventListener('DOMContentLoaded', () => {
  new MobileApp();
});
