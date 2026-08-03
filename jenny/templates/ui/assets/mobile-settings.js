/** Mobile Settings Controller — accordion-based settings panel. */

import { api } from './shared/api-client.js';
import { escapeHtml, showToast } from './shared/utils.js';
import { i18n } from './shared/i18n.js';
import { AppState } from './shared/state.js';
import { confirmDialog } from './shared/dialog.js';
import { THEMES, DEFAULT_THEME, setTheme } from './shared/theme.js';
import { advancedMode, setAdvancedMode } from './shared/advanced-mode.js';
import { mascotVisible, setMascotVisible, mascotSide, setMascotSide,
  mascotColor, setMascotColor, mascotSize, setMascotSize,
  MASCOT_SIZES } from './shared/mascot.js';
import { homeView, setHomeView, HOME_VIEW_CHOICES } from './shared/home-view.js';
import { TelegramPairingWidget } from './shared/telegram-pairing.js';
import {
  runExportFlow,
  runImportFlow,
  runSnapshotRestore,
} from './shared/backup-flow.js';

export class SettingsController {
  constructor() {
    this.contentEl = document.getElementById('settings-content');
    this.loadingEl = document.getElementById('settings-loading');
    this.data = null;
    this._debounceTimers = {};
    // Sezioni aperte, per id: sopravvive ai re-render (che ricostruiscono
    // tutto l'HTML), non alla navigazione — niente localStorage di proposito.
    this._openSections = new Set();
    this.loadSettings();
  }

  showLoading() { this.loadingEl?.classList.add('active'); }
  hideLoading() { this.loadingEl?.classList.remove('active'); }

  async loadSettings() {
    this.showLoading();
    try {
      const settings = await api.getSettings();
      this.data = settings;
      this.render();
    } catch (err) {
      this.contentEl.innerHTML = `
        <div class="settings-error">
          <i class="ti ti-cloud-off" style="font-size:32px;color:var(--text-faint)"></i>
           <p>${i18n.t('settings.failedToLoad')}</p>
          <p style="font-size:11px;color:var(--text-faint)">${escapeHtml(err.message)}</p>
        </div>`;
    }
    this.hideLoading();
  }

  activate() { this.loadSettings(); }
  deactivate() {
    if (this._tgWidget) {
      this._tgWidget.destroy();
      this._tgWidget = null;
    }
  }

  handleAction(action) {
    if (action === 'refresh') this.loadSettings();
  }

  // ── Rendering ──────────────────────────────────────────────────────

  render() {
    const d = this.data;
    if (!d) return;

    // Sei sezioni tematiche, una per asse mentale: preferenze d'interfaccia,
    // motore LLM, capacità dell'agente, canali, dati, diagnostica.
    this.contentEl.innerHTML = [
      this._renderConfigRecovery(d),
      this._section('personalization', 'ti-palette', i18n.t('settings.personalization'), this._renderPersonalization(d)),
      this._section('models', 'ti-cpu', i18n.t('settings.model'), this._renderModelSettings(d)),
      this._section('tools', 'ti-tool', i18n.t('settings.tools'), this._renderTools(d)),
      this._section('telegram', 'ti-brand-telegram', i18n.t('settings.telegram.title'), this._renderTelegram()),
      this._section('backup', 'ti-database-export', i18n.t('backup.sectionTitle'), this._renderBackup()),
      this._section('system', 'ti-info-circle', i18n.t('settings.system'), this._renderSystem(d)),
    ].join('');

    this._wireSections();
  }

  /* Avviso di config recuperata all'avvio. Silenzioso nel caso normale: se
     compare, l'utente sta usando impostazioni che non sono quelle che aveva
     scelto — e con restored_from = "defaults" deve rimettere anche la chiave
     API. Farglielo scoprire da solo sarebbe la sorpresa peggiore. */
  _renderConfigRecovery(d) {
    const info = d.config_recovery;
    if (!info) return '';
    const fromDefaults = info.restored_from === 'defaults';
    const text = fromDefaults
      ? i18n.t('settings.configRecoveredDefaults')
      : i18n.t('settings.configRecoveredBackup');
    const where = info.broken_file
      ? `<div class="settings-notice-path">${escapeHtml(info.broken_file)}</div>`
      : '';
    return `<div class="settings-notice${fromDefaults ? ' settings-notice-strong' : ''}">
      <i class="ti ti-alert-triangle"></i>
      <div>
        <div>${text}</div>
        ${where}
      </div>
    </div>`;
  }

  _section(id, icon, title, body) {
    const collapsed = this._openSections.has(id) ? '' : ' collapsed';
    return `<div class="settings-section${collapsed}" data-section="${id}">
      <div class="settings-section-header">
        <i class="ti ${icon}"></i>
        <span>${title}</span>
        <i class="ti ti-chevron-down settings-chevron"></i>
      </div>
      <div class="settings-section-body">${body}</div>
    </div>`;
  }

  // ── Personalizzazione ──────────────────────────────────────────────

  /* Tutte le preferenze "come appare e come parla l'interfaccia": temi,
     mascotte, nome del bot e lingua vivono qui, sullo stesso asse. */
  _renderPersonalization(d) {
    const a = d.agent || {};
    return `
      ${this._renderTheme()}
      ${this._renderHomeView()}
      <div class="theme-strip-eyebrow">${i18n.t('settings.botName')}</div>
      <div class="settings-field">
        <input type="text" class="settings-input" data-key="bot_name" value="${escapeHtml(a.bot_name || '')}" />
      </div>
      <div class="theme-strip-eyebrow">${i18n.t('settings.language')}</div>
      ${this._renderLanguage()}`;
  }

