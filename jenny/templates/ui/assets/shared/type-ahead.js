/** Type-ahead — la guardia che decide se un tasto "nel vuoto" è testo.
 *
 *  Estratta da `ChatController._maybeTypeAheadFocus`, dove era stata tarata su
 *  questo hardware (Titan 2, tastiera fisica via bbkeyboard) e dove restava
 *  l'unica copia. Il cassetto ha lo stesso bisogno — il primo carattere mette a
 *  fuoco il campo di ricerca invece di andare perso (D6: nessun autofocus, che
 *  alzerebbe la tastiera software) — e riscriverla a occhio avrebbe prodotto
 *  una seconda versione destinata a divergere sul caso raro, che qui è il caso
 *  vero: il `keydown` con `key` undefined delle tastiere fisiche.
 *
 *  Il modulo è **puro**: `activeElement` si passa, non si legge da
 *  `document`. È quello che lo rende eseguibile sotto node senza un DOM finto,
 *  come `launcher-rank.js` — v. `tests/webui/test_type_ahead_client.py`.
 *
 *  Fuori di qui resta una sola decisione, ed è giusto che sia del chiamante:
 *  *chi* ha diritto ai tasti quando ci sono più livelli a schermo. La chat
 *  cede a qualunque overlay, il cassetto solo a quelli sopra di sé.
 */

/** Il tasto `e` va inteso come inizio di una digitazione?
 *
 *  @param {KeyboardEvent} e evento `keydown`.
 *  @param {Element|null} activeElement chi ha il fuoco adesso
 *         (`document.activeElement` dal chiamante).
 *  @returns {boolean}
 */
export function isTypeAheadKey(e, activeElement) {
  if (!e) return false;
  // Solo caratteri stampabili singoli. Le tastiere fisiche (Titan via
  // bbkeyboard) possono emettere keydown con `e.key` undefined: il guard
  // length===1 li scarta, come pure Enter/Escape/frecce/ecc.
  if (!e.key || e.key.length !== 1) return false;
  // Lo spazio è escluso: non si inizia mai a scrivere con uno spazio (verrebbe
  // comunque trimmato) ed è riservato a interazioni future con la mascotte.
  // Nel cassetto ha in più un uso proprio — attiva la riga che ha il fuoco —
  // e rubarlo al campo di ricerca lo renderebbe inutilizzabile lì.
  if (e.key === ' ') return false;
  // I combo con modificatori sono scorciatoie, non testo.
  if (e.metaKey || e.ctrlKey || e.altKey) return false;
  // Non rubare il fuoco se si sta già scrivendo altrove.
  const el = activeElement;
  if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' ||
             el.tagName === 'SELECT' || el.isContentEditable)) return false;
  return true;
}
