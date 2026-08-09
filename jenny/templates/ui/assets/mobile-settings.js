/** Mobile Settings Controller — accordion-based settings panel. */

import { api } from './shared/api-client.js';
import { escapeHtml, showToast } from './shared/utils.js';
import { i18n } from './shared/i18n.js';
import { AppState } from './shared/state.js';
import { confirmDialog, detailDialog } from './shared/dialog.js';
import { THEMES, DEFAULT_THEME, setTheme } from './shared/theme.js';
import { advancedMode, setAdvancedMode } from './shared/advanced-mode.js';
import { mascotVisible, setMascotVisible,
  mascotColor, setMascotColor, mascotSize, setMascotSize,
  MASCOT_SIZES } from './shared/mascot.js';
import { homeView, setHomeView, HOME_VIEW_CHOICES } from './shared/home-view.js';
import { TelegramPairingWidget } from './shared/telegram-pairing.js';
import {
  BatteryExemptionCard,
  batteryExemptionSupported,
  batteryExemptionNeeded,
} from './shared/battery-exemption.js';
import {
  runExportFlow,
  runImportFlow,
  runSnapshotRestore,
} from './shared/backup-flow.js';

// Ripiego per `power.modes` quando il payload arriva da un gateway più vecchio
// del client: stesso ordine di `KEEP_AWAKE_MODES` in config/schema.py, dal più
// parsimonioso al più affamato.
const KEEP_AWAKE_CHOICES = ['off', 'turns', 'always'];

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
    // La card batteria resta in ascolto di visibilitychange finché non la si
    // chiude: senza questo, ogni ritorno nelle impostazioni ne lascia una viva.
    if (this._batteryCard) {
      this._batteryCard.destroy();
      this._batteryCard = null;
    }
    // Stesso motivo per il listener della diagnostica: senza, ogni ritorno
    // nelle impostazioni ne lascia uno vivo che ricarica l'endpoint.
    if (this._onPowerVisible) {
      document.removeEventListener('visibilitychange', this._onPowerVisible);
      this._onPowerVisible = null;
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
      this._renderCronRecovery(d),
      this._section('personalization', 'ti-palette', i18n.t('settings.personalization'), this._renderPersonalization(d)),
      this._section('models', 'ti-cpu', i18n.t('settings.model'), this._renderModelSettings(d)),
      this._section('tools', 'ti-tool', i18n.t('settings.tools'), this._renderTools(d)),
      this._renderBatterySection(d),
      this._section('ssh', 'ti-terminal-2', i18n.t('settings.ssh.title'), this._renderSsh()),
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
    return this._recoveryNotice(
      i18n.t(fromDefaults ? 'settings.configRecoveredDefaults' : 'settings.configRecoveredBackup'),
      info.broken_file,
      fromDefaults,
    );
  }

  /* Stesso avviso per lo store dei job cron. Merita una riga sua e non una
     variante di quella sopra: qui, con restored_from = "empty", a mancare sono
     i promemoria che l'utente aveva creato — e quelli non si notano assenti,
     si notano solo quando non suonano. */
  _renderCronRecovery(d) {
    const info = d.cron_recovery;
    if (!info) return '';
    const empty = info.restored_from === 'empty';
    return this._recoveryNotice(
      i18n.t(empty ? 'settings.cronRecoveredEmpty' : 'settings.cronRecoveredBackup'),
      info.broken_file,
      empty,
    );
  }

  _recoveryNotice(text, brokenFile, strong) {
    const where = brokenFile
      ? `<div class="settings-notice-path">${escapeHtml(brokenFile)}</div>`
      : '';
    return `<div class="settings-notice${strong ? ' settings-notice-strong' : ''}">
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

  // ── Attività in background (doze) ──────────────────────────────────

  /* Sezione a sé, non annidata sotto Telegram: il doze differisce cron, Dream,
     Atlas, promemoria e heartbeat esattamente come rallenta il long-poll, ma
     finché la richiesta viveva solo nella card di pairing chi Telegram non lo
     usa non se la vedeva chiedere mai.

     Due impostazioni, una storia sola: l'esenzione dice ad Android di non
     strozzare Jenny, keepAwake decide se Jenny tiene sveglia la CPU da sé.
     Fuori dalla WebView Android il bridge nativo non c'è e la card sparisce,
     ma keepAwake vive nel config del gateway ed è modificabile da qualunque
     browser: la sezione resta, con il solo controllo che ha ancora senso. */
  _renderBatterySection(d) {
    // Aperta d'ufficio quando l'esenzione manca: un accordion chiuso è
    // esattamente il posto in cui il problema è rimasto invisibile finora.
    if (batteryExemptionSupported() && batteryExemptionNeeded()) {
      this._openSections.add('battery');
    }
    const card = batteryExemptionSupported()
      ? `<div id="settings-battery-card"></div><div class="settings-divider"></div>`
      : '';
    return this._section(
      'battery', 'ti-battery-charging', i18n.t('settings.battery.title'),
      `${card}${this._renderKeepAwake(d)}<div id="settings-power-diagnostics"></div>`,
    );
  }

  /* Wakelock anti-doze. Il costo della scelta ("i lavori slittano", "consuma
     batteria") è l'unica cosa che qui conta — il nome da solo ("Sempre") non
     dice cosa l'utente sta accettando — ma dentro una <option> non ci stava:
     il testo di un'opzione nativa non va a capo, e su un telefono da 1440px la
     frase veniva tagliata esattamente sulla clausola del costo. Quindi nella
     select resta il nome breve e il costo vive sotto, su una riga che segue la
     selezione (v. `_wireSections`) e che può occupare le righe che le servono.

     La riga sul riavvio non è un dettaglio da nota a piè di pagina: il lock di
     servizio si prende una volta all'avvio del gateway, quindi chi passa a
     "Sempre" e resta a guardare non vedrebbe cambiare niente e penserebbe che
     l'impostazione sia rotta. */
  _renderKeepAwake(d) {
    const power = (d && d.power) || {};
    const current = power.keep_awake || 'turns';
    const modes = power.modes || KEEP_AWAKE_CHOICES;
    const options = modes.map(id =>
      `<option value="${escapeHtml(id)}"${id === current ? ' selected' : ''}>${escapeHtml(i18n.t(`settings.battery.keepAwake.${id}`))}</option>`
    ).join('');
    return `
      <div class="settings-subheading">${i18n.t('settings.battery.keepAwakeTitle')}</div>
      <div class="settings-field">
        <select class="settings-select" id="keep-awake-select">${options}</select>
        <p class="settings-choice-cost" id="keep-awake-cost">${escapeHtml(this._keepAwakeCost(current))}</p>
        <p class="settings-hint" style="margin:6px 0 0;font-size:12px;color:var(--text-faint)">${i18n.t('settings.battery.keepAwakeHint')}</p>
        <p class="settings-hint" style="margin:6px 0 0;font-size:12px;color:var(--text-faint)"><i class="ti ti-refresh"></i> ${i18n.t('settings.battery.keepAwakeRestart')}</p>
      </div>`;
  }

  /** Il costo della modalità, o stringa vuota se non lo conosciamo.
   *
   *  I modi arrivano dal gateway (`power.modes`), la copy dai file i18n: un
   *  gateway più nuovo del client può mandarne uno che qui non ha frase, e
   *  `i18n.t` in quel caso ritorna la chiave — stampata sotto la select
   *  sembrerebbe un guasto. Meglio nessuna riga che "settings.battery...". */
  _keepAwakeCost(mode) {
    const key = `settings.battery.keepAwakeCost.${mode}`;
    const text = i18n.t(key);
    return text === key ? '' : text;
  }

  // ── Diagnostica energetica ─────────────────────────────────────────

  /* Chiamata a parte e non dentro il payload delle impostazioni: interroga il
     bridge Android (tre chiamate JNI) e va riletta al ritorno da un dialogo di
     sistema, quando il resto delle impostazioni non è cambiato.

     Il pannello risponde alla domanda che finora non aveva risposta: "sta
     girando o no?". Un gateway ucciso dal gestore energetico dell'OEM non
     lascia niente dietro di sé — nessun errore, nessuna notifica, solo
     promemoria che smettono di arrivare — e l'utente se ne accorge giorni
     dopo, se se ne accorge. */
  async _loadPowerDiagnostics() {
    const el = this.contentEl.querySelector('#settings-power-diagnostics');
    if (!el) return;
    let diag = null;
    try {
      diag = await api.getPowerDiagnostics();
    } catch (_) {
      // Endpoint muto (gateway vecchio, richiesta fallita): una riga sobria,
      // non un errore rosso — qui non si è rotto niente di quello che l'utente
      // stava facendo.
      if (el.isConnected) {
        el.innerHTML = `<div class="settings-divider"></div>
          <div class="settings-empty-state">${i18n.t('settings.battery.diagUnavailable')}</div>`;
      }
      return;
    }
    // Un re-render nel frattempo ha staccato questo nodo: la risposta appartiene
    // a un pannello che non è più nel documento, e il nuovo si ricarica da sé.
    if (!el.isConnected) return;
    // Fuori da Android i tre booleani non significano niente e i buchi non si
    // misurano: meglio niente pannello che un pannello di "no".
    if (!diag || !diag.android) { el.innerHTML = ''; return; }
    el.innerHTML = this._renderPowerDiagnostics(diag);
    this._wirePowerDiagnostics(el);
  }

  _renderPowerDiagnostics(diag) {
    const rows = [
      ['diagExempt', diag.battery_exempt],
      ['diagExactAlarms', diag.exact_alarms],
      ['diagWakelock', diag.wakelock_held],
    ].map(([key, ok]) => `
      <div class="settings-field-row">
        <span class="settings-field-label">${i18n.t(`settings.battery.${key}`)}</span>
        <span class="settings-field-value"><i class="ti ti-${ok ? 'check' : 'x'}"></i> ${i18n.t(ok ? 'settings.battery.diagYes' : 'settings.battery.diagNo')}</span>
      </div>`).join('');
    const gaps = Array.isArray(diag.gaps) ? diag.gaps : [];
    const gapRows = gaps.length
      ? gaps.map(g => `
          <div class="settings-field-row">
            <span class="settings-field-label">${escapeHtml(this._formatGapDuration(g.duration_ms))}</span>
            <span class="settings-field-value">${escapeHtml(this._formatGapWhen(g.start_ms))}</span>
          </div>`).join('')
      : `<div class="settings-empty-state">${i18n.t('settings.battery.gapsEmpty')}</div>`;
    return `
      <div class="settings-divider"></div>
      <div class="settings-subheading">${i18n.t('settings.battery.diagTitle')}</div>
      ${rows}
      ${this._renderExactAlarmRequest(diag)}
      <div class="settings-subheading">${i18n.t('settings.battery.gapsTitle')}</div>
      ${gapRows}
      <p class="settings-hint" style="margin:6px 0 0;font-size:12px;color:var(--text-faint)">${i18n.t('settings.battery.gapsHint', { minutes: diag.gap_warning_min })}</p>
      ${gaps.length ? this._renderOemGuidance() : ''}`;
  }

  /* Il permesso da cui dipende tutto il resto, e l'unico modo di concederlo.
     Un'app che punta ad API 33 o più si ritrova SCHEDULE_EXACT_ALARM negato
     alla prima installazione: dichiararlo nel manifest non basta, e finché
     manca ogni sveglia degrada a inesatta — misurato su un telefono nuovo,
     cron e watchdog partivano con dieci minuti di ritardo e il controllo di
     rete con un'ora. La riga sopra lo diceva già, ma dirlo e basta lasciava
     l'utente senza niente da fare.

     Attaccata alla riga "Sveglie precise" e solo quando è "no": a permesso
     concesso sarebbe un avviso che si impara a ignorare, come per la card
     dell'esenzione. `!== false` e non `!diag.exact_alarms`: da un gateway che
     il campo non lo manda non si deduce che il permesso manchi. */
  _renderExactAlarmRequest(diag) {
    if (diag.exact_alarms !== false) return '';
    // Bridge più vecchio della UI: nessun bottone da offrire, il permesso si
    // concede solo dalla schermata di sistema che sa aprire lui.
    const native = window.JennyNative;
    if (!native || typeof native.requestExactAlarmPermission !== 'function') return '';
    return `
      <div class="settings-notice settings-notice-strong">
        <i class="ti ti-alarm"></i>
        <div>
          <div>${i18n.t('settings.battery.exactAlarmsHint')}</div>
          <div style="margin-top:6px"><i class="ti ti-refresh"></i> ${i18n.t('settings.battery.exactAlarmsRestart')}</div>
        </div>
      </div>
      <div class="onboarding-nav">
        <button class="onboarding-btn onboarding-btn-secondary" id="btn-exact-alarms">
          ${i18n.t('settings.battery.exactAlarmsButton')}
        </button>
      </div>`;
  }

  /* La carta che dice l'unica cosa che solo l'utente può fare. Compare solo
     quando un buco è stato davvero registrato: senza prove sarebbe l'ennesimo
     avviso preventivo che si impara a ignorare. */
  _renderOemGuidance() {
    const native = window.JennyNative;
    let brandRaw = '';
    try {
      if (native && typeof native.deviceManufacturer === 'function') {
        brandRaw = String(native.deviceManufacturer() || '');
      }
    } catch (_) { /* bridge che solleva: si resta sul link generico */ }
    // Slug di dontkillmyapp.com: minuscolo e ridotto ad ASCII sicuro, perché
    // Build.MANUFACTURER è testo libero deciso dall'OEM ("TCL Communication").
    const slug = brandRaw.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
    const url = slug
      ? `https://dontkillmyapp.com/${encodeURIComponent(slug)}`
      : 'https://dontkillmyapp.com/';
    const brand = brandRaw || i18n.t('settings.battery.oemUnknownBrand');
    // Il link resta un <a> normale: la WebView devia le navigazioni fuori dal
    // gateway locale su una Chrome Custom Tab (MainActivity#openExternalUrl),
    // mentre aprirlo dentro la SPA la sostituirebbe senza via di ritorno.
    const link = `<a href="${escapeHtml(url)}" target="_blank" rel="noopener">${escapeHtml(i18n.t('settings.battery.oemLink', { brand }))}</a>`;
    const canOpen = !!(native && typeof native.openBatterySettings === 'function');
    const button = canOpen
      ? `<div class="onboarding-nav">
           <button class="onboarding-btn onboarding-btn-secondary" id="btn-oem-battery">
             ${i18n.t('settings.battery.oemButton')}
           </button>
         </div>`
      : '';
    return `
      <div class="settings-notice settings-notice-strong">
        <i class="ti ti-alert-triangle"></i>
        <div>
          <div>${i18n.t('settings.battery.oemHint')}</div>
          <div style="margin-top:6px">${link}</div>
        </div>
      </div>
      ${button}`;
  }

  _wirePowerDiagnostics(root) {
    // Sveglie precise: al ritorno dalla schermata di sistema il pannello si
    // ricarica da sé (il visibilitychange di `_wireSections`), quindi la riga
    // passa a "Sì" e la richiesta sparisce senza fare niente qui.
    const exactBtn = root.querySelector('#btn-exact-alarms');
    if (exactBtn) {
      exactBtn.addEventListener('click', () => {
        const native = window.JennyNative;
        if (!native || typeof native.requestExactAlarmPermission !== 'function') return;
        let opened = false;
        try {
          opened = !!native.requestExactAlarmPermission();
        } catch (_) { opened = false; }
        // Sotto Android 12 il permesso non esiste e la schermata nemmeno:
        // dirlo, invece di lasciare il tap senza conseguenze visibili.
        if (!opened) showToast(i18n.t('settings.battery.exactAlarmsFailed'), 'error');
      });
    }
    const btn = root.querySelector('#btn-oem-battery');
    if (!btn) return;
    btn.addEventListener('click', () => {
      const native = window.JennyNative;
      if (!native || typeof native.openBatterySettings !== 'function') return;
      let opened = false;
      try {
        opened = !!native.openBatterySettings();
      } catch (_) { opened = false; }
      // Nessuna schermata raggiungibile: dirlo, invece di lasciare il tap
      // senza conseguenze visibili. Restano le istruzioni del link.
      if (!opened) showToast(i18n.t('settings.battery.oemOpenFailed'), 'error');
    });
  }

  /** Durata di un buco, nella lingua dell'utente ("4h 12m"). */
  _formatGapDuration(ms) {
    // Arrotondato al minuto e mai a zero: un buco registrato è sopra soglia,
    // e "0m" lo farebbe sembrare un errore di misura.
    const totalMin = Math.max(1, Math.round((Number(ms) || 0) / 60000));
    const days = Math.floor(totalMin / 1440);
    const hours = Math.floor((totalMin % 1440) / 60);
    const minutes = totalMin % 60;
    if (days) return i18n.t('settings.battery.gapDays', { days, hours });
    if (hours) return i18n.t('settings.battery.gapHours', { hours, minutes });
    return i18n.t('settings.battery.gapMinutes', { minutes });
  }

  /** Quando il buco è cominciato ("ieri alle 23:40").
   *
   *  L'inizio e non la fine: l'ora in cui Jenny è stata uccisa è quella che si
   *  riconosce ("ah, quando metto il telefono in carica la notte"). */
  _formatGapWhen(startMs) {
    const at = new Date(Number(startMs) || 0);
    const time = at.toLocaleTimeString(i18n.locale, { hour: '2-digit', minute: '2-digit' });
    const midnight = new Date();
    midnight.setHours(0, 0, 0, 0);
    const dayMs = 86400000;
    if (at.getTime() >= midnight.getTime()) {
      return i18n.t('settings.battery.gapToday', { time });
    }
    if (at.getTime() >= midnight.getTime() - dayMs) {
      return i18n.t('settings.battery.gapYesterday', { time });
    }
    const date = at.toLocaleDateString(i18n.locale, { day: 'numeric', month: 'short' });
    return i18n.t('settings.battery.gapOn', { date, time });
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

  // ── SSH ────────────────────────────────────────────────────────────

  /* Il blocco SSH arriva da /api/settings/ssh, non dal payload di /api/settings:
     è l'unica sezione con stato che vive fuori dal config (chiave sul disco,
     riga in known_hosts), e tenerla su una chiamata sua evita di far pagare
     quelle letture a ogni apertura delle impostazioni. */
  _renderSsh() {
    return `<div id="ssh-block"><div class="settings-empty-state">${i18n.t('settings.loading')}</div></div>`;
  }

  async _loadSsh() {
    const blockEl = this.contentEl.querySelector('#ssh-block');
    if (!blockEl) return;
    try {
      this._ssh = await api.getSsh();
    } catch {
      blockEl.innerHTML = `<div class="settings-empty-state">${i18n.t('settings.ssh.loadFailed')}</div>`;
      return;
    }
    blockEl.innerHTML = this._renderSshBlock(this._ssh);
    this._wireSshBlock();
  }

  _renderSshBlock(d) {
    const hosts = d.hosts || [];
    const list = hosts.length
      ? hosts.map(h => this._renderSshHost(h)).join('')
      : `<div class="settings-empty-state">${i18n.t('settings.ssh.empty')}</div>`;
    return `
      <div class="settings-field settings-toggle-row">
        <label class="settings-label">${i18n.t('settings.ssh.enable')}</label>
        <label class="toggle-switch">
          <input type="checkbox" id="ssh-enabled-toggle" ${d.enabled ? 'checked' : ''}>
          <span class="toggle-slider"></span>
        </label>
      </div>
      <p class="settings-hint" style="margin:6px 0 10px;font-size:12px;color:var(--text-faint)">${i18n.t('settings.ssh.hint')}</p>
      ${this._renderSshCredentialsLost(d)}
      ${list}
      <button class="settings-btn-add" id="btn-ssh-add"><i class="ti ti-plus"></i> ${i18n.t('settings.ssh.addHost')}</button>`;
  }

  /* Credenziali sparite da host che erano già stati verificati: quasi sempre
     un workspace ripristinato da backup. Chiave privata e known_hosts vivono
     fuori dal workspace apposta, quindi nel backup non ci sono e il ripristino
     non può riportarli. Senza questa riga l'utente vede solo dei badge "non
     verificato" e un tool SSH che fallisce, e sembra un guasto. */
  _renderSshCredentialsLost(d) {
    const aliases = d.credentials_lost || [];
    if (!aliases.length) return '';
    const text = i18n.t('settings.ssh.credentialsLost', { aliases: aliases.join(', ') });
    return `<div class="settings-notice settings-notice-strong">
      <i class="ti ti-alert-triangle"></i>
      <div>${escapeHtml(text)}</div>
    </div>`;
  }

  /* Una card per host. I due stati che decidono se l'host è usabile —
     credenziale pronta (chiave generata o password impostata) e impronta
     accettata — stanno in chiaro sulla card: sono i due passi che l'utente deve
     fare, e nasconderli dietro un tap lascerebbe host mezzi configurati che
     falliscono solo al primo comando. */
  _renderSshHost(h) {
    const alias = escapeHtml(h.alias);
    const byPassword = h.auth === 'password';
    const pinned = h.pinned
      ? `<span class="provider-badge format-badge">${i18n.t('settings.ssh.statusPinned')}</span>`
      : `<span class="provider-badge format-badge">${i18n.t('settings.ssh.statusUnpinned')}</span>`;
    /* Lo stato della credenziale segue il modo scelto: su un host a password
       "Nessuna chiave" sarebbe un allarme per qualcosa che non serve, e
       nasconderebbe l'unica cosa che conta lì, cioè se la password c'è. */
    const credentialState = byPassword
      ? (h.has_password
        ? i18n.t('settings.ssh.statusPasswordSet')
        : i18n.t('settings.ssh.statusPasswordMissing'))
      : (h.has_key
        ? i18n.t('settings.ssh.statusKeyReady')
        : i18n.t('settings.ssh.statusKeyMissing'));
    const desc = h.description
      ? `<div style="font-size:12px;color:var(--text-faint)">${escapeHtml(h.description)}</div>`
      : '';
    return `<div class="provider-card" data-ssh-alias="${alias}">
      <div class="provider-card-header">
        <span class="provider-name">${alias}</span>
        ${pinned}
      </div>
      <div class="provider-card-body">
        <span class="provider-url">${escapeHtml(`${h.username}@${h.host}:${h.port}`)}</span>
        <span class="provider-key">${escapeHtml(credentialState)}</span>
      </div>
      ${desc}
      ${this._renderSshPublicKey(h)}
      <div class="provider-card-actions">
        ${byPassword ? '' : `<button class="settings-btn-add ssh-generate" data-ssh-alias="${alias}" data-has-key="${h.has_key ? '1' : ''}">
          ${h.has_key ? i18n.t('settings.ssh.regenerateKey') : i18n.t('settings.ssh.generateKey')}
        </button>`}
        <button class="settings-btn-add ssh-verify" data-ssh-alias="${alias}">${i18n.t('settings.ssh.verify')}</button>
        <button class="btn-icon ssh-edit" data-ssh-alias="${alias}" title="${i18n.t('settings.edit')}">
          <i class="ti ti-edit"></i>
        </button>
        <button class="btn-icon btn-danger ssh-delete" data-ssh-alias="${alias}" title="${i18n.t('settings.delete')}">
          <i class="ti ti-trash"></i>
        </button>
      </div>
    </div>`;
  }

  /* La pubblica resta a schermo finché l'host esiste: il passo "incollala in
     authorized_keys" avviene su un'altra macchina, e mostrarla una volta sola
     costringerebbe a rigenerare la coppia — cioè a invalidare la chiave che si
     stava installando.

     Su un host a password tutto questo blocco sparisce: "copia questa chiave
     pubblica sul server" è un passo che lì non esiste, e lasciarlo a schermo
     farebbe credere che manchi qualcosa da fare. Una chiave eventualmente
     generata prima resta sul disco e ricompare tornando a `auth = key`. */
  _renderSshPublicKey(h) {
    if (h.auth === 'password') return '';
    if (!h.public_key) {
      return `<div class="settings-field-hint">${i18n.t('settings.ssh.noKeyYet')}</div>`;
    }
    return `<div style="margin-top:8px">
      <div class="settings-field-hint">${i18n.t('settings.ssh.publicKeyHint')}</div>
      <code style="display:block;margin:4px 0;padding:6px 8px;font-size:11px;word-break:break-all;
        background:var(--bg-elevated,rgba(128,128,128,.12));border-radius:6px">${escapeHtml(h.public_key)}</code>
      <button class="settings-btn-add ssh-copy" data-ssh-alias="${escapeHtml(h.alias)}">
        <i class="ti ti-copy"></i> ${i18n.t('settings.ssh.copy')}
      </button>
    </div>`;
  }

  _wireSshBlock() {
    const toggle = this.contentEl.querySelector('#ssh-enabled-toggle');
    if (toggle) {
      toggle.addEventListener('change', () => {
        const enabled = toggle.checked;
        api.updateSsh({ enabled: enabled ? '1' : '0' })
          .then(() => showToast(i18n.t(enabled ? 'settings.ssh.on' : 'settings.ssh.off')))
          .catch(e => {
            toggle.checked = !enabled;  // rollback sull'errore
            showToast(e.message || i18n.t('settings.saveError'), 'error');
          });
      });
    }
    this._wireBtn('btn-ssh-add', () => this._showSshHostDialog());
    const each = (selector, fn) =>
      this.contentEl.querySelectorAll(selector).forEach(btn =>
        btn.addEventListener('click', () => fn(btn.dataset.sshAlias, btn)));
    each('.ssh-generate', (alias, btn) => this._sshGenerateKey(alias, !!btn.dataset.hasKey));
    each('.ssh-verify', alias => this._sshVerify(alias));
    each('.ssh-edit', alias => this._showSshHostDialog(
      (this._ssh?.hosts || []).find(h => h.alias === alias)));
    each('.ssh-delete', alias => this._sshDelete(alias));
    each('.ssh-copy', alias => this._sshCopyPublicKey(alias));
  }

  /* Le route SSH rispondono con un corpo di errore in testo semplice, e quel
     testo è già scritto per l'utente ("host refused by the network policy: …"):
     va mostrato com'è, non sostituito da un codice di stato. */
  async _sshGenerateKey(alias, hasKey) {
    if (hasKey && !await confirmDialog(i18n.t('settings.ssh.regenerateConfirm', { alias }))) return;
    try {
      await api.generateSshKey(alias, { replace: hasKey });
      showToast(i18n.t('settings.ssh.keyGenerated'));
      this._loadSsh();
    } catch (e) { showToast(e.message, 'error'); }
  }

  async _sshCopyPublicKey(alias) {
    const host = (this._ssh?.hosts || []).find(h => h.alias === alias);
    if (!host?.public_key) return;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(host.public_key);
      } else {
        // WebView senza Clipboard API: il vecchio execCommand su una textarea
        // fuori schermo funziona ancora, ed è l'unica via che resta.
        const area = document.createElement('textarea');
        area.value = host.public_key;
        area.style.position = 'fixed';
        area.style.left = '-9999px';
        document.body.appendChild(area);
        area.select();
        document.execCommand('copy');
        area.remove();
      }
      showToast(i18n.t('settings.ssh.copied'));
    } catch {
      showToast(i18n.t('settings.ssh.copyFailed'), 'error');
    }
  }

  /* Legge l'impronta e la mette davanti all'utente. Il probe non accetta
     niente: fin qui known_hosts non è stato toccato. */
  async _sshVerify(alias) {
    showToast(i18n.t('settings.ssh.verifying'));
    let probe;
    try {
      probe = (await api.probeSshHostKey(alias)).probe;
    } catch (e) { showToast(e.message, 'error'); return; }

    if (probe.already_accepted) {
      showToast(i18n.t('settings.ssh.alreadyAccepted'));
      this._loadSsh();
      return;
    }
    const accepted = probe.changed
      ? await this._confirmChangedHostKey(alias, probe)
      : await this._confirmNewHostKey(alias, probe);
    if (!accepted) return;

    try {
      await api.acceptSshHostKey(alias, probe.fingerprint, { replace: probe.changed });
      showToast(i18n.t('settings.ssh.accepted'));
      this._loadSsh();
    } catch (e) { showToast(e.message, 'error'); }
  }

  /* Con la password il pinning conta di più, non di meno: una chiave la si
     presenta a un impostore senza dargli niente di riutilizzabile, una password
     invece gliela si consegna intera al primo comando. Per questo l'avviso in
     più sta proprio qui, nel momento in cui l'utente decide di fidarsi. */
  _sshPasswordPinningWarning(alias) {
    const host = (this._ssh?.hosts || []).find(h => h.alias === alias);
    if (host?.auth !== 'password') return '';
    return `<p style="font-size:12px;margin-top:8px">${escapeHtml(i18n.t('settings.ssh.fingerprintPasswordWarning'))}</p>`;
  }

  _confirmNewHostKey(alias, probe) {
    const passwordWarning = this._sshPasswordPinningWarning(alias);
    return detailDialog({
      title: i18n.t('settings.ssh.fingerprintTitle'),
      bodyHtml: `
        <p style="font-size:13px">${escapeHtml(i18n.t('settings.ssh.fingerprintIntro', { alias }))}</p>
        <code style="display:block;margin:8px 0;padding:8px;font-size:12px;word-break:break-all;
          background:var(--bg-elevated,rgba(128,128,128,.12));border-radius:6px">${escapeHtml(probe.fingerprint)}</code>
        <p style="font-size:12px;color:var(--text-faint)">${escapeHtml(i18n.t('settings.ssh.fingerprintServerHint'))}</p>
        ${passwordWarning}`,
      actions: [
        { id: 'cancel', label: i18n.t('common.cancel') },
        { id: 'accept', label: i18n.t('settings.ssh.accept'), variant: 'primary' },
      ],
    }).then(choice => choice === 'accept');
  }

  /* Host key diversa da quella accettata: potenziale MITM, non un
     aggiornamento. Le due impronte vanno affiancate — senza la vecchia accanto
     alla nuova, "accetta" e "annulla" sono una scelta alla cieca — e la
     sostituzione chiede una seconda conferma, perché è quella che butta via la
     verifica fatta la prima volta. */
  async _confirmChangedHostKey(alias, probe) {
    const choice = await detailDialog({
      title: i18n.t('settings.ssh.changedTitle'),
      bodyHtml: `
        <p style="font-size:13px">${escapeHtml(i18n.t('settings.ssh.changedWarning', { alias }))}</p>
        <div style="font-size:12px;color:var(--text-faint);margin-top:8px">${escapeHtml(i18n.t('settings.ssh.changedOld'))}</div>
        <code style="display:block;padding:6px 8px;font-size:12px;word-break:break-all;
          background:var(--bg-elevated,rgba(128,128,128,.12));border-radius:6px">${escapeHtml(probe.pinned_fingerprint || '—')}</code>
        <div style="font-size:12px;color:var(--text-faint);margin-top:8px">${escapeHtml(i18n.t('settings.ssh.changedNew'))}</div>
        <code style="display:block;padding:6px 8px;font-size:12px;word-break:break-all;
          background:var(--bg-elevated,rgba(128,128,128,.12));border-radius:6px">${escapeHtml(probe.fingerprint)}</code>
        ${this._sshPasswordPinningWarning(alias)}`,
      actions: [
        { id: 'cancel', label: i18n.t('common.cancel') },
        { id: 'accept', label: i18n.t('settings.ssh.replace'), variant: 'primary' },
      ],
    });
    if (choice !== 'accept') return false;
    return confirmDialog(i18n.t('settings.ssh.replaceConfirm', { alias }));
  }

  async _sshDelete(alias) {
    if (!await confirmDialog(i18n.t('settings.ssh.deleteConfirm', { alias }))) return;
    try {
      await api.deleteSshHost(alias);
      showToast(i18n.t('settings.ssh.deleted'));
      this._loadSsh();
    } catch (e) { showToast(e.message, 'error'); }
  }

  /* L'alias non è modificabile: è l'identità dell'host, il nome del file di
     chiave e l'unica cosa che il modello passa ai tool SSH. Rinominarlo
     scollegherebbe chiave e impronta dall'host senza dirlo a nessuno.

     La password non viene mai pre-compilata perché non arriva mai: il payload
     di lettura porta solo `has_password`. Il campo vuoto in modifica significa
     quindi "tieni quella salvata", ed è il server a rifiutare il caso in cui
     non ce ne sia una da tenere. */
  _showSshHostDialog(existing) {
    const isEdit = !!existing;
    const auth = existing?.auth === 'password' ? 'password' : 'key';
    /* Il campo password si nasconde con `display` inline, non con l'attributo
       `hidden`: `.settings-field` porta un `display:flex` d'autore, che batte
       il `[hidden] { display:none }` dello user-agent. Con `hidden` il campo
       resterebbe a schermo su un host a chiave. */
    const passwordFieldStyle = auth === 'password' ? '' : 'display:none';
    const dialog = document.createElement('dialog');
    dialog.className = 'oc-dialog';
    dialog.id = 'ssh-host-dialog';
    const value = (field, fallback = '') => escapeHtml(String(existing?.[field] ?? fallback));
    dialog.innerHTML = `
      <div class="oc-dialog-inner">
        <h3 style="margin:0 0 16px;font-size:15px;font-weight:600">
          ${isEdit ? i18n.t('settings.ssh.editHost') : i18n.t('settings.ssh.addHost')}
        </h3>
        <div class="settings-field">
          <label class="settings-label">${i18n.t('settings.ssh.alias')}</label>
          <input type="text" class="settings-input" id="dlg-ssh-alias" placeholder="${i18n.t('settings.ssh.aliasPlaceholder')}"
            value="${isEdit ? value('alias') : ''}" ${isEdit ? 'readonly' : ''} autocomplete="off" />
          <span class="settings-field-hint">${i18n.t('settings.ssh.aliasHint')}</span>
        </div>
        <div class="settings-field">
          <label class="settings-label">${i18n.t('settings.ssh.host')}</label>
          <input type="text" class="settings-input" id="dlg-ssh-host" placeholder="${i18n.t('settings.ssh.hostPlaceholder')}"
            value="${value('host')}" autocomplete="off" />
        </div>
        <div class="settings-field">
          <label class="settings-label">${i18n.t('settings.ssh.port')}</label>
          <input type="number" class="settings-input" id="dlg-ssh-port" value="${value('port', '22')}" />
        </div>
        <div class="settings-field">
          <label class="settings-label">${i18n.t('settings.ssh.username')}</label>
          <input type="text" class="settings-input" id="dlg-ssh-username" placeholder="${i18n.t('settings.ssh.usernamePlaceholder')}"
            value="${value('username')}" autocomplete="off" />
        </div>
        <div class="settings-field">
          <label class="settings-label">${i18n.t('settings.ssh.description')}</label>
          <input type="text" class="settings-input" id="dlg-ssh-description" placeholder="${i18n.t('settings.ssh.descriptionPlaceholder')}"
            value="${value('description')}" autocomplete="off" />
        </div>
        <div class="settings-field">
          <label class="settings-label">${i18n.t('settings.ssh.auth')}</label>
          <select class="settings-select" id="dlg-ssh-auth">
            <option value="key" ${auth === 'key' ? 'selected' : ''}>${i18n.t('settings.ssh.authKey')}</option>
            <option value="password" ${auth === 'password' ? 'selected' : ''}>${i18n.t('settings.ssh.authPassword')}</option>
          </select>
          <span class="settings-field-hint">${i18n.t('settings.ssh.authHint')}</span>
        </div>
        <div class="settings-field" id="dlg-ssh-password-field" style="${passwordFieldStyle}">
          <label class="settings-label">${i18n.t('settings.ssh.password')}</label>
          <input type="password" class="settings-input" id="dlg-ssh-password"
            placeholder="${i18n.t('settings.ssh.passwordPlaceholder')}"
            autocomplete="off" data-lpignore="true" value="" />
          <span class="settings-field-hint">${existing?.has_password
            ? i18n.t('settings.ssh.passwordKeepBlank')
            : i18n.t('settings.ssh.passwordHint')}</span>
        </div>
        <div class="oc-dialog-buttons" style="margin-top:16px">
          <button class="oc-btn oc-btn-cancel" id="dlg-ssh-cancel">${i18n.t('common.cancel')}</button>
          <button class="oc-btn oc-btn-confirm" id="dlg-ssh-save">${i18n.t('settings.save')}</button>
        </div>
      </div>`;
    document.body.appendChild(dialog);
    dialog.showModal();

    const close = () => { dialog.close(); dialog.remove(); };
    const authEl = dialog.querySelector('#dlg-ssh-auth');
    const passwordField = dialog.querySelector('#dlg-ssh-password-field');
    const passwordEl = dialog.querySelector('#dlg-ssh-password');
    /* Passando a "chiave" il campo password viene anche svuotato, non solo
       nascosto: un campo nascosto ma pieno resterebbe nel DOM, e il valore
       digitato per ripensarci un attimo dopo non ha motivo di sopravvivere al
       cambio di modo. */
    authEl.addEventListener('change', () => {
      const byPassword = authEl.value === 'password';
      // Stringa vuota, non 'flex': così torna a valere la regola della classe.
      passwordField.style.display = byPassword ? '' : 'none';
      if (!byPassword) passwordEl.value = '';
    });

    dialog.querySelector('#dlg-ssh-cancel').addEventListener('click', close);
    dialog.querySelector('#dlg-ssh-save').addEventListener('click', async () => {
      const params = {
        alias: dialog.querySelector('#dlg-ssh-alias').value.trim(),
        host: dialog.querySelector('#dlg-ssh-host').value.trim(),
        port: dialog.querySelector('#dlg-ssh-port').value.trim() || '22',
        username: dialog.querySelector('#dlg-ssh-username').value.trim(),
        description: dialog.querySelector('#dlg-ssh-description').value.trim(),
        auth: authEl.value,
      };
      if (!params.alias || !params.host || !params.username) {
        showToast(i18n.t('settings.ssh.fieldsRequired'), 'error');
        return;
      }
      if (params.auth === 'password') {
        // Niente `.trim()`: gli spazi in una password sono contenuto. Il campo
        // vuoto vale "tieni quella salvata", e senza niente di salvato il
        // server rifiuta comunque — questo controllo evita solo il giro.
        const typed = passwordEl.value;
        if (!typed && !existing?.has_password) {
          showToast(i18n.t('settings.ssh.passwordRequired'), 'error');
          return;
        }
        if (typed) params.password = typed;
      }
      try {
        await api.saveSshHost(params);
        close();
        showToast(i18n.t('settings.saved'));
        this._loadSsh();
      } catch (e) { showToast(e.message, 'error'); }
    });
    dialog.addEventListener('close', () => dialog.remove());
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
     mini-label, toggle di visibilità, taglia e variante colore.
     Le opzioni restano SEMPRE a schermo: nasconderle a mascotte spenta faceva
     sembrare che l'unica scelta fosse tenerla o buttarla via — chi la spegneva
     subito non scopriva mai che era personalizzabile. Da spenta si vedono
     inerti (attributo `disabled`), come promessa di cosa si ottiene
     riaccendendola.
     Il lato NON si sceglie qui: lo decide il lancio (v. mobile-jenny.js), e
     un'impostazione che cambia da sola al primo lancio sarebbe una bugia. */
  _renderMascot() {
    const visible = mascotVisible();
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

    // Attività in background: stessa card condivisa con onboarding e Telegram.
    // Qui `grantedKey` è d'obbligo — è l'unica superficie che l'utente apre
    // apposta per controllare, e "nessun messaggio" non è una risposta.
    const batteryContainer = this.contentEl.querySelector('#settings-battery-card');
    if (batteryContainer) {
      if (this._batteryCard) this._batteryCard.destroy();
      this._batteryCard = new BatteryExemptionCard(batteryContainer, {
        tone: 'notice',
        grantedKey: 'settings.battery.granted',
      });
      this._batteryCard.render();
    }

    // Diagnostica energetica: si popola da sola (chiamata a parte) e si
    // rilegge al rientro nell'app — il permesso si concede in un dialogo di
    // sistema, e al ritorno la pagina non ha ricevuto nessun evento.
    if (this._onPowerVisible) {
      document.removeEventListener('visibilitychange', this._onPowerVisible);
    }
    this._onPowerVisible = () => {
      if (document.visibilityState === 'visible') this._loadPowerDiagnostics();
    };
    document.addEventListener('visibilitychange', this._onPowerVisible);
    this._loadPowerDiagnostics();

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

    // Wakelock anti-doze: si salva al cambio, e il toast ripete che vale dal
    // prossimo riavvio — chi lo cambia dalla select non rilegge la riga sotto.
    const keepAwakeSelect = this.contentEl.querySelector('#keep-awake-select');
    if (keepAwakeSelect) {
      // `previous` segue l'ultimo valore accettato dal server, non quello del
      // primo render: due cambi di fila con il secondo fallito riporterebbero
      // altrimenti la select su un modo che non è più quello salvato.
      let previous = keepAwakeSelect.value;
      // Il costo della scelta sta fuori dalla select (una <option> non va a
      // capo) e segue la selezione subito, prima ancora del salvataggio: è
      // quello che l'utente sta valutando, non la conferma di quello che ha
      // già scelto. `textContent`: la frase viene da i18n, non da HTML.
      const costEl = this.contentEl.querySelector('#keep-awake-cost');
      const showCost = (mode) => {
        if (costEl) costEl.textContent = this._keepAwakeCost(mode);
      };
      keepAwakeSelect.addEventListener('change', () => {
        const mode = keepAwakeSelect.value;
        showCost(mode);
        api.updatePower({ keep_awake: mode })
          .then(() => {
            previous = mode;
            if (this.data) this.data.power = { ...(this.data.power || {}), keep_awake: mode };
            showToast(i18n.t('settings.battery.keepAwakeSaved'));
          })
          .catch(() => {
            keepAwakeSelect.value = previous;  // rollback sull'errore
            showCost(previous);
            showToast(i18n.t('settings.saveError'));
          });
      });
    }

    // Add provider
    this._wireBtn('btn-add-provider', () => this._showAddProviderDialog());

    // SSH: il blocco si popola da solo (chiamata a parte, v. _renderSsh)
    this._loadSsh();

    // Backup e ripristino
    this._wireBackup();

    // Modalità avanzata
    const advToggle = this.contentEl.querySelector('#advanced-mode-toggle');
    if (advToggle) advToggle.addEventListener('change', () => setAdvancedMode(advToggle.checked));

    // Mascotte: toggle visibilità (re-render per accendere/spegnere le
    // opzioni sotto) + scelta della taglia
    const mascotToggle = this.contentEl.querySelector('#mascot-visible-toggle');
    if (mascotToggle) {
      mascotToggle.addEventListener('change', () => {
        setMascotVisible(mascotToggle.checked);
        this.render();
      });
    }
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

    // Language selector — solo i bottoni seg con data-locale (quelli della
    // taglia mascotte condividono la classe ma hanno data-mascot-size).
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
            autocomplete="off" data-lpignore="true" value="" />
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