  // ── Telegram ───────────────────────────────────────────────────────

  _renderTelegram() {
    // Il contenuto vero lo disegna il TelegramPairingWidget (condiviso con
    // l'onboarding) dentro questo placeholder, in _wireSections.
    return `<div id="settings-telegram-widget"></div>`;
  }

  // ── Models & Providers ─────────────────────────────────────────────

  _formatLabel(fmt) {
    return {
      'openai_compat': i18n.t('provider.openai'),
      'anthropic': i18n.t('provider.anthropic'),
    }[fmt] || fmt || i18n.t('provider.unknown');
  }

  /* Gerarchia a decisione unica: la card "In uso" mostra modello e provider
     correnti; il catalogo unificato (raggruppato per provider) salva
     modello + default_provider insieme, in una chiamata sola. Le chiavi API
     sono pura gestione credenziali (nessuno stato "attivo" da leggere lì);
     i parametri di generazione stanno in una disclosure chiusa. */
  _renderModelSettings(d) {
    const a = d.agent || {};
    const providers = d.providers || [];
    const active = providers.find(p => p.name === d.default_provider);
    const via = active
      ? `${i18n.t('settings.via')} ${escapeHtml(active.name)} · ${escapeHtml(this._formatLabel(active.format))}`
      : i18n.t('settings.noProviderConfigured');

    return `
      <div class="settings-subheading">${i18n.t('settings.inUse')}</div>
      <div class="model-inuse">
        <span class="model-inuse-name">${escapeHtml(a.model || '—')}</span>
        <span class="model-inuse-via">${via}</span>
        <button class="settings-btn-save model-change-btn" id="btn-change-model">${i18n.t('settings.changeModel')}</button>
      </div>
      <div class="model-catalog" id="model-catalog" style="display:none">
        <input type="text" class="settings-input" id="model-search" placeholder="${i18n.t('settings.filterModels')}" autocomplete="off" />
        <div id="model-catalog-groups"></div>
      </div>
      <div class="settings-divider"></div>
      <div class="settings-subheading">${i18n.t('settings.apiKeys')}</div>
      <div id="provider-list">
        ${this._renderProviderListHtml(providers)}
      </div>
      <button class="settings-btn-add" id="btn-add-provider"><i class="ti ti-plus"></i> ${i18n.t('settings.addProvider')}</button>
      <details class="settings-disclosure">
        <summary>${i18n.t('settings.advancedParams')}</summary>
        ${this._field(i18n.t('settings.maxTokens'), 'number', 'max_tokens', a.max_tokens || '', i18n.t('settings.maxTokensPlaceholder'))}
        ${this._field(i18n.t('settings.temperature'), 'number', 'temperature', a.temperature ?? '', i18n.t('settings.temperaturePlaceholder'))}
        ${this._select(i18n.t('settings.reasoningEffort'), 'reasoning_effort', a.reasoning_effort || '',
          ['', 'low', 'medium', 'high'])}
      </details>`;
  }

  _renderProviderListHtml(providers) {
    if (!providers.length) return `<div class="settings-empty-state">${i18n.t('settings.noProviders')}</div>`;
    return providers.map(p => {
      return `<div class="provider-card" data-provider="${escapeHtml(p.name)}">
        <div class="provider-card-header">
          <span class="provider-name">${escapeHtml(p.name)}</span>
          <span class="provider-badge format-badge">${escapeHtml(this._formatLabel(p.format))}</span>
        </div>
        <div class="provider-card-body">
          <span class="provider-url">${escapeHtml(p.api_base || i18n.t('settings.defaultUrl'))}</span>
          <span class="provider-key">${escapeHtml(p.api_key_hint || i18n.t('settings.noKey'))}</span>
        </div>
        <div class="provider-card-actions">
          <button class="btn-icon provider-edit" data-provider="${escapeHtml(p.name)}" title="${i18n.t('settings.edit')}">
            <i class="ti ti-edit"></i>
          </button>
          <button class="btn-icon btn-danger provider-delete" data-provider="${escapeHtml(p.name)}" title="${i18n.t('settings.delete')}">
            <i class="ti ti-trash"></i>
          </button>
        </div>
      </div>`;
    }).join('');
  }

  // ── Strumenti ──────────────────────────────────────────────────────

  /* Le capacità dell'agente (ricerca web, posizione; i prossimi tool
     finiranno qui). I campi salvano da soli al cambio, come il resto. */
  _renderTools(d) {
    const ws = d.web_search || {};
    const engines = ws.engines || ['bing'];
    return `
      <div class="settings-subheading">${i18n.t('settings.webSearch')}</div>
      ${this._select(i18n.t('settings.searchEngine'), 'ws_engine', ws.search_engine || 'bing', engines)}
      ${this._field(i18n.t('settings.maxResults'), 'number', 'ws_max', ws.max_results ?? 5, i18n.t('settings.maxResultsPlaceholder'))}
      ${this._field(i18n.t('settings.timeoutSec'), 'number', 'ws_timeout', ws.timeout ?? 30, i18n.t('settings.timeoutPlaceholder'))}
      ${this._field(i18n.t('settings.fetchMaxChars'), 'number', 'ws_fetch_max', ws.fetch_max_chars ?? 50000, i18n.t('settings.fetchMaxCharsPlaceholder'))}
      <div class="settings-divider"></div>
      <div class="settings-subheading">${i18n.t('settings.location.section')}</div>
      ${this._renderLocation(d)}`;
  }

