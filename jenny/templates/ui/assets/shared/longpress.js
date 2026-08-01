/** Long-press helper — pointer-based, works for touch and mouse.
 *
 * Fires `callback(event)` after `delay` ms of a held press without movement,
 * and sets `el.dataset.longpress = 'true'` so a companion `click` handler can
 * detect and skip the tap that follows the press. Extracted from the workspace
 * file-tree context-menu implementation so multiple views can share it.
 */
export function setupLongPress(el, callback, delay = 600) {
  let timer = null;
  let startX = 0;
  let startY = 0;
  const MOVE_THRESHOLD = 10; // px — ignore finger micro-jitter during the hold
  const start = (e) => {
    if (e.button !== undefined && e.button !== 0 && e.type !== 'touchstart') return;
    startX = e.clientX;
    startY = e.clientY;
    timer = setTimeout(() => {
      el.dataset.longpress = 'true';
      callback(e);
    }, delay);
  };
  const cancel = () => {
    if (timer) { clearTimeout(timer); timer = null; }
  };
  const onMove = (e) => {
    if (!timer) return;
    if (Math.abs(e.clientX - startX) > MOVE_THRESHOLD ||
        Math.abs(e.clientY - startY) > MOVE_THRESHOLD) {
      cancel();
    }
  };
  el.addEventListener('pointerdown', start);
  el.addEventListener('pointerup', cancel);
  el.addEventListener('pointermove', onMove);
  el.addEventListener('pointerleave', cancel);
  el.addEventListener('pointercancel', cancel);
}
