/** Preferenze della mascotte (JennyCompanion) — visibilità, aspetto e lato.
 *
 * Stato puramente client-side (localStorage), come tema/lingua/modalità
 * avanzata: non passa mai dal backend. Visibilità, taglia e colore sono
 * scelte dell'utente (Impostazioni → Personalizzazione); il lato invece non
 * è più un'impostazione ma il ricordo di dove l'hai lasciata: lo scrive la
 * companion quando lei atterra dopo un lancio (v. mobile-jenny.js#settle).
 */

const VISIBLE_KEY = 'jenny-mascotte-visible';
/* Chiave nuova rispetto a 'jenny-mascotte-side': il vecchio valore era una
   preferenza esplicita, e chi aveva scelto "destra" se la ritroverebbe come
   posizione di partenza di una feature che quella scelta non ce l'ha più.
   Ripartono tutti da sinistra; la chiave morta si ripulisce sotto. */
const SIDE_KEY = 'jenny-mascotte-dock-side';
const LEGACY_SIDE_KEY = 'jenny-mascotte-side';
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
  try {
    localStorage.removeItem(LEGACY_SIDE_KEY);
  } catch (_) {
    /* storage non disponibile */
  }
  const s = localStorage.getItem(SIDE_KEY);
  return s === 'right' ? 'right' : 'left'; // default: sinistra
}

/* Diversamente dalle altre preferenze NON emette 'mascotchange': lo scrive la
   companion mentre lei sta atterrando, e l'evento la farebbe passare da
   _applyMascotPrefs -> setMode -> _abortFlight, cioè ucciderebbe il volo
   nell'istante esatto in cui sceglie il bordo. La classe .side-left la
   applica direttamente chi chiama (v. mobile-jenny.js#_setSide). */
export function setMascotSide(side) {
  const normalized = side === 'right' ? 'right' : 'left';
  localStorage.setItem(SIDE_KEY, normalized);
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
