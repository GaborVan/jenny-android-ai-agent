/** "Modalità avanzata" — mostra o nasconde skill/file marcati come `internal`.
 *
 * Stato puramente client-side (localStorage), come tema/lingua: non passa mai
 * dal backend. Il server annota ogni skill/file con un flag `internal` sempre
 * presente; questo modulo decide solo se il client li mostra o li filtra.
 */

const KEY = 'jenny-advanced-mode';

export function advancedMode() {
  return localStorage.getItem(KEY) === '1';
}

export function setAdvancedMode(on) {
  localStorage.setItem(KEY, on ? '1' : '0');
  window.dispatchEvent(new CustomEvent('advancedmodechange', { detail: { on } }));
  return on;
}
