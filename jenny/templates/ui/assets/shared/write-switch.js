/** Interruttore scrittura / sola lettura, accanto al chip dello scope.
 *
 * Risponde alla seconda metà della stessa domanda del chip — *cosa farà quel
 * che sto per mandare* — e per questo sta nella stessa riga: un messaggio
 * partito credendolo in sola lettura non si ritira, e l'unico momento in cui
 * l'utente può accorgersene è mentre guarda il composer.
 *
 * **Il flag viaggia nel messaggio, non in uno stato sul server.** Il server non
 * lo tiene da nessuna parte: se lo tenesse potrebbe raccontare al client uno
 * stato diverso da quello con cui il messaggio è partito, che è il solo guasto
 * che conta. Qui si tiene solo la *preferenza*, per conversazione, e la si
 * rimanda a ogni invio.
 *
 * Sola lettura vuol dire «non cambia niente sul telefono»: file, download, dati
 * delle mini-app, memoria, promemoria, aggiornamento dell'app. Restano possibili
 * la risposta e l'invio di messaggi — una chat che non può nemmeno avvisarti non
 * è in sola lettura, è muta.
 */

import { i18n } from './i18n.js';
import { AppState } from './state.js';

export class WriteSwitch {
  constructor() {
    this.el = document.getElementById('write-switch');
    // Chat e onboarding condividono l'index: senza il blocco il modulo non fa
    // nulla invece di sollevare al primo getElementById nullo (come il chip).
    this.enabled = Boolean(this.el);
    // Preferenza per conversazione, in memoria. **Non in localStorage**: la
    // conversazione aperta non sopravvive a un riavvio (`session-manager.js`
    // riparte sempre dalla personale), quindi una preferenza che sopravvive
    // sarebbe una promessa che al riavvio non ha più un soggetto.
    this._byKey = new Map();
    this._key = null;
    // Montata da chi possiede la chat: ridisegnare il composer non è mestiere
    // di questo modulo.
    this.onChange = null;
  }

  init() {
    if (!this.enabled) return;
    this.el.addEventListener('click', (e) => {
      e.stopPropagation();
      this.toggle();
    });
    i18n.onLocaleChange(() => this.render());
    this.render();
  }

  /** La conversazione a cui si riferisce l'interruttore da adesso. */
  syncFromSession(sessionKey) {
    this._key = sessionKey || null;
    this._publish();
    this.render();
  }

  /** `true` se il prossimo messaggio di questa conversazione è in sola lettura. */
  get readonly() {
    return this._byKey.get(this._key) === true;
  }

  toggle() {
    if (!this.enabled) return;
    const next = !this.readonly;
    if (this._key) this._byKey.set(this._key, next);
    this._publish();
    this.render();
    this.onChange?.(next);
  }

  /** Su `AppState` perché il placeholder del composer lo legge da lì. */
  _publish() {
    if (AppState.readonlyTurn === this.readonly) return;
    AppState.set('readonlyTurn', this.readonly);
  }

  render() {
    if (!this.enabled) return;
    const ro = this.readonly;
    this.el.dataset.mode = ro ? 'readonly' : 'write';
    this.el.setAttribute('aria-pressed', ro ? 'true' : 'false');
    const label = i18n.t(ro ? 'write.readonly' : 'write.write');
    this.el.setAttribute('aria-label', i18n.t('write.change'));
    this.el.setAttribute('title', i18n.t(ro ? 'write.readonlyHint' : 'write.writeHint'));
    const mark = this.el.querySelector('.write-switch-mark');
    if (mark) mark.className = 'write-switch-mark ti ' + (ro ? 'ti-eye' : 'ti-pencil');
    const text = this.el.querySelector('.write-switch-label');
    if (text) text.textContent = label;
  }
}

export const writeSwitch = new WriteSwitch();
