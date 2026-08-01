/** Shared Application State — global reactive state. */

export const AppState = {
  // Current view/mode
  currentMode: 'chat',

  // Theme (the boot script in index.html migrates legacy values first)
  theme: localStorage.getItem('tc-theme') || 'chanel',

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
