/** Mobile Launcher — il foglio che sale dal composer.
 *
 *  Passi 1 e 2 del piano `.agent/apps-drawer-plan.md`: l'impianto di
 *  navigazione, e dentro le tre liste vere. Restano fuori la ricerca e il
 *  ranking (passo 3), la tastiera (passo 4) e la geometria (passo 5).
 *
 *  Il foglio non è una vista: non sta in `controllerFactories`, non ha un
 *  `view-*` e non tocca la history. È un **livello** di
 *  `MobileApp._overlayLayers()`, fra `miniapp` e `drawer` (D3), ed è da lì che
 *  eredita gratis il tasto Indietro, Home e la guardia del type-ahead della
 *  chat.
 *
 *  **Non possiede i dati** (D5). Skill, Jenny App e app Android stanno in
 *  `AppsController`, che ha già il ricaricamento con annuncio delle rimozioni,
 *  l'elenco delle nascoste, `onPackageChanged` e l'ascolto dei frame
 *  `apps_list_changed` / `app_data_changed`. Qui si legge da lì e ci si iscrive
 *  ai suoi cambi: una seconda macchina di ricarica sarebbe una seconda verità
 *  da tenere allineata, e si scoprirebbe disallineata proprio nei casi che il
 *  cassetto deve servire bene (una app disinstallata mentre il foglio è aperto).
 */

import { i18n } from './shared/i18n.js';

/* Etichetta del tipo, a destra della riga. Chiavi proprie del cassetto e non
   quelle della scheda: lì i titoli sono intestazioni di sezione (plurali,
   "Jenny Apps"), qui qualificano una voce sola. */
