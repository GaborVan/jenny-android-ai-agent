/** Mobile Launcher — il foglio che sale dal composer.
 *
 *  Passo 1 del piano `.agent/apps-drawer-plan.md`: qui c'è solo l'impianto di
 *  navigazione, e dentro contenuto finto. I dati veri (passo 2) e la lista
 *  digitabile (passo 3) arrivano dopo, e arrivano *in* questo scheletro: se lo
 *  scheletro non si comporta bene, il contenuto è tempo buttato.
 *
 *  Il foglio non è una vista: non sta in `controllerFactories`, non ha un
 *  `view-*` e non tocca la history. È un **livello** di
 *  `MobileApp._overlayLayers()`, fra `miniapp` e `drawer` (D3), ed è da lì che
 *  eredita gratis il tasto Indietro, Home e la guardia del type-ahead della
 *  chat.
 */

import { i18n } from './shared/i18n.js';

export class LauncherController {
  /** @param {object} app istanza di MobileApp (per il ritorno del fuoco). */
  constructor(app) {
    this.app = app;
    this.sheet = document.getElementById('launcher-sheet');
    this.scrim = document.getElementById('launcher-scrim');
    this.list = document.getElementById('launcher-list');

    /* Verità unica sullo stato del foglio, e la ragione per cui esiste invece
       di interrogare il DOM: `present()` deve diventare falso *nell'istante*
       in cui si chiude, non a transizione finita. `_dismissAllOverlays()`
       richiama il livello fino a otto volte finché `present()` è vero — e un
       foglio che scivola giù per 320 ms resterebbe nel DOM, e quindi
       "presente", per tutte e otto. Un flag scritto sincronamente da close()
       chiude quel ciclo alla prima iterazione. */
    this._open = false;
    // Chi aveva il fuoco all'apertura: ci torna alla chiusura (idioma di
    // DrawerManager._releaseContent).
    this._lastFocus = null;

    if (!this.sheet) return;

    this.closeBtn = document.getElementById('launcher-close');
    this.closeBtn?.addEventListener('click', () => this.close());
    this.scrim?.addEventListener('click', () => this.close());

    this.trigger = document.getElementById('btn-launcher');
    this.trigger?.addEventListener('click', () => this.app.openLauncher());

    this._renderPlaceholder();
  }

  /** Il foglio è a schermo e reattivo? Letto da `_overlayLayers().present`. */
  isOpen() {
    return this._open;
  }

  open() {
    if (!this.sheet || this._open) return;
    this._open = true;
    this._lastFocus = document.activeElement;
    this.sheet.classList.add('open');
    this.sheet.setAttribute('aria-hidden', 'false');
    this.scrim?.classList.add('open');
    this.trigger?.setAttribute('aria-expanded', 'true');
    // Lo sfondo diventa inerte in un colpo solo: niente fuoco, niente tap,
    // niente lettura TalkBack. Il foglio vive *fuori* da `.app` (in fondo al
    // <body>, come .app-frame-overlay) proprio perché questa riga possa essere
    // una riga sola — e perché una mini-app aperta sopra di esso resti viva.
    this._setBackgroundInert(true);
    // Il fuoco entra nel foglio, altrimenti Tab ripartirebbe da dentro il
    // contenuto appena reso inerte, cioè da nessuna parte. Sul *campo* di
    // ricerca non ci va: alzerebbe la tastiera software e si mangerebbe il
    // foglio (D6, type-ahead invece di autofocus). Il campo arriva al passo 3.
    this.closeBtn?.focus?.();
  }

  /** Chiusura completa. Idempotente: Home la chiama comunque. */
  close() {
    if (!this.sheet || !this._open) return;
    this._open = false;
    this.sheet.classList.remove('open');
    this.sheet.setAttribute('aria-hidden', 'true');
    this.scrim?.classList.remove('open');
    this.trigger?.setAttribute('aria-expanded', 'false');
    this._setBackgroundInert(false);
    const previous = this._lastFocus;
    this._lastFocus = null;
    // La vista sotto può essere stata ridisegnata mentre il foglio era aperto:
    // in quel caso non si sposta niente.
    if (previous && previous.isConnected) previous.focus?.();
  }

  /** Semantica del tasto Indietro. Oggi coincide con la chiusura; al passo 4
   *  Esc pulirà prima il campo e chiuderà solo se già vuoto. */
  dismiss() {
    this.close();
  }

  /** Home / cambio vista: il foglio non ha sotto-stati da collassare. */
  collapseToRoot() {
    this.close();
  }

  _setBackgroundInert(on) {
    const shell = document.getElementById('app');
    if (shell) shell.inert = on;
  }

  /** Contenuto finto del passo 1 — quattro righe statiche.
   *
   *  Serve a dare al foglio un'altezza e uno scorrimento veri mentre si
   *  verificano Indietro, Home e il carosello. Sparisce col passo 2, quando
   *  `AppsController` diventa la sorgente. I testi passano comunque da i18n:
   *  la convenzione del progetto non ammette eccezioni per il provvisorio.
   */
  _renderPlaceholder() {
    if (!this.list) return;
    const rows = [
      { name: 'launcher.demoName1', desc: 'launcher.demoDesc1', kind: 'apps.skill' },
      { name: 'launcher.demoName2', desc: 'launcher.demoDesc2', kind: 'apps.jennyApps' },
      { name: 'launcher.demoName3', desc: 'launcher.demoDesc3', kind: 'apps.androidApp' },
      { name: 'launcher.demoName4', desc: 'launcher.demoDesc4', kind: 'apps.androidApp' },
    ];
    this.list.innerHTML = '';
    for (const row of rows) {
      const el = document.createElement('div');
      el.className = 'launcher-row';
      el.innerHTML = `
        <div class="launcher-row-text">
          <span class="launcher-row-name" data-i18n="${row.name}"></span>
          <span class="launcher-row-desc" data-i18n="${row.desc}"></span>
        </div>
        <span class="launcher-row-kind" data-i18n="${row.kind}"></span>
      `;
      this.list.appendChild(el);
    }
    const note = document.createElement('p');
    note.className = 'launcher-note';
    note.dataset.i18n = 'launcher.demoNote';
    this.list.appendChild(note);
    // Le righe nascono dopo `_applyStaticTranslations()` del boot, quindi il
    // testo se lo scrivono da sé; ai cambi di lingua ci ripensa quella.
    this.list.querySelectorAll('[data-i18n]').forEach((el) => {
      el.textContent = i18n.t(el.dataset.i18n);
    });
  }
}
