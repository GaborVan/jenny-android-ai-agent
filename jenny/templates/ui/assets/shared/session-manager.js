/** Session Manager — quale conversazione è aperta: attach + caricamento thread.
 *
 *  La chiave *è* l'indirizzo della conversazione, e ne esistono due forme:
 *  `websocket:default` per la chat personale e `project:<nome>` per un progetto.
 *  Il gateway la usa per due cose diverse — la sessione che Jenny rilegge e il
 *  thread che viene disegnato — e le tiene separate da sé.
 */

import { api } from './api-client.js';
import { wsManager } from './ws-manager.js';

const UNIFIED_KEY = 'websocket:default';

export class SessionManager {
  constructor() {
    this.currentKey = UNIFIED_KEY;
    this.currentScope = null;
    this.runStartedAt = null;
    this._initialized = false;
  }

  /** Passa a un'altra conversazione e si iscrive ai suoi messaggi.
   *
   *  Non tocca quel che è a schermo: ridisegnare il thread è di chi possiede la
   *  chat (`mobile-chat`), che sa anche quando è il momento di farlo.
   */
  switchTo(key) {
    const next = key || UNIFIED_KEY;
    if (next === this.currentKey) return false;
    this.currentKey = next;
    this.currentScope = null;
    this.runStartedAt = null;
    wsManager.attachChat(next);
    return true;
  }

  /** La chiave della conversazione personale. */
  get personalKey() {
    return UNIFIED_KEY;
  }

  init() {
    if (this._initialized) return;
    this._initialized = true;
    this.ensureAttached();
  }

  /** Attach the shared chat (no-op if already attached; re-attach on reconnect is automatic). */
  ensureAttached() {
    wsManager.attachChat(this.currentKey);
  }

  async loadThread(key, limit = 160, before = null) {
    const data = await api.fetchWebuiThread(key, { limit, before });
    if (data && typeof data === 'object') {
      this.currentScope = data.workspace_scope || null;
      this.runStartedAt = data.run_started_at || null;
    }
    return data;
  }
}

export const sessionManager = new SessionManager();
