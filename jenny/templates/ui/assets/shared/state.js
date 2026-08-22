/** Shared Application State — global reactive state. */

export const AppState = {
  // Current view/mode
  currentMode: 'chat',

  // Theme (the boot script in index.html migrates legacy values first)
  theme: localStorage.getItem('tc-theme') || 'chanel',

  // Il progetto aperto, o `null` nella conversazione personale. Lo scrive
  // soltanto lo scope chip (`_publishPin`), che è l'unico a saperlo; lo
  // leggono le viste wiki e grafo per mostrare quel progetto e non gli altri.
  pinnedWiki: null,

  // Se il prossimo messaggio parte in sola lettura. Lo scrive soltanto
  // `write-switch.js`; lo leggono il placeholder del composer e `ws-manager`,
  // che lo mette nell'envelope di ogni invio.
  readonlyTurn: false,

  // State change listeners
  _listeners: new Map(),

  on(key, callback) {
    if (!this._listeners.has(key)) this._listeners.set(key, []);
    this._listeners.get(key).push(callback);
  },

  set(key, value) {
    const oldValue = this[key];
    this[key] = value;
    if (this._listeners.has(key)) {
      this._listeners.get(key).forEach(cb => cb(value, oldValue));
    }
  }
};