  _renderLocation(d) {
    const loc = d.location || {};
    return `
      <div class="settings-field settings-toggle-row">
        <label class="settings-label">${i18n.t('settings.location.enable')}</label>
        <label class="toggle-switch">
          <input type="checkbox" id="location-enabled-toggle" ${loc.enabled ? 'checked' : ''}>
          <span class="toggle-slider"></span>
        </label>
      </div>
      <p class="settings-hint" style="margin:6px 0 0;font-size:12px;color:var(--text-faint)">${i18n.t('settings.location.hint')}</p>`;
  }

  // ── Theme ──────────────────────────────────────────────────────────

  _renderTheme() {
    const current = AppState.theme || localStorage.getItem('tc-theme') || DEFAULT_THEME;
    // Each card is dressed in its own theme (self-contained `.tk-<id>` styles)
    // and *is* the preview — a mini-conversation + input, not just a swatch.
    const cards = THEMES.map(t => {
      const sel = t.id === current;
      return `<button class="tcard tk-${t.id}${sel ? ' sel' : ''}" data-theme-choice="${t.id}" title="${escapeHtml(t.label)}">
        ${sel ? '<span class="tsel">✓</span>' : ''}
        <div class="thead"><span class="tnm">${escapeHtml(t.label)}</span><span class="tfl">✿</span></div>
        <div class="tconv">
          <div class="tblo">${escapeHtml(i18n.t('themes.' + t.id + '.desc'))}</div>
          <div class="trep">${escapeHtml(i18n.t('themes.' + t.id + '.reply'))}</div>
          <div class="tmeta">0.8s</div>
        </div>
        <div class="tfoot"><span class="tin">${i18n.t('themes.placeholder')}</span><span class="tsend">↑</span></div>
      </button>`;
    }).join('');
    return `<div class="theme-strip-eyebrow">${i18n.t('settings.themeLabel')}</div>
      <div class="tstrip">${cards}</div>
      ${this._renderMascot()}`;
  }

  // ── Mascotte ───────────────────────────────────────────────────────

  /* Blocco della sezione "Personalizzazione", sotto la passerella dei temi:
     mini-label, toggle di visibilità, taglia, lato e variante colore.
     Le opzioni restano SEMPRE a schermo: nasconderle a mascotte spenta faceva
     sembrare che l'unica scelta fosse tenerla o buttarla via — chi la spegneva
     subito non scopriva mai che era personalizzabile. Da spenta si vedono
     inerti (attributo `disabled`), come promessa di cosa si ottiene
     riaccendendola. */
  _renderMascot() {
    const visible = mascotVisible();
    const side = mascotSide();
    const color = mascotColor();
    const size = mascotSize();
    const off = visible ? '' : ' disabled';
    const sizeLabels = {
      sm: i18n.t('settings.mascotSizeSmall'),
      md: i18n.t('settings.mascotSizeMedium'),
      lg: i18n.t('settings.mascotSizeLarge'),
    };
    const sizeButtons = Object.keys(MASCOT_SIZES).map(id =>
      `<button class="settings-seg-btn${id === size ? ' active' : ''}" data-mascot-size="${id}"${off}>
        ${escapeHtml(sizeLabels[id])}
        ${id === size ? '<i class="ti ti-check"></i>' : ''}
      </button>`
    ).join('');
    return `
      <div class="theme-strip-eyebrow">${i18n.t('settings.mascotSection')}</div>
      <div class="settings-field settings-toggle-row">
        <label class="settings-label">${i18n.t('settings.mascotVisible')}</label>
        <label class="toggle-switch">
          <input type="checkbox" id="mascot-visible-toggle" ${visible ? 'checked' : ''}>
          <span class="toggle-slider"></span>
        </label>
      </div>
      <div class="settings-field"${visible ? '' : ' data-settings-off'}>
        <label class="settings-label">${i18n.t('settings.mascotSize')}</label>
        <div class="settings-seg">${sizeButtons}</div>
      </div>
      <div class="settings-field"${visible ? '' : ' data-settings-off'}>
        <label class="settings-label">${i18n.t('settings.mascotSide')}</label>
        <div class="settings-seg">
          <button class="settings-seg-btn${side === 'left' ? ' active' : ''}" data-mascot-side="left"${off}>
            ${i18n.t('settings.mascotSideLeft')}
            ${side === 'left' ? '<i class="ti ti-check"></i>' : ''}
          </button>
          <button class="settings-seg-btn${side === 'right' ? ' active' : ''}" data-mascot-side="right"${off}>
            ${i18n.t('settings.mascotSideRight')}
            ${side === 'right' ? '<i class="ti ti-check"></i>' : ''}
          </button>
        </div>
      </div>
      <div class="settings-field settings-toggle-row"${visible ? '' : ' data-settings-off'}>
        <label class="settings-label">${i18n.t('settings.mascotColor')}</label>
        <label class="toggle-switch">
          <input type="checkbox" id="mascot-color-toggle" ${color ? 'checked' : ''}${off}>
          <span class="toggle-slider"></span>
        </label>
      </div>`;
  }

  // ── Tasto Home ─────────────────────────────────────────────────────

