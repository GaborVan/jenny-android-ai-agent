/** Flusso nativo della cloud sync (Drive) — usato dalla card Settings.
 *
 * Il lato nativo (MainActivity) espone su window.JennyNative:
 *   pickDriveSyncFolder() → picker SAF OpenDocumentTree
 * e risponde in modo asincrono chiamando
 * window.jennyDriveSync.onFolderPicked(ok, name) via evaluateJavascript.
 * Stessa disciplina di backup-flow.js: un solo flusso di picking alla volta.
 */

const _pending = { pick: null };
let _busy = false;

window.jennyDriveSync = {
  onFolderPicked(ok, name) {
    const cb = _pending.pick; _pending.pick = null;
    if (cb) cb(!!ok, name || '');
  },
};

export function driveSyncNativeAvailable() {
  return !!(window.JennyNative && window.JennyNative.pickDriveSyncFolder);
}

/** Apre il picker di cartella; risolve `{ok, name}`. Un picking già in corso
 *  (doppio tap) risolve subito `{ok:false, name:''}`. */
export function pickDriveSyncFolder() {
  if (_busy) return Promise.resolve({ ok: false, name: '' });
  if (!driveSyncNativeAvailable()) return Promise.resolve({ ok: false, name: '' });
  _busy = true;
  return new Promise((resolve) => {
    _pending.pick = (ok, name) => { _busy = false; resolve({ ok, name }); };
    window.JennyNative.pickDriveSyncFolder();
  });
}