const KIND_LABEL_KEYS = {
  skill: 'launcher.kindSkill',
  jenny: 'launcher.kindJennyApp',
  android: 'launcher.kindAndroidApp',
};

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
    // Il proprietario dei dati, agganciato alla prima apertura e mai più
    // lasciato: l'iscrizione ai suoi cambi deve sopravvivere alla chiusura del
    // foglio, altrimenti riaprirlo mostrerebbe l'elenco di quando si è chiuso.
    this._apps = null;

    if (!this.sheet) return;

    this.closeBtn = document.getElementById('launcher-close');
    this.closeBtn?.addEventListener('click', () => this.close());
    this.scrim?.addEventListener('click', () => this.close());

    this.trigger = document.getElementById('btn-launcher');
    this.trigger?.addEventListener('click', () => this.app.openLauncher());

    /* I nomi dei tipi cambiano con la lingua, e questa lista la costruisce JS:
       `_applyStaticTranslations()` passa sui `data-i18n` che *sono già in
       pagina*, quindi copre le righe esistenti ma non quelle che nasceranno
       dopo. Ridisegnare al cambio di lingua le copre entrambe. Il primo disegno
       non è qui ma in `open()`: al boot le traduzioni non sono ancora arrivate
       (`i18n.load` è asincrona) e disegnare adesso vorrebbe dire scrivere le
       chiavi grezze in un foglio che nessuno sta guardando. */
    i18n.onLocaleChange(() => this._render());
  }

  /** Il foglio è a schermo e reattivo? Letto da `_overlayLayers().present`. */
  isOpen() {
    return this._open;
  }

  open() {
    if (!this.sheet || this._open) return;
    this._open = true;
    this._lastFocus = document.activeElement;
    // Prima di mostrarlo: la lista è già quella giusta quando il foglio arriva
    // a fine corsa, e non c'è un fotogramma con dentro l'elenco di ieri.
    this._attachSource();
    this._render();
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

  /** Aggancia la sorgente dei dati (D5) alla prima apertura, e ci resta.
   *
   *  Non nel costruttore: `AppsController` fa quattro fetch, e quella delle app
   *  Android ricodifica ogni icona in base64. Farle al boot per un foglio che
   *  potrebbe non aprirsi mai è un costo che si paga sempre e serve a volte.
   *  Da qui in poi però l'iscrizione non si scioglie più — v. `this._apps`.
   */
  _attachSource() {
    if (this._apps) {
      // Aperture successive: la scheda App può essere stata visitata e lasciata
      // nel frattempo, e `deactivate()` invalida le Jenny App di proposito.
      this._apps.ensureLoaded();
      return;
    }
    const apps = this.app.appsController?.();
    if (!apps) return;
    this._apps = apps;
    apps.addChangeListener(() => this._onDataChanged());
    apps.ensureLoaded();
  }

  /** Una delle tre liste è cambiata: un `apps_list_changed` dal gateway, un
   *  pacchetto annunciato dal PackageManager, o una fetch appena tornata.
   *
   *  La lista si riscrive **sul posto**: il foglio non si chiude e non si
   *  rimonta. A foglio chiuso non si disegna niente — riaprirlo ridisegna
   *  comunque, e ridipingere un elenco che nessuno vede è lavoro sul thread
   *  principale sottratto a chi invece si sta guardando una risposta arrivare.
   */
  _onDataChanged() {
    if (this._open) this._render();
  }

  /** Le tre categorie vere, una riga per voce.
   *
   *  Righe e non celle: la lista definitiva è del passo 3 — con descrizione,
   *  ricerca e ordine per pertinenza — ma la forma è già questa, icona a
   *  sinistra e tipo a destra. Le voci non sono ancora attivabili: l'avvio
   *  arriva col passo 4, insieme ai tasti che lo scelgono.
   */
  _render() {
    if (!this.list) return;
    const apps = this._apps;
    const entries = apps ? apps.launcherEntries() : [];
    this.list.innerHTML = '';
    if (!entries.length) {
      const note = document.createElement('p');
      note.className = 'launcher-note';
      // Due stati vuoti diversi, non uno solo: "non c'è niente" e "non è
      // ancora arrivato niente" sono la stessa schermata nella scheda di oggi,
      // ed è un difetto noto (v. 6.2). Qui si parte già distinti.
      const key = (!apps || apps.isLoadingLists()) ? 'launcher.loading' : 'launcher.empty';
      note.dataset.i18n = key;
      note.textContent = i18n.t(key);
      this.list.appendChild(note);
      return;
    }
    // Un frammento solo: la lista può avere qualche centinaio di voci (le app
    // Android di un telefono vero), e appenderle una a una farebbe altrettanti
    // ricalcoli di layout dentro un foglio che sta scivolando su.
    const frag = document.createDocumentFragment();
    for (const entry of entries) frag.appendChild(this._buildRow(entry));
    this.list.appendChild(frag);
  }

  /** Una riga. Costruita nel DOM, non concatenando HTML: i nomi delle app
   *  arrivano dal PackageManager e dai manifest, cioè da testo che non
   *  scriviamo noi. */
  _buildRow(entry) {
    const row = document.createElement('div');
    row.className = 'launcher-row';
    row.dataset.key = entry.key;

    const iconWrap = document.createElement('div');
    iconWrap.className = 'launcher-row-icon';
    /* Le app Android hanno l'icona vera, che il gateway consegna come data URL
       base64; skill e Jenny App un glifo Tabler. Il prefisso si controlla
       perché `src` accetta anche schemi che eseguono, e questo valore ha fatto
       un giro fuori dal nostro codice. */
    if (entry.icon && entry.icon.startsWith('data:image/')) {
      const img = document.createElement('img');
      img.src = entry.icon;
      img.alt = '';
      iconWrap.appendChild(img);
    } else {
      const glyph = document.createElement('i');
      glyph.className = `ti ${entry.glyph || 'ti-apps'}`;
      iconWrap.appendChild(glyph);
    }
    row.appendChild(iconWrap);

    const text = document.createElement('div');
    text.className = 'launcher-row-text';
    const name = document.createElement('span');
    name.className = 'launcher-row-name';
    name.textContent = entry.name;
    text.appendChild(name);
    row.appendChild(text);

    const kind = document.createElement('span');
    kind.className = 'launcher-row-kind';
    const kindKey = KIND_LABEL_KEYS[entry.kind];
    if (kindKey) {
      kind.dataset.i18n = kindKey;
      kind.textContent = i18n.t(kindKey);
    }
    row.appendChild(kind);
    return row;
  }
}
