/** Preferenze della mascotte (JennyCompanion) — visibilità e lato dello schermo.
 *
 * Stato puramente client-side (localStorage), come tema/lingua/modalità
 * avanzata: non passa mai dal backend. Default = comportamento attuale
 * (mascotte visibile, ancorata a destra), così chi non tocca queste
 * impostazioni non nota alcuna differenza.
 */

const VISIBLE_KEY = 'jenny-mascotte-visible';
const SIDE_KEY = 'jenny-mascotte-side';
const COLOR_KEY = 'jenny-mascotte-color';
const SIZE_KEY = 'jenny-mascotte-size';

/** Lato del canvas quadrato per ogni taglia. 'md' è il valore storico e resta
 *  il default; la geometria in mobile-style.css deriva tutta da --jenny-size,
 *  quindi qui basta scrivere il pixel. */
export const MASCOT_SIZES = { sm: 120, md: 160, lg: 210 };

export function mascotVisible() {
  const v = localStorage.getItem(VISIBLE_KEY);
  if (v === null) return true; // default: visibile
  return v === '1';
}

export function setMascotVisible(on) {
  localStorage.setItem(VISIBLE_KEY, on ? '1' : '0');
  window.dispatchEvent(new CustomEvent('mascotchange', {
    detail: { visible: on, side: mascotSide(), color: mascotColor() },
  }));
  return on;
}

export function mascotSide() {
  const s = localStorage.getItem(SIDE_KEY);
  return s === 'left' ? 'left' : 'right'; // default: destra
}

export function setMascotSide(side) {
  const normalized = side === 'left' ? 'left' : 'right';
  localStorage.setItem(SIDE_KEY, normalized);
  window.dispatchEvent(new CustomEvent('mascotchange', {
    detail: { visible: mascotVisible(), side: normalized, color: mascotColor() },
  }));
  return normalized;
}

export function mascotColor() {
  const c = localStorage.getItem(COLOR_KEY);
  if (c === null) return true; // default: a colori
  return c === '1';
}

export function setMascotColor(on) {
  localStorage.setItem(COLOR_KEY, on ? '1' : '0');
  window.dispatchEvent(new CustomEvent('mascotchange', {
    detail: { visible: mascotVisible(), side: mascotSide(), color: !!on },
  }));
  return !!on;
}

export function mascotSize() {
  const s = localStorage.getItem(SIZE_KEY);
  return s in MASCOT_SIZES ? s : 'md'; // default: media (comportamento storico)
}

export function setMascotSize(size) {
  const normalized = size in MASCOT_SIZES ? size : 'md';
  localStorage.setItem(SIZE_KEY, normalized);
  applyMascotSize();
  window.dispatchEvent(new CustomEvent('mascotchange', {
    detail: {
      visible: mascotVisible(), side: mascotSide(), color: mascotColor(), size: normalized,
    },
  }));
  return normalized;
}

/** Scrive la taglia attiva su <html> come --jenny-size. Da chiamare anche
 *  all'avvio: il default CSS copre solo 'md'. */
export function applyMascotSize() {
  document.documentElement.style.setProperty(
    '--jenny-size', `${MASCOT_SIZES[mascotSize()]}px`
  );
}

/** Rimappa il path base di una posa (jenny-<name>.webp) alla variante attiva.
 *
 * In colore inserisce il suffisso `-color` prima di `.webp`
 * (jenny-idle.webp -> jenny-idle-color.webp); in bianco/nero lascia il path
 * invariato. Ogni assegnazione `img.src` delle pose passa di qui, così lo
 * switch e' un semplice re-render della posa corrente. */
export function poseUrl(baseUrl) {
  if (!mascotColor()) return baseUrl;
  return baseUrl.replace(/\.webp$/, '-color.webp');
}