  /* Da launcher, Home significa "torna alla schermata iniziale": qui si sceglie
     quale sia. Select e non segmented: quattro voci non stanno in riga su un
     telefono. Le etichette delle viste sono quelle del dock (nav.*), così
     restano allineate a quello che si vede nella barra. */
  _renderHomeView() {
    const current = homeView();
    const labels = {
      chat: i18n.t('nav.chat'),
      apps: i18n.t('nav.apps'),
      workspace: i18n.t('nav.workspace'),
      last: i18n.t('settings.homeLast'),
    };
    const options = HOME_VIEW_CHOICES.map(id =>
      `<option value="${id}"${id === current ? ' selected' : ''}>${escapeHtml(labels[id])}</option>`
    ).join('');
    return `
      <div class="theme-strip-eyebrow">${i18n.t('settings.homeSection')}</div>
      <div class="settings-field">
        <select class="settings-select" id="home-view-select">${options}</select>
        <p class="settings-hint" style="margin:6px 0 0;font-size:12px;color:var(--text-faint)">${i18n.t('settings.homeHint')}</p>
      </div>`;
  }

  // ── Language ───────────────────────────────────────────────────────

  _renderLanguage() {
    const current = i18n.locale;
    let html = '<div class="settings-language-list">';
    for (const locale of i18n.availableLocales) {
      const isActive = locale === current;
      html += `<button class="settings-seg-btn${isActive ? ' active' : ''}" data-locale="${locale}">
        ${i18n.getLocaleName(locale)}
        ${isActive ? '<i class="ti ti-check"></i>' : ''}
      </button>`;
    }
    html += '</div>';
    return html;
  }

  // ── Backup e ripristino ──────────────────────────────────────────────

  _renderBackup() {
    return `
      <p class="settings-hint" style="margin:0 0 10px;font-size:12px;color:var(--text-faint)">${i18n.t('backup.exportDesc')}</p>
      <button class="settings-btn-save settings-btn-block" id="btn-backup-export"><i class="ti ti-file-export"></i> ${i18n.t('backup.exportButton')}</button>
      <div class="settings-divider"></div>
      <p class="settings-hint" style="margin:0 0 10px;font-size:12px;color:var(--text-faint)">${i18n.t('backup.importDesc')}</p>
      <button class="settings-btn-add" id="btn-backup-import"><i class="ti ti-file-import"></i> ${i18n.t('backup.importButton')}</button>
      <div class="settings-divider"></div>
      <div class="settings-subheading">${i18n.t('backup.snapshotHistory')}</div>
      <div class="settings-field">
        <label class="settings-label">${i18n.t('backup.retentionLabel')}</label>
        <select class="settings-select" id="snapshot-retention">
          <option value="7">${i18n.t('backup.retentionWeek')}</option>
          <option value="30">${i18n.t('backup.retentionMonth')}</option>
          <option value="365">${i18n.t('backup.retentionYear')}</option>
          <option value="0">${i18n.t('backup.retentionForever')}</option>
        </select>
      </div>
      <button class="settings-btn-add" id="btn-snapshot-create"><i class="ti ti-camera"></i> ${i18n.t('backup.snapshotCreate')}</button>
      <div id="snapshot-list" style="margin-top:8px">
        <div class="settings-empty-state">${i18n.t('settings.loading')}</div>
      </div>`;
  }

  // ── Sistema ────────────────────────────────────────────────────────

  /* Diagnostica e opzioni da power user: versione, modalità avanzata,
     statistiche di utilizzo token. */
  _renderSystem(d) {
    const v = d.version || {};
    return `
      <div class="settings-field-row">
        <span class="settings-field-label">${i18n.t('settings.version')}</span>
        <span class="settings-field-value">${escapeHtml(v.current || '—')}</span>
      </div>
      <div class="settings-divider"></div>
      <div class="settings-field settings-toggle-row">
        <label class="settings-label">${i18n.t('settings.advancedMode')}</label>
        <label class="toggle-switch">
          <input type="checkbox" id="advanced-mode-toggle" ${advancedMode() ? 'checked' : ''}>
          <span class="toggle-slider"></span>
        </label>
      </div>
      <p class="settings-hint" style="margin-top:6px;font-size:12px;color:var(--text-faint)">${i18n.t('settings.advancedModeHint')}</p>
      <div class="settings-divider"></div>
      <div class="settings-subheading">${i18n.t('settings.tokenUsage')}</div>
      ${this._renderUsage(d)}`;
  }

  _wireBackup() {
    this._wireBtn('btn-backup-export', () => runExportFlow());
    this._wireBtn('btn-backup-import', async () => {
      if (await confirmDialog(i18n.t('backup.importConfirm'))) runImportFlow();
    });
    this._wireBtn('btn-snapshot-create', async () => {
      try {
        const res = await api.createSnapshot();
        showToast(res.snapshot
          ? i18n.t('backup.snapshotCreated')
          : i18n.t('backup.snapshotNoChanges'));
        this._loadSnapshotList();
      } catch (e) { showToast(e.message, 'error'); }
    });
    const retentionEl = this.contentEl.querySelector('#snapshot-retention');
    if (retentionEl) {
      retentionEl.addEventListener('change', async () => {
        try {
          await api.updateSnapshotRetention(parseInt(retentionEl.value, 10));
          showToast(i18n.t('settings.saved'));
          this._loadSnapshotList();
        } catch (e) { showToast(e.message, 'error'); }
      });
    }
    this._loadSnapshotList();
  }

