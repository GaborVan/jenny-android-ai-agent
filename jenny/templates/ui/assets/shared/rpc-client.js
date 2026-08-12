/** Comandi con payload verso il gateway (RPC sul WebSocket).
 *
 *  Gemello di `api-client.js` e divisione del lavoro precisa:
 *
 *    - `api`  → letture e operazioni con parametri corti, su /api/ (HTTP GET);
 *    - `rpc`  → operazioni che portano *contenuto* (il testo di un file, una
 *               nota libera), sul WebSocket.
 *
 *  Il motivo non è stilistico. La superficie /api/ del gateway è servita
 *  dall'hook di handshake di `websockets`, che non legge mai il body di una
 *  richiesta: i parametri possono viaggiare solo nella query string o negli
 *  header, dove stanno 8192 byte per riga e solo caratteri ISO-8859-1 —
 *  `new Headers()` rifiuta un'emoji prima ancora di spedire. Salvare `SOUL.md`
 *  da lì era impossibile. Un frame WebSocket invece è framed e UTF-8.
 *
 *  Ogni metodo qui corrisponde a un comando in `jenny/webui/commands.py`.
 */

import { wsManager } from './ws-manager.js';

export const rpc = {
  /** Salva un file di testo del workspace (tetto 1 MB, lato server). */
  writeWorkspaceFile(path, content) {
    return wsManager.request('workspace.write', { path, content });
  },

  /** Chiude un item di audit con una nota di risoluzione. */
  resolveAudit(auditId, wiki, resolution) {
    return wsManager.request('audit.resolve', {
      audit_id: auditId, wiki, resolution,
    });
  },
};
