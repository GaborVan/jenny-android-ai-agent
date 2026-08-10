/** View Title Controller — in-content view headings and actions.
 *
 * Replaces the old fixed 40px header: each view owns a `.view-title-mount`
 * (index.html) where the big scrolling-style title and its action buttons
 * are rendered. Chat and onboarding have no mount (chat renders its own
 * identity line inside the scroll area).
 */

import { i18n } from './shared/i18n.js';

export class ViewTitleController {
  constructor() {
    this.currentMode = null;
    this.titleEl = null;
    this.actionsEl = null;
    this.modeConfigs = {
      apps: {
        title: i18n.t('nav.apps'),
        actions: [
          { icon: 'ti-eye-off', title: i18n.t('header.showHiddenApps'), action: 'toggle-hidden' }
        ]
      },
      workspace: {
        title: i18n.t('nav.workspace'),
        actions: [
          { icon: 'ti-arrow-left', title: i18n.t('header.back'), action: 'ws-back', hidden: true },
          { icon: 'ti-refresh', title: i18n.t('header.refresh'), action: 'refresh' },
          { icon: 'ti-plus', title: i18n.t('header.new'), action: 'ws-new' }
        ]
      },
      wiki: {
        title: i18n.t('nav.wiki'),
        actions: [
          { icon: 'ti-clipboard-list', title: i18n.t('header.audits'), drawer: 'audit' },
          { icon: 'ti-folder', title: i18n.t('header.files'), drawer: 'files' },
          { type: 'sep' },
          { icon: 'ti-topology-star', title: i18n.t('header.graph'), action: 'graph' }
        ]
      },
      graph: {
        title: i18n.t('nav.wiki'),
        actions: [
          { icon: 'ti-file-text', title: i18n.t('header.pages'), action: 'open-pages' },
          { icon: 'ti-refresh', title: i18n.t('header.refresh'), action: 'refresh' }
        ]
      },
      settings: {
        title: i18n.t('nav.settings'),
        actions: [
          { icon: 'ti-refresh', title: i18n.t('header.refresh'), action: 'refresh' }
        ]
      }
    };

    i18n.onLocaleChange(() => this._refreshTitles());
  }

  _refreshTitles() {
    this.modeConfigs.apps.title = i18n.t('nav.apps');
    this.modeConfigs.workspace.title = i18n.t('nav.workspace');
    this.modeConfigs.wiki.title = i18n.t('nav.wiki');
    this.modeConfigs.graph.title = i18n.t('nav.wiki');
    this.modeConfigs.settings.title = i18n.t('nav.settings');
    if (this.currentMode) this.setMode(this.currentMode);
  }

  _mount(mode) {
    return document.getElementById(`title-${mode}`);
  }

  setMode(mode, customTitle = null) {
    this.currentMode = mode;
    const config = this.modeConfigs[mode];
    const mount = this._mount(mode);
    if (!config || !mount) {
      this.titleEl = null;
      this.actionsEl = null;
      return;
    }

    mount.innerHTML = '<div class="view-title">' +
      '<h1 class="view-title-text"></h1>' +
      '<div class="view-title-actions"></div>' +
      '</div>';
    this.titleEl = mount.querySelector('.view-title-text');
    this.actionsEl = mount.querySelector('.view-title-actions');

    this.titleEl.textContent = customTitle || config.title;
    this.renderActions(config.actions);
  }

  /** Scrive il titolo della vista, ma solo se chi lo scrive è ancora il
   *  proprietario della modalità corrente.
   *
   *  `titleEl` viene ripuntato soltanto da `setMode`, che `switchMode` chiama
   *  *prima* di `deactivate`/`activate`: un caricamento lento della sezione che
   *  si sta lasciando riprendeva dopo il cambio e scriveva il proprio titolo nel
   *  mount della sezione di **destinazione**. Il difetto è intermittente — chat
   *  e onboarding non hanno mount, quindi lì `titleEl` è null e non si vede
   *  niente — e per questo era rimasto invisibile.
   *
   *  `ownerMode` è opzionale solo per non rompere chiamanti futuri distratti:
   *  chi scrive un titolo asincrono deve passarlo.
   */
  setTitle(title, ownerMode = null) {
    if (ownerMode && ownerMode !== this.currentMode) return;
    if (this.titleEl) this.titleEl.textContent = title;
  }