  /** Allinea la select al valore corrente; un valore fuori preset (config
   *  editata a mano) diventa un'opzione dedicata invece di mostrarne una falsa. */
  _syncRetentionSelect(days) {
    const el = this.contentEl.querySelector('#snapshot-retention');
    if (el == null || days == null) return;
    const value = String(days);
    if (![...el.options].some(o => o.value === value)) {
      const opt = document.createElement('option');
      opt.value = value;
      opt.textContent = i18n.t('backup.retentionDays', { days: value });
      el.appendChild(opt);
    }
    el.value = value;
  }

  async _loadSnapshotList() {
    const listEl = this.contentEl.querySelector('#snapshot-list');
    if (!listEl) return;
    let snapshots = [];
    try {
      const history = await api.getSnapshotHistory();
      snapshots = history.snapshots || [];
      this._syncRetentionSelect(history.retention_max_age_days);
    } catch {
      listEl.innerHTML = `<div class="settings-empty-state">${i18n.t('backup.snapshotHistoryUnavailable')}</div>`;
      return;
    }
    if (!snapshots.length) {
      listEl.innerHTML = `<div class="settings-empty-state">${i18n.t('backup.snapshotHistoryEmpty')}</div>`;
      return;
    }
    listEl.innerHTML = snapshots.map(s => {
      const date = new Date(s.created_at_ms).toLocaleString(i18n.locale);
      const triggerKey = `backup.trigger.${s.trigger}`;
      let trigger = i18n.t(triggerKey);
      if (trigger === triggerKey) trigger = s.trigger;
      const label = s.label ? ` · ${escapeHtml(s.label)}` : '';
      return `<button class="snapshot-row" data-snapshot="${escapeHtml(s.id)}" data-date="${escapeHtml(date)}"
        style="display:flex;flex-direction:column;align-items:flex-start;width:100%;gap:2px;padding:8px 10px;margin-bottom:6px;border:1px solid var(--border,rgba(128,128,128,.25));border-radius:8px;background:transparent;color:inherit;text-align:left">
        <span style="font-size:13px">${escapeHtml(date)}${label}</span>
        <span style="font-size:11px;color:var(--text-faint)">${escapeHtml(trigger)} · ${s.file_count} ${i18n.t('backup.files')} · ${this._fmtBytes(s.total_bytes)}</span>
      </button>`;
    }).join('');
    listEl.querySelectorAll('.snapshot-row').forEach(btn => {
      btn.addEventListener('click', async () => {
        const ok = await confirmDialog(
          i18n.t('backup.snapshotRestoreConfirm', { date: btn.dataset.date }));
        if (ok) runSnapshotRestore(btn.dataset.snapshot);
      });
    });
  }

  _fmtBytes(n) {
    if (n == null) return '—';
    if (n >= 1e6) return (n / 1e6).toFixed(1) + ' MB';
    if (n >= 1e3) return (n / 1e3).toFixed(1) + ' KB';
    return n + ' B';
  }

  // ── Token Usage ──────────────────────────────────────────────────────

  _renderUsage(d) {
    const u = d.usage || {};
    if (u.total_tokens == null) return `<div class="settings-empty">${i18n.t('settings.usage.noData')}</div>`;
    return `
      <div class="settings-usage-grid">
        <div class="settings-usage-stat">
          <div class="settings-usage-value">${this._fmtNum(u.total_tokens)}</div>
          <div class="settings-usage-label">${i18n.t('settings.usage.totalTokens')}</div>
        </div>
        <div class="settings-usage-stat">
          <div class="settings-usage-value">${this._fmtNum(u.total_tokens_30d)}</div>
          <div class="settings-usage-label">${i18n.t('settings.usage.last30d')}</div>
        </div>
        <div class="settings-usage-stat">
          <div class="settings-usage-value">${this._fmtNum(u.total_tokens_365d)}</div>
          <div class="settings-usage-label">${i18n.t('settings.usage.last365d')}</div>
        </div>
        <div class="settings-usage-stat">
          <div class="settings-usage-value">${this._fmtNum(u.peak_day_tokens)}</div>
          <div class="settings-usage-label">${i18n.t('settings.usage.peakDay')}</div>
        </div>
        <div class="settings-usage-stat">
          <div class="settings-usage-value">${u.current_streak_days || 0}d</div>
          <div class="settings-usage-label">${i18n.t('settings.usage.streak')}</div>
        </div>
        <div class="settings-usage-stat">
          <div class="settings-usage-value">${u.active_days_30d || 0}</div>
          <div class="settings-usage-label">${i18n.t('settings.usage.activeDays')}</div>
        </div>
        <div class="settings-usage-stat">
          <div class="settings-usage-value">${u.requests_30d || 0}</div>
          <div class="settings-usage-label">${i18n.t('settings.usage.requests')}</div>
        </div>
      </div>`;
  }

  // ── Form Helpers ───────────────────────────────────────────────────

  _field(label, type, key, value, placeholder = '') {
    return `<div class="settings-field">
      <label class="settings-label">${label}</label>
      <input type="${type}" class="settings-input" data-key="${key}" value="${escapeHtml(String(value))}"
        placeholder="${escapeHtml(placeholder)}">
    </div>`;
  }

  _select(label, key, value, options) {
    const opts = options.map(o =>
      `<option value="${escapeHtml(o)}" ${o === value ? 'selected' : ''}>${o || '—'}</option>`
    ).join('');
    return `<div class="settings-field">
      <label class="settings-label">${label}</label>
      <select class="settings-select" data-key="${key}">${opts}</select>
    </div>`;
  }

  // ── Wire Events ────────────────────────────────────────────────────

