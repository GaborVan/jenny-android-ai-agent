/** Shared Utilities — pure helper functions. */

export function escapeHtml(text) {
  return String(text ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

export function hashString(s) {
  let h = 0;
  for (let i = 0; i < s.length; i++) {
    h = ((h << 5) - h) + s.charCodeAt(i);
    h |= 0;
  }
  return Math.abs(h);
}

export function getFileExtension(filename) {
  return filename.split('.').pop().toLowerCase();
}

export function showToast(message, type = 'info', duration = 3000) {
  const toast = document.createElement('div');
  toast.className = `mobile-toast ${type}`;
  toast.textContent = message;
  document.body.appendChild(toast);

  setTimeout(() => {
    toast.style.animation = 'toastOut 0.3s ease forwards';
    setTimeout(() => toast.remove(), 300);
  }, duration);
}

/* Carica su richiesta un vendor pesante che serve a una sola vista.
   Mermaid da solo pesa 3,2 MB e index.html lo caricava a ogni avvio anche a
   chi apriva solo la chat. La Promise è memoizzata (non il risultato), così
   due chiamate ravvicinate condividono lo stesso <script> invece di
   iniettarne due. Same-origin: passa sotto la CSP `script-src 'self'`. */
const _vendorLoads = new Map();

export function ensureVendor(src) {
  const cached = _vendorLoads.get(src);
  if (cached) return cached;
  const p = new Promise((resolve, reject) => {
    const el = document.createElement('script');
    el.src = src;
    el.onload = () => resolve();
    el.onerror = () => {
      // Non lasciare in cache un fallimento: un ritentativo (rete assente al
      // primo colpo, asset non ancora estratto) deve poter riprovare.
      _vendorLoads.delete(src);
      reject(new Error(`Failed to load ${src}`));
    };
    document.head.appendChild(el);
  });
  _vendorLoads.set(src, p);
  return p;
}
