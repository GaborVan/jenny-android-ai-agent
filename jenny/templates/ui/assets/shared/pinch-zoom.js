// Pinch-zoom custom per le immagini della lightbox.
//
// Lo zoom del viewport è disabilitato globalmente (launcher: user-scalable=no
// + setSupportZoom(false) lato nativo), quindi il pinch sulle immagini viene
// reimplementato qui via pointer events, applicando un transform inline
// `translate(tx, ty) scale(s)` alla sola <img>. L'elemento deve avere
// `touch-action: none` (vedi .image-lightbox-img in mobile-style.css).

const MIN_SCALE = 1;
const MAX_SCALE = 4;
const TAP_SCALE = 2;      // scala del toggle al tap singolo
const TAP_SLOP_PX = 10;   // movimento massimo perché un gesto conti come tap
const TAP_TRANSITION = 'transform 0.15s ease-out';

/**
 * Aggancia pinch-zoom, pan e tap-per-zoom a un'immagine di lightbox.
 *
 * - Pinch (2 dita): scala continua in [1, 4] ancorata al punto medio.
 * - Pan (1 dito, solo con scala > 1): trascina l'immagine, con clamp.
 * - Tap singolo: alterna adatta-schermo (1x) ↔ 2x.
 * - I tap/click sull'immagine non risalgono all'overlay (che chiude al tap).
 *
 * Nessun detach necessario: la lightbox rimuove l'intero overlay alla chiusura.
 */
export function attachPinchZoom(img) {
  let scale = 1;
  let tx = 0;
  let ty = 0;

  // Pointer attivi del gesto corrente: pointerId → {x, y}.
  const pointers = new Map();
  let gestureMoved = false;   // il gesto ha superato la soglia di tap?
  let gesturePinched = false; // il gesto ha coinvolto due dita?
  let startX = 0;
  let startY = 0;
  // Stato del pinch in corso.
  let pinchStartDist = 0;
  let pinchStartScale = 1;

  const apply = () => {
    img.style.transform =
      scale === 1 && tx === 0 && ty === 0
        ? ''
        : `translate(${tx}px, ${ty}px) scale(${scale})`;
  };

  // Limita la translate perché l'immagine resti raggiungibile: con
  // transform-origin al centro, il bordo si sposta di (s-1)/2 volte la
  // dimensione di layout.
  const clampPan = () => {
    const maxX = (img.clientWidth * (scale - 1)) / 2;
    const maxY = (img.clientHeight * (scale - 1)) / 2;
    tx = Math.min(maxX, Math.max(-maxX, tx));
    ty = Math.min(maxY, Math.max(-maxY, ty));
  };

  const midpoint = () => {
    const pts = [...pointers.values()];
    return { x: (pts[0].x + pts[1].x) / 2, y: (pts[0].y + pts[1].y) / 2 };
  };
  const distance = () => {
    const pts = [...pointers.values()];
    return Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y);
  };

  img.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    img.setPointerCapture(e.pointerId);
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    img.style.transition = '';
    if (pointers.size === 1) {
      gestureMoved = false;
      gesturePinched = false;
      startX = e.clientX;
      startY = e.clientY;
    } else if (pointers.size === 2) {
      gesturePinched = true;
      pinchStartDist = distance();
      pinchStartScale = scale;
    }
  });

  img.addEventListener('pointermove', (e) => {
    if (!pointers.has(e.pointerId)) return;
    const prev = pointers.get(e.pointerId);
    const prevMid = pointers.size === 2 ? midpoint() : null;
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });

    if (Math.hypot(e.clientX - startX, e.clientY - startY) > TAP_SLOP_PX) {
      gestureMoved = true;
    }

    if (pointers.size === 2 && pinchStartDist > 0) {
      // Pinch: nuova scala ancorata al punto medio (che può anche spostarsi).
      const mid = midpoint();
      const rect = img.getBoundingClientRect();
      const cx = rect.left + rect.width / 2 - tx; // centro di layout, invariante
      const cy = rect.top + rect.height / 2 - ty;
      const newScale = Math.min(
        MAX_SCALE,
        Math.max(MIN_SCALE, pinchStartScale * (distance() / pinchStartDist)),
      );
      const ratio = newScale / scale;
      tx = mid.x - cx - ratio * (prevMid.x - cx - tx);
      ty = mid.y - cy - ratio * (prevMid.y - cy - ty);
      scale = newScale;
      clampPan();
      apply();
    } else if (pointers.size === 1 && scale > 1) {
      // Pan a un dito, solo da zoomati.
      tx += e.clientX - prev.x;
      ty += e.clientY - prev.y;
      clampPan();
      apply();
    }
  });

  const endPointer = (e) => {
    if (!pointers.has(e.pointerId)) return;
    pointers.delete(e.pointerId);
    pinchStartDist = 0;
    if (pointers.size > 0) return;
    // Fine gesto: tap singolo → toggle 1x ↔ 2x (mai dopo pinch o drag).
    if (!gestureMoved && !gesturePinched && e.type === 'pointerup') {
      img.style.transition = TAP_TRANSITION;
      if (scale !== 1) {
        scale = 1;
        tx = 0;
        ty = 0;
      } else {
        scale = TAP_SCALE;
      }
      apply();
    } else if (scale === 1) {
      // Rientro morbido se il pinch è terminato sotto la scala minima.
      img.style.transition = TAP_TRANSITION;
      tx = 0;
      ty = 0;
      apply();
    }
  };
  img.addEventListener('pointerup', endPointer);
  img.addEventListener('pointercancel', endPointer);

  // I click sintetici post-tap non devono chiudere l'overlay della lightbox.
  img.addEventListener('click', (e) => e.stopPropagation());
}