  _wireSections() {
    // Accordion toggle (lo stato aperto va in _openSections così i
    // re-render non richiudono la sezione in cui l'utente sta lavorando)
    this.contentEl.querySelectorAll('.settings-section-header').forEach(h => {
      h.addEventListener('click', () => {
        const sec = h.closest('.settings-section');
        const collapsed = sec.classList.toggle('collapsed');
        if (collapsed) this._openSections.delete(sec.dataset.section);
        else this._openSections.add(sec.dataset.section);
      });
    });

    // Active config fields → auto-save on change
    for (const key of ['bot_name', 'max_tokens', 'temperature', 'reasoning_effort']) {
      const el = this.contentEl.querySelector(`[data-key="${key}"]`);
      if (!el) continue;
      el.addEventListener('change', () => this._debouncedSave(key, el.value));
    }

    // Telegram: widget condiviso con lo step di onboarding
    const tgContainer = this.contentEl.querySelector('#settings-telegram-widget');
    if (tgContainer) {
      if (this._tgWidget) this._tgWidget.destroy();
      this._tgWidget = new TelegramPairingWidget(tgContainer, { mode: 'settings' });
      this._tgWidget.refresh();
    }

    // Catalogo modelli unificato
    this._wireBtn('btn-change-model', () => this._toggleModelCatalog());
    const modelSearch = this.contentEl.querySelector('#model-search');
    if (modelSearch) {
      modelSearch.addEventListener('input', () => this._applyCatalogFilter());
    }

    // Provider edit/delete buttons
    this.contentEl.querySelectorAll('.provider-edit').forEach(btn => {
      btn.addEventListener('click', () => this._editProvider(btn.dataset.provider));
    });
    this.contentEl.querySelectorAll('.provider-delete').forEach(btn => {
      btn.addEventListener('click', () => this._deleteProvider(btn.dataset.provider));
    });

    // Ricerca web → auto-save con debounce (payload completo, come il
    // bottone Salva che sostituisce)
    for (const key of ['ws_engine', 'ws_max', 'ws_timeout', 'ws_fetch_max']) {
      const el = this.contentEl.querySelector(`[data-key="${key}"]`);
      if (!el) continue;
      el.addEventListener('change', () => {
        clearTimeout(this._debounceTimers.web_search);
        this._debounceTimers.web_search = setTimeout(() => this._saveWebSearch(), 600);
      });
    }

    // Posizione: toggle auto-applicato al cambio (nessun bottone salva).
    const locToggle = this.contentEl.querySelector('#location-enabled-toggle');
    if (locToggle) {
      locToggle.addEventListener('change', () => {
        const enabled = locToggle.checked;
        api.updateLocation({ enabled: enabled ? '1' : '0' })
          .then(() => {
            if (this.data && this.data.location) this.data.location.enabled = enabled;
            showToast(i18n.t(enabled ? 'settings.location.on' : 'settings.location.off'));
          })
          .catch(() => {
            locToggle.checked = !enabled;  // rollback sull'errore
            showToast(i18n.t('settings.saveError'));
          });
      });
    }

    // Add provider
    this._wireBtn('btn-add-provider', () => this._showAddProviderDialog());

    // Backup e ripristino
    this._wireBackup();

    // Modalità avanzata
    const advToggle = this.contentEl.querySelector('#advanced-mode-toggle');
    if (advToggle) advToggle.addEventListener('change', () => setAdvancedMode(advToggle.checked));

    // Mascotte: toggle visibilità (re-render per mostrare/nascondere il
    // blocco scelta lato) + scelta del lato
    const mascotToggle = this.contentEl.querySelector('#mascot-visible-toggle');
    if (mascotToggle) {
      mascotToggle.addEventListener('change', () => {
        setMascotVisible(mascotToggle.checked);
        this.render();
      });
    }
    this.contentEl.querySelectorAll('[data-mascot-side]').forEach(btn => {
      btn.addEventListener('click', () => {
        setMascotSide(btn.dataset.mascotSide);
        this.render();
      });
    });
    this.contentEl.querySelectorAll('[data-mascot-size]').forEach(btn => {
      btn.addEventListener('click', () => {
        setMascotSize(btn.dataset.mascotSize);
        this.render();
      });
    });
    // Mascotte: variante colore <-> bianco/nero (la companion ri-risolve le
    // pose via l'evento 'mascotchange', v. shared/mascot.js)
    const mascotColorToggle = this.contentEl.querySelector('#mascot-color-toggle');
    if (mascotColorToggle) {
      mascotColorToggle.addEventListener('change', () => setMascotColor(mascotColorToggle.checked));
    }

    // Tasto Home: nessun re-render, il valore serve solo a goHome()
    const homeSelect = this.contentEl.querySelector('#home-view-select');
    if (homeSelect) {
      homeSelect.addEventListener('change', () => setHomeView(homeSelect.value));
    }

    // Theme selector — tap a card to switch theme
    this.contentEl.querySelectorAll('.tcard[data-theme-choice]').forEach(btn => {
      btn.addEventListener('click', () => {
        const theme = setTheme(btn.dataset.themeChoice);
        this.render();
        showToast(theme.label);
      });
    });

    // Language selector — solo i bottoni seg con data-locale (quelli del
    // lato mascotte condividono la classe ma hanno data-mascot-side).
    this.contentEl.querySelectorAll('.settings-seg-btn[data-locale]').forEach(btn => {
      btn.addEventListener('click', () => {
        const locale = btn.dataset.locale;
        i18n.setLocale(locale).then(() => {
          this.render();
          showToast(i18n.t('settings.saved'));
        });
      });
    });
  }