  showAction(actionName) {
    const btn = this.actionsEl?.querySelector(`[data-action="${actionName}"]`);
    if (btn) btn.style.display = '';
  }

  hideAction(actionName) {
    const btn = this.actionsEl?.querySelector(`[data-action="${actionName}"]`);
    if (btn) btn.style.display = 'none';
  }

  renderActions(actions) {
    if (!this.actionsEl) return;
    const html = actions.map(action => {
      if (action.type === 'sep') {
        return '<div class="sep"></div>';
      }
      if (action.drawer) {
        const hiddenStyle = action.hidden ? ' style="display:none"' : '';
        return `<button class="ibtn ibtn-drawer" data-drawer="${action.drawer}" title="${action.title}"${hiddenStyle}>
          <i class="ti ${action.icon}"></i>
        </button>`;
      }
      const dangerClass = action.danger ? ' ibtn-danger' : '';
      const hiddenStyle = action.hidden ? ' style="display:none"' : '';
      return `<button class="ibtn ibtn-action${dangerClass}" data-action="${action.action}" title="${action.title}"${hiddenStyle}>
        <i class="ti ${action.icon}"></i>
      </button>`;
    }).join('');

    this.actionsEl.innerHTML = html;
    this.wireActions();
  }

  wireActions() {
    if (!this.actionsEl) return;
    this.actionsEl.querySelectorAll('[data-drawer]').forEach(btn => {
      btn.addEventListener('click', () => {
        const drawerId = btn.dataset.drawer;
        window.mobileApp.drawer.toggle(drawerId);
        this.syncDrawerTabs();
      });
    });

    this.actionsEl.querySelectorAll('[data-action]').forEach(btn => {
      btn.addEventListener('click', () => {
        const action = btn.dataset.action;
        this.handleAction(action);
      });
    });
  }

  syncDrawerTabs() {
    if (!this.actionsEl) return;
    const activeDrawer = window.mobileApp.drawer.activeDrawer;
    this.actionsEl.querySelectorAll('[data-drawer]').forEach(btn => {
      const isActive = btn.dataset.drawer === activeDrawer;
      btn.classList.toggle('active-tab', isActive);
      const icon = btn.querySelector('i');
      if (icon && btn.dataset.drawer === 'files') {
        icon.classList.toggle('ti-folder', !isActive);
        icon.classList.toggle('ti-folder-open', isActive);
      }
    });
  }

  handleAction(action) {
    const app = window.mobileApp;
    if (action === 'graph') {
      if (app.currentMode === 'graph') {
        const wiki = app.controllers.graph?.currentWiki;
        if (wiki) {
          const lastPage = app.controllers.wiki?.lastWikiPage?.[wiki];
          app.switchMode('wiki', false);
          app.controllers.wiki.loadWikiPage(wiki, lastPage || 'index.md', true);
        } else {
          app.switchMode('wiki', false);
          app.controllers.wiki.loadHome(true);
        }
      } else {
        // Sorgente unica: la vista voluta si deposita e la carica
        // `GraphController.activate()`. Prima qui c'era
        // `switchMode('graph'); loadGraph(...)`, e activate() aveva già
        // caricato per conto suo — due fetch e due settleSimulation sincroni.
        const wiki = app.controllers.wiki?.currentWiki;
        app.requestGraph(wiki || null, true);
      }
      return;
    }
    if (action === 'open-pages') {
      // Apre la vista file/pagina dalla vista grafo (landing di default).
      const wiki = app.controllers.graph?.currentWiki;
      if (wiki && wiki !== '_home') {
        const lastPage = app.controllers.wiki?.lastWikiPage?.[wiki];
        app.switchMode('wiki', false);
        app.controllers.wiki.loadWikiPage(wiki, lastPage || 'index.md', true);
      } else {
        app.switchMode('wiki', false);
        app.controllers.wiki.loadHome(true);
      }
      return;
    }
    const controller = app.controllers[app.currentMode];
    if (controller && controller.handleAction) {
      controller.handleAction(action);
    }
  }
}
