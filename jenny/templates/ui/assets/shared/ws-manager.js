/** Shared WebSocket Manager — Chat connection. */

import { api } from './api-client.js';
import { i18n } from './i18n.js';
import { AppState } from './state.js';

class WebSocketManager extends EventTarget {
  constructor() {
    super();
    this.chatWs = null;
    this.chatConnected = false;
    this.reconnectTimer = null;
    this._reconnectAttempts = 0;
    this.knownChats = new Set();
    this._netListenersBound = false;
    // RPC client→server in volo: id → {resolve, reject, timer}. Vedi request().
    this._pendingRpc = new Map();
    this._rpcSeq = 0;
    this._bindNetworkListeners();
  }

  // Reagisce al ritorno di connettività (app tornata in foreground o rete
  // ripristinata): azzera il backoff e forza un reconnect immediato. I
  // listener vanno registrati UNA sola volta — mai a ogni reconnect — per
  // non duplicarli. `connectChat()` fa da guard contro socket già OPEN/
  // CONNECTING, quindi il resync non crea connessioni doppie.
  _bindNetworkListeners() {
    if (this._netListenersBound) return;
    if (typeof document === 'undefined' || typeof window === 'undefined') return;
    this._netListenersBound = true;
    this._onVisible = () => {
      if (document.visibilityState === 'visible') this._resyncNow();
    };
    this._onOnline = () => this._resyncNow();
    document.addEventListener('visibilitychange', this._onVisible);
    window.addEventListener('online', this._onOnline);
  }

  _resyncNow() {
    this._reconnectAttempts = 0;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.connectChat();
  }

  _makeUrl(path) {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const secret = api.getSecret();
    const sep = path.includes('?') ? '&' : '?';
    const tokenParam = secret ? `${sep}token=${encodeURIComponent(secret)}` : '';
    return `${protocol}//${window.location.host}${path}${tokenParam}`;
  }

  // Strip "websocket:" prefix so callers can pass either a raw UUID or a
  // full session key ("websocket:<uuid>") without creating a double prefix
  // on the server side.
  _stripPrefix(chatId) {
    if (chatId && chatId.startsWith('websocket:')) {
      return chatId.substring(10);
    }
    return chatId;
  }