  _wireBtn(id, fn) {
    const btn = this.contentEl.querySelector(`#${id}`);
    if (btn) btn.addEventListener('click', fn);
  }

  // ── Save Handlers ──────────────────────────────────────────────────

  _debouncedSave(key, value) {
    clearTimeout(this._debounceTimers[key]);
    this._debounceTimers[key] = setTimeout(async () => {
      try {
        await api.updateSettings({ [key]: value });
        showToast(i18n.t('settings.saved'));
      } catch (e) { showToast(e.message, 'error'); }
    }, 600);
  }

  /* keepStoredKey: il provider ha già una chiave salvata, quindi un campo
     vuoto significa "lasciala com'è" e non va segnalato come errore. */
  _saveProvider(name, format, apiKey, apiBase, { keepStoredKey = false } = {}) {
    if (!name || (!apiKey && !keepStoredKey)) {
      showToast(i18n.t('settings.nameAndKeyRequired'), 'error');
      return;
    }

    api.updateProvider({ name, format, api_key: apiKey, api_base: apiBase })
      .then(() => {
        this._closeProviderDialog();
        showToast(i18n.t('settings.providerSaved'));
        this.loadSettings();
      })
      .catch(e => showToast(e.message, 'error'));
  }

  _editProvider(name) {
    const p = this.data?.providers?.find(pr => pr.name === name);
    if (!p) return;
    this._showAddProviderDialog(p);
  }

  async _deleteProvider(name) {
    const providers = this.data?.providers || [];
    if (providers.length <= 1) {
      showToast(i18n.t('settings.cannotDeleteLast'), 'error');
      return;
    }
    // `confirmDialog`, non la confirm() nativa: nella WebView dell'app quella
    // non mostra niente e ritorna false, quindi il tasto elimina non faceva
    // assolutamente nulla — nessun dialogo, nessuna richiesta, nessun errore.
    if (!await confirmDialog(i18n.t('settings.deleteProviderConfirm', { name }))) return;
    api.deleteProvider({ name })
      .then(() => {
        showToast(i18n.t('settings.providerDeleted'));
        this.loadSettings();
      })
      .catch(e => showToast(e.message, 'error'));
  }

  _closeProviderDialog() {
    const dialog = document.getElementById('provider-dialog');
    if (dialog) { dialog.close(); dialog.remove(); }
  }

  _saveWebSearch() {
    const v = k => this._val(k);
    const payload = {
      search_engine: v('ws_engine'),
      max_results: Number(v('ws_max')) || null,
      timeout: Number(v('ws_timeout')) || null,
      fetch_max_chars: Number(v('ws_fetch_max')) || null,
    };
    api.updateWebSearch(payload)
      .then(() => showToast(i18n.t('settings.saved')))
      .catch(e => showToast(e.message, 'error'));
  }

  // ── Catalogo modelli ───────────────────────────────────────────────

  _toggleModelCatalog() {
    const el = this.contentEl.querySelector('#model-catalog');
    if (!el) return;
    const wasOpen = el.style.display !== 'none';
    el.style.display = wasOpen ? 'none' : '';
    if (!wasOpen) this._loadModelCatalog();
  }

  /* Un gruppo per provider; i cataloghi arrivano in parallelo e ogni gruppo
     si riempie appena il suo fetch risponde. In coda a ogni gruppo c'è
     l'input per un ID manuale (il provider è implicito nel gruppo). */
  _loadModelCatalog() {
    const groupsEl = this.contentEl.querySelector('#model-catalog-groups');
    if (!groupsEl) return;
    const providers = this.data?.providers || [];
    if (!providers.length) {
      groupsEl.innerHTML = `<div class="settings-empty-state">${i18n.t('settings.noProviders')}</div>`;
      return;
    }
    groupsEl.innerHTML = providers.map(p => `
      <div class="model-group" data-group="${escapeHtml(p.name)}">
        <div class="model-group-label">${escapeHtml(p.name)} <span>· ${escapeHtml(this._formatLabel(p.format))}</span></div>
        <div class="model-group-items"><p class="model-group-msg">${i18n.t('settings.loading')}</p></div>
      </div>`).join('');
    for (const p of providers) {
      api.getProviderModels(p.name)
        .then(res => this._fillCatalogGroup(p, (res.models || []).map(m => m.id || m), res.message))
        .catch(() => this._fillCatalogGroup(p, [], i18n.t('settings.couldNotFetch')));
    }
  }

  _fillCatalogGroup(p, models, message) {
    const group = this.contentEl.querySelector(
      `.model-group[data-group="${CSS.escape(p.name)}"] .model-group-items`);
    if (!group) return; // catalogo richiuso o re-render nel frattempo
    const current = this.data?.agent?.model;
    const isActive = this.data?.default_provider === p.name;
    const rows = models.map(m =>
      `<div class="onboarding-model-item${isActive && m === current ? ' selected' : ''}"
        data-model="${escapeHtml(m)}" data-provider="${escapeHtml(p.name)}">${escapeHtml(m)}</div>`
    ).join('');
    const msg = !models.length && message
      ? `<p class="model-group-msg">${escapeHtml(message)}</p>` : '';
    group.innerHTML = `${rows}${msg}
      <input type="text" class="settings-input model-custom-input"
        placeholder="${i18n.t('settings.customModelId')}" autocomplete="off" />`;
    group.querySelectorAll('[data-model]').forEach(el => {
      el.addEventListener('click', () => this._selectModel(el.dataset.provider, el.dataset.model));
    });
    const custom = group.querySelector('.model-custom-input');
    custom.addEventListener('keydown', e => {
      if (e.key === 'Enter' && custom.value.trim()) this._selectModel(p.name, custom.value.trim());
    });
    this._applyCatalogFilter();
  }

