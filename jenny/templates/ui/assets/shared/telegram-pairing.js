/** Widget condiviso per collegare il bot Telegram (onboarding + impostazioni).
 *
 * Stati: non configurato (input token) → in pairing (codice + polling dello
 * stato ogni 2.5s) → accoppiato (✓ + scollega/disabilita in modalità settings).
 * Le stringhe vivono sotto `settings.telegram.*` così le due superfici restano
 * allineate.
 */

import { api } from './api-client.js';
import { escapeHtml, showToast } from './utils.js';
import { i18n } from './i18n.js';

const POLL_MS = 2500;

export class TelegramPairingWidget {
  /**
   * @param {HTMLElement} container dove renderizzare
   * @param {{mode?: 'onboarding'|'settings', onPaired?: Function}} opts
   */
  constructor(container, opts = {}) {
    this.el = container;
    this.mode = opts.mode || 'settings';
    this.onPaired = opts.onPaired || null;
    this.status = null;
    this._pollTimer = null;
    this._busy = false;
  }

  destroy() {
    this._stopPolling();
  }

  async refresh() {
    try {
      this.status = await api.getTelegramStatus();
    } catch (e) {
      this.el.innerHTML = `<div class="onboarding-hint">${escapeHtml(e.message || 'error')}</div>`;
      return;
    }
    this.render();
  }

  render() {
    this._stopPolling();
    const s = this.status;
    if (!s) return;
    if (s.paired) {
      this._renderPaired();
    } else if (s.enabled && s.configured && s.pairing_code) {
      this._renderPairing();
      this._startPolling();
    } else {
      this._renderTokenForm();
    }
  }

  // ── Stato: non configurato ──────────────────────────────────────────

  _renderTokenForm() {
    const s = this.status;
    const hint = s.token_hint
      ? `<span class="onboarding-hint">${i18n.t('settings.telegram.currentToken')}: ${escapeHtml(s.token_hint)}</span>`
      : '';
    this.el.innerHTML = `
      <p class="onboarding-desc">${i18n.t('settings.telegram.intro')}</p>
      <ol class="tg-steps">
        <li>${i18n.t('settings.telegram.step1')}</li>
        <li>${i18n.t('settings.telegram.step2')}</li>
        <li>${i18n.t('settings.telegram.step3')}</li>
      </ol>
      <div class="onboarding-field">
        <label class="onboarding-label" for="tg-token">${i18n.t('settings.telegram.token')}</label>
        <input type="password" class="onboarding-input" id="tg-token"
               placeholder="123456789:AA..." autocomplete="off" data-lpignore="true">
        ${hint}
      </div>
      <div class="onboarding-nav">
        <button class="onboarding-btn onboarding-btn-primary" id="tg-connect" disabled>
          ${i18n.t('settings.telegram.connect')}
        </button>
      </div>`;
    const input = this.el.querySelector('#tg-token');
    const btn = this.el.querySelector('#tg-connect');
    input.addEventListener('input', () => { btn.disabled = !input.value.trim(); });
    btn.addEventListener('click', () => this._saveToken(input.value.trim()));
  }

  async _saveToken(token) {
    if (this._busy || !token) return;
    this._busy = true;
    const btn = this.el.querySelector('#tg-connect');
    if (btn) { btn.disabled = true; btn.textContent = i18n.t('settings.telegram.validating'); }
    try {
      this.status = await api.saveTelegramToken(token);
      this.render();
    } catch (e) {
      showToast(e.message || i18n.t('settings.telegram.saveFailed'), 'error');
      this.render();
    } finally {
      this._busy = false;
    }
  }

  // ── Stato: in attesa di pairing ─────────────────────────────────────

