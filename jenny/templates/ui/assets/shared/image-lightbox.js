import { attachPinchZoom } from './pinch-zoom.js';

/**
 * Overlay fullscreen per un'immagine, condiviso tra chat e workspace.
 * Monta overlay + <img> + pulsante di chiusura, gestisce la chiusura su click
 * sullo sfondo e il pinch-zoom (lo zoom del viewport è disabilitato
 * globalmente, quindi il pinch è reimplementato dall'helper). Esc e il tasto
 * Indietro passano entrambi dalla catena dei livelli di MobileApp, che chiude
 * via __jennyClose (v. in fondo).
 *
 * options:
 *   alt           testo alternativo dell'immagine
 *   closeLabel    aria-label del pulsante di chiusura
 *   actions       array di { act, icon, label } → barra .ws-lightbox-actions
 *   onAction      callback (act, close) al click di un'azione della barra
 *   onError       callback se l'immagine non si decodifica (chiude prima)
 *   onClose       callback di cleanup alla chiusura (es. revokeObjectURL)
 * Ritorna la funzione close() per chiudere programmaticamente.
 */
export function openImageLightbox(src, options = {}) {
  if (!src) return () => {};
  const {
    alt = '',
    closeLabel = 'Close',
    actions = null,
    onAction = null,
    onError = null,
    onClose = null,
  } = options;

  const overlay = document.createElement('div');
  overlay.className = 'image-lightbox';
  const img = document.createElement('img');
  img.src = src;
  img.alt = alt;
  img.className = 'image-lightbox-img';
  const closeBtn = document.createElement('button');
  closeBtn.className = 'image-lightbox-close';
  closeBtn.setAttribute('aria-label', closeLabel);
  closeBtn.textContent = '✕';
  overlay.appendChild(img);
  overlay.appendChild(closeBtn);

  let actionsEl = null;
  if (actions && actions.length) {
    actionsEl = document.createElement('div');
    actionsEl.className = 'ws-lightbox-actions';
    actionsEl.innerHTML = actions
      .map((a) => `<button data-act="${a.act}"><i class="ti ${a.icon}"></i>${a.label}</button>`)
      .join('');
    overlay.appendChild(actionsEl);
  }

  // Esc non ha più un listener qui: la chiusura passa dalla catena dei livelli
  // di MobileApp (la shortcut Escape chiama handleHardwareBack, che trova la
  // lightbox al secondo livello e la chiude via __jennyClose). Un secondo
  // listener locale rifarebbe lo stesso lavoro fuori dalla catena, cioè
  // esattamente la divergenza che la catena esiste per eliminare.
  // Chi ha aperto la lightbox: ci torna il fuoco alla chiusura, sempre che sia
  // ancora nel documento (griglia ridisegnata, sezione cambiata → non si
  // sposta niente e il fuoco resta dov'è).
  const previousFocus = document.activeElement;
  const close = () => {
    overlay.remove();
    if (previousFocus && previousFocus.isConnected) previousFocus.focus?.();
    if (onClose) onClose();
  };

  attachPinchZoom(img);
  if (onError) {
    img.addEventListener('error', () => { close(); onError(); });
  }
  if (actionsEl) {
    actionsEl.addEventListener('click', (e) => {
      const btn = e.target.closest('button[data-act]');
      if (!btn) return;
      e.stopPropagation();
      if (onAction) onAction(btn.dataset.act, close);
    });
  }
  closeBtn.addEventListener('click', close);
  overlay.addEventListener('click', close);
  // Appeso all'elemento perché il tasto Indietro di Android arriva alla shell,
  // che conosce solo il DOM: senza questo dovrebbe rimuovere l'overlay a mano,
  // saltando il cleanup (object URL revocati da onClose).
  overlay.__jennyClose = close;
  document.body.appendChild(overlay);
  // Il fuoco entra nell'overlay: ciò che sta sotto non è inerte, quindi senza
  // questo Tab proseguirebbe nella pagina coperta.
  closeBtn.focus();
  return close;
}