  /* Il punto dell'intero redesign: modello e provider si salvano insieme. */
  _selectModel(providerName, model) {
    api.updateSettings({ model, default_provider: providerName })
      .then(() => {
        showToast(i18n.t('settings.saved'));
        this.loadSettings();
      })
      .catch(e => showToast(e.message, 'error'));
  }

  _applyCatalogFilter() {
    const q = (this.contentEl.querySelector('#model-search')?.value || '').toLowerCase();
    this.contentEl.querySelectorAll('#model-catalog-groups [data-model]').forEach(el => {
      el.style.display = el.dataset.model.toLowerCase().includes(q) ? '' : 'none';
    });
  }

  _showAddProviderDialog(existingProvider) {
    const isEdit = !!existingProvider;
    // La chiave salvata non torna mai al client: il backend manda solo un
    // suggerimento offuscato. Va nel placeholder, MAI nel value, altrimenti
    // un salvataggio senza riscrivere la chiave persisterebbe la maschera.
    const hasStoredKey = isEdit && !!existingProvider.api_key_hint;
    const keyPlaceholder = hasStoredKey
      ? existingProvider.api_key_hint
      : i18n.t('settings.apiKeyPlaceholder');
    const dialog = document.createElement('dialog');
    dialog.className = 'oc-dialog';
    dialog.id = 'provider-dialog';
    dialog.innerHTML = `
      <div class="oc-dialog-inner">
        <h3 style="margin:0 0 16px;font-size:15px;font-weight:600">
          ${isEdit ? i18n.t('settings.editProvider') : i18n.t('settings.addProviderTitle')}
        </h3>
        <div class="settings-field">
          <label class="settings-label">${i18n.t('settings.name')}</label>
          <input type="text" class="settings-input" id="dlg-provider-name" placeholder="${i18n.t('settings.namePlaceholder')}"
            value="${isEdit ? escapeHtml(existingProvider.name) : ''}"
            ${isEdit ? 'readonly' : ''} />
        </div>
        <div class="settings-field">
          <label class="settings-label">${i18n.t('settings.format')}</label>
          <select class="settings-select" id="dlg-provider-format">
            <option value="openai_compat" ${isEdit && existingProvider.format === 'openai_compat' ? 'selected' : ''}>${i18n.t('settings.openaiCompat')}</option>
            <option value="anthropic" ${isEdit && existingProvider.format === 'anthropic' ? 'selected' : ''}>${i18n.t('settings.anthropicCompat')}</option>
          </select>
        </div>
        <div class="settings-field">
          <label class="settings-label">${i18n.t('settings.apiKey')}</label>
          <input type="password" class="settings-input" id="dlg-api-key"
            placeholder="${escapeHtml(keyPlaceholder)}"
            autocomplete="new-password" data-lpignore="true" value="" />
          ${hasStoredKey ? `<span class="settings-field-hint">${i18n.t('settings.apiKeyKeepBlank')}</span>` : ''}
        </div>
        <div class="settings-field">
          <label class="settings-label">${i18n.t('settings.baseUrl')}</label>
          <input type="text" class="settings-input" id="dlg-api-base" placeholder="https://api.openai.com/v1"
            value="${isEdit ? escapeHtml(existingProvider.api_base || '') : ''}" />
        </div>
        <div class="oc-dialog-buttons" style="margin-top:16px">
          <button class="oc-btn oc-btn-cancel" id="dlg-provider-cancel">${i18n.t('common.cancel')}</button>
          <button class="oc-btn oc-btn-confirm" id="dlg-provider-save">${i18n.t('settings.save')}</button>
        </div>
      </div>`;
    document.body.appendChild(dialog);
    dialog.showModal();

    const formatSelect = dialog.querySelector('#dlg-provider-format');
    const baseInput = dialog.querySelector('#dlg-api-base');
    formatSelect.addEventListener('change', () => {
      const defaults = {
        'openai_compat': 'https://api.openai.com/v1',
        'anthropic': 'https://api.anthropic.com',
      };
      baseInput.placeholder = defaults[formatSelect.value] || '';
    });

    dialog.querySelector('#dlg-provider-cancel').addEventListener('click', () => this._closeProviderDialog());
    dialog.querySelector('#dlg-provider-save').addEventListener('click', () => {
      const name = dialog.querySelector('#dlg-provider-name').value.trim();
      const format = dialog.querySelector('#dlg-provider-format').value;
      const apiKey = dialog.querySelector('#dlg-api-key').value.trim();
      const apiBase = dialog.querySelector('#dlg-api-base').value.trim();
      // In modifica il campo vuoto vale sempre "tieni la chiave salvata":
      // il provider esiste già, non serve ridigitarla per cambiare l'URL.
      this._saveProvider(name, format, apiKey, apiBase, { keepStoredKey: isEdit });
    });
    dialog.addEventListener('close', () => dialog.remove());
  }

  _val(key) {
    const el = this.contentEl.querySelector(`[data-key="${key}"]`);
    if (!el) return '';
    if (el.type === 'checkbox') return el.checked ? 'on' : '';
    return el.value;
  }

  // ── Utils ──────────────────────────────────────────────────────────

  _fmtNum(n) {
    if (n == null) return '—';
    if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
    if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
    return String(n);
  }
}