  _renderPairing() {
    const s = this.status;
    const botLink = s.bot_username
      ? `<a href="https://t.me/${escapeHtml(s.bot_username)}" target="_blank" rel="noopener" class="tg-bot-link">@${escapeHtml(s.bot_username)}</a>`
      : i18n.t('settings.telegram.yourBot');
    this.el.innerHTML = `
      <p class="onboarding-desc">${i18n.t('settings.telegram.sendCode', { bot: botLink })}</p>
      <div class="tg-pairing-code" aria-live="polite">${escapeHtml(s.pairing_code)}</div>
      <p class="onboarding-hint tg-waiting">
        <span class="onboarding-spinner tg-spinner-inline"></span>
        ${i18n.t('settings.telegram.waiting')}
      </p>
      <div class="onboarding-nav">
        <button class="onboarding-btn onboarding-btn-secondary" id="tg-change-token">
          ${i18n.t('settings.telegram.changeToken')}
        </button>
      </div>`;
    this.el.querySelector('#tg-change-token').addEventListener('click', () => {
      this._stopPolling();
      this.status = { ...this.status, configured: true, enabled: false, pairing_code: null };
      this._renderTokenForm();
    });
  }

  _startPolling() {
    this._pollTimer = setInterval(async () => {
      try {
        const s = await api.getTelegramStatus();
        if (s.paired) {
          this.status = s;
          this._stopPolling();
          this.render();
          if (this.onPaired) this.onPaired(s);
        }
      } catch (_) { /* transitorio: si ritenta al prossimo tick */ }
    }, POLL_MS);
  }

  _stopPolling() {
    if (this._pollTimer) {
      clearInterval(this._pollTimer);
      this._pollTimer = null;
    }
  }

  // ── Stato: accoppiato ───────────────────────────────────────────────

  _renderPaired() {
    const s = this.status;
    const who = s.paired_username ? `@${escapeHtml(s.paired_username)}` : i18n.t('settings.telegram.aChat');
    const bot = s.bot_username ? ` (@${escapeHtml(s.bot_username)})` : '';
    const settingsButtons = this.mode === 'settings' ? `
      <div class="onboarding-nav">
        <button class="onboarding-btn onboarding-btn-secondary" id="tg-unpair">
          ${i18n.t('settings.telegram.unpair')}
        </button>
        <button class="onboarding-btn onboarding-btn-secondary" id="tg-disable">
          ${i18n.t('settings.telegram.disable')}
        </button>
      </div>` : '';
    this.el.innerHTML = `
      <div class="tg-paired">
        <i class="ti ti-circle-check"></i>
        ${i18n.t('settings.telegram.paired', { who })}${bot}
      </div>
      ${this._batteryHtml()}
      ${settingsButtons}`;
    const unpairBtn = this.el.querySelector('#tg-unpair');
    if (unpairBtn) unpairBtn.addEventListener('click', () => this._unpair());
    const disableBtn = this.el.querySelector('#tg-disable');
    if (disableBtn) disableBtn.addEventListener('click', () => this._disable());
    const batteryBtn = this.el.querySelector('#tg-battery');
    if (batteryBtn) {
      batteryBtn.addEventListener('click', () => {
        window.JennyNative.requestBatteryExemption();
      });
    }
  }

  /* Solo nella WebView Android e solo se l'esenzione manca: senza, a telefono
     scollegato dalla corrente il doze rallenta il long-poll Telegram. */
  _batteryHtml() {
    const native = window.JennyNative;
    if (!native || typeof native.isBatteryExempt !== 'function') return '';
    try {
      if (native.isBatteryExempt()) return '';
    } catch (_) { return ''; }
    return `
      <p class="onboarding-hint">${i18n.t('settings.telegram.batteryHint')}</p>
      <div class="onboarding-nav">
        <button class="onboarding-btn onboarding-btn-secondary" id="tg-battery">
          ${i18n.t('settings.telegram.batteryButton')}
        </button>
      </div>`;
  }

  async _unpair() {
    if (this._busy) return;
    this._busy = true;
    try {
      this.status = await api.unpairTelegram();
      this.render();
    } catch (e) {
      showToast(e.message || i18n.t('settings.telegram.saveFailed'), 'error');
    } finally {
      this._busy = false;
    }
  }

  async _disable() {
    if (this._busy) return;
    this._busy = true;
    try {
      this.status = await api.disableTelegram();
      showToast(i18n.t('settings.telegram.disabled'), 'info');
      this.render();
    } catch (e) {
      showToast(e.message || i18n.t('settings.telegram.saveFailed'), 'error');
    } finally {
      this._busy = false;
    }
  }
}