  // ── Chat ──
  connectChat() {
    // Guard anche su CONNECTING, non solo OPEN: un resync immediato (foreground/
    // online) mentre un socket è ancora in handshake creerebbe altrimenti una
    // seconda connessione.
    const rs = this.chatWs?.readyState;
    if (rs === WebSocket.OPEN || rs === WebSocket.CONNECTING) return;

    const url = this._makeUrl('/');
    this.chatWs = new WebSocket(url);

    this.chatWs.onopen = () => {
      this.chatConnected = true;
      this._reconnectAttempts = 0;
      this.dispatchEvent(new CustomEvent('chat:open'));
      if (this.reconnectTimer) {
        clearTimeout(this.reconnectTimer);
        this.reconnectTimer = null;
      }
      for (const chatId of this.knownChats) {
        this.chatWs.send(JSON.stringify({ type: 'attach', chat_id: chatId }));
      }
    };

    this.chatWs.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.event === 'rpc_result') {
          this._settleRpc(msg);
          return;
        }
        this.dispatchEvent(new CustomEvent('chat:message', { detail: msg }));
        if (msg.chat_id) {
          this.dispatchEvent(new CustomEvent(`chat:${msg.chat_id}:message`, { detail: msg }));
        }
      } catch (err) {
        console.error('Invalid WS message:', event.data);
      }
    };

    this.chatWs.onclose = () => {
      this.chatConnected = false;
      // Una richiesta in volo su un socket chiuso non riceverà mai risposta:
      // rifiutarla subito è ciò che evita un editor appeso fino al timeout.
      this._rejectAllRpc('connection closed');
      this.dispatchEvent(new CustomEvent('chat:close'));
      this._reconnectAttempts++;
      // Retry indefinito con backoff esponenziale (cap 30s): una WebView locale
      // verso un gateway locale non deve mai arrendersi. Il backoff resta per
      // non innescare loop di reconnect stretti.
      const delay = Math.min(3000 * Math.pow(1.5, this._reconnectAttempts - 1), 30000);
      if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
      this.reconnectTimer = setTimeout(() => this.connectChat(), delay);
    };
  }

  attachChat(chatId) {
    const clean = this._stripPrefix(chatId);
    if (this.knownChats.has(clean)) return;
    this.knownChats.add(clean);
    if (this.chatWs?.readyState === WebSocket.OPEN) {
      this.chatWs.send(JSON.stringify({ type: 'attach', chat_id: clean }));
    }
  }

  sendToChat(chatId, text, media = []) {
    if (!this.chatWs || this.chatWs.readyState !== WebSocket.OPEN) return false;
    const clean = this._stripPrefix(chatId);
    const payload = { type: 'message', chat_id: clean, content: text, webui: true };
    // Sola lettura: **in ogni messaggio**, e non in uno stato sul server. Il
    // gateway non se la ricorda apposta — se la ricordasse potrebbe applicare
    // al turno un modo diverso da quello che l'utente aveva sotto gli occhi, e
    // un messaggio partito non si ritira. Si manda solo quando è accesa: il
    // gateway accende su `true` e ignora tutto il resto.
    if (AppState.readonlyTurn === true) payload.readonly = true;
    if (media.length) payload.media = media;
    this.chatWs.send(JSON.stringify(payload));
    this.dispatchEvent(new CustomEvent('chat:sent', { detail: { chat_id: clean } }));
    return true;
  }

  /* Osservazione dell'attività fine di un subagent. Il gateway spinge i frame
     `subagent_activity` SOLO a chi ha mandato un watch: è per questo che la
     modale deve dire quando apre e quando chiude, e non c'è nessun polling da
     sostituire — a modale chiusa il costo lato server è esattamente zero.

     `since` è il cursore da cui ripartire: 0 alla prima apertura, l'ultimo `seq`
     consegnato dopo un reconnect (così non si duplica e non si perde nulla).
     Ritorna false se il socket non è aperto: il chiamante riproverà al prossimo
     `chat:open`, che è anche il momento in cui il watch va rifatto perché il
     gateway ha dimenticato la connessione caduta. */
  sendSubagentWatch(taskId, since = 0) {
    if (!this.chatWs || this.chatWs.readyState !== WebSocket.OPEN) return false;
    this.chatWs.send(JSON.stringify({
      type: 'subagent_watch', task_id: String(taskId), since: Number(since) || 0,
    }));
    return true;
  }

  sendSubagentUnwatch(taskId) {
    if (!this.chatWs || this.chatWs.readyState !== WebSocket.OPEN) return false;
    this.chatWs.send(JSON.stringify({ type: 'subagent_unwatch', task_id: String(taskId) }));
    return true;
  }

  /* ── RPC client→server (`rpc` → `rpc_result`) ──────────────────────────
     La superficie /api/ del gateway non può trasportare contenuto: è servita
     dall'hook di handshake di `websockets`, che non legge body, e i suoi header
     stanno in 8 KB per riga e solo in ISO-8859-1 — `new Headers()` rifiuta
     un'emoji. Un frame WebSocket invece è framed e UTF-8, quindi il
     salvataggio di un file passa da qui.

     Simmetrico a `ui_query`/`ui_result`, che è lo stesso meccanismo nel verso
     opposto: un id opaco correla richiesta e risposta. */
  request(method, params = {}, { timeoutMs = 20000 } = {}) {
    if (!this.chatWs || this.chatWs.readyState !== WebSocket.OPEN) {
      return Promise.reject(new Error(i18n.t('common.gatewayOffline')));
    }
    const id = `rpc-${Date.now().toString(36)}-${(this._rpcSeq++).toString(36)}`;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this._pendingRpc.delete(id);
        reject(new Error(i18n.t('common.requestTimeout')));
      }, timeoutMs);
      this._pendingRpc.set(id, { resolve, reject, timer });
      try {
        this.chatWs.send(JSON.stringify({ type: 'rpc', id, method, params }));
      } catch (err) {
        clearTimeout(timer);
        this._pendingRpc.delete(id);
        reject(err);
      }
    });
  }

  _settleRpc(msg) {
    const pending = this._pendingRpc.get(msg.id);
    if (!pending) return;  // risposta tardiva a una richiesta già scaduta
    this._pendingRpc.delete(msg.id);
    clearTimeout(pending.timer);
    if (msg.ok) {
      pending.resolve(msg.result || {});
      return;
    }
    const err = new Error(msg.error?.message || 'request failed');
    err.code = msg.error?.code || 'internal';
    pending.reject(err);
  }

  _rejectAllRpc(reason) {
    for (const [, pending] of this._pendingRpc) {
      clearTimeout(pending.timer);
      pending.reject(new Error(reason));
    }
    this._pendingRpc.clear();
  }

  /* Risposta a una ui_query del server (tool ui_view): il discriminatore
     client→server è `type`. Con `error` set, `payload` viene omesso. */
  sendUiResult(correlationId, payload, error = null) {
    if (!this.chatWs || this.chatWs.readyState !== WebSocket.OPEN) return false;
    const body = { type: 'ui_result', correlation_id: correlationId };
    if (error) body.error = error; else body.payload = payload;
    this.chatWs.send(JSON.stringify(body));
    return true;
  }

}

export const wsManager = new WebSocketManager();
