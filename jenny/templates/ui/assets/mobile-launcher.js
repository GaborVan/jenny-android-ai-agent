/** Mobile Launcher — il foglio che sale dal composer.
 *
 *  Passi 1, 2 e 3 del piano `.agent/apps-drawer-plan.md`: l'impianto di
 *  navigazione, le tre liste vere, e il campo di ricerca con sotto la lista
 *  ordinata e attivabile col tocco. Restano fuori i tasti (passo 4: ⏎, frecce,
 *  rotella, type-ahead) e la geometria (passo 5).
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
import { UsageRanking, rankEntries } from './shared/launcher-rank.js';

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
    this.search = document.getElementById('launcher-search');
    this.titleEl = document.getElementById('launcher-title');
    this.clearBtn = document.getElementById('launcher-search-clear');

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
    // Le voci come le consegna `launcherEntries()`, non ordinate per la query:
    // riordinarle e filtrarle è lavoro di `_renderList()`, che gira a ogni
    // tasto, mentre questa si rinfresca solo quando i dati cambiano davvero.
    this._entries = [];
    /* Le righe già costruite, per chiave. È **il** motivo per cui digitare non
       costa: il difetto 07 del rilievo è che la scheda ricostruisce 68 celle e
       ridecodifica 47 icone base64 a ogni carattere, e ricostruire qui la riga
       a ogni tasto lo riprodurrebbe identico. Le righe si costruiscono una
       volta, e la ricerca si limita a rimetterle in fila: un nodo `<img>` che
       resta vivo con la stessa `src` non si ridecodifica.
       Valore: `{ sig, el }` — `sig` è la firma del contenuto, così una voce che
       cambia (una app che si rompe, un nome che cambia lingua) si riconosce e
       si ricostruisce, e solo lei. */
    this._rows = new Map();
    // Frequenza e recenza per chiave (D9). Costruito qui e non alla prima
    // apertura: leggere una riga di localStorage costa meno di decidere se
    // leggerla, e il ranking serve già al primo disegno.
    this._usage = new UsageRanking(window.localStorage);

    if (!this.sheet) return;

    this.closeBtn = document.getElementById('launcher-close');
    this.closeBtn?.addEventListener('click', () => this.close());
    this.scrim?.addEventListener('click', () => this.close());

    this.trigger = document.getElementById('btn-launcher');
    this.trigger?.addEventListener('click', () => this.app.openLauncher());

    this.search?.addEventListener('input', () => this._onQueryChanged());
    this.clearBtn?.addEventListener('click', () => {
      this.search.value = '';
      this._onQueryChanged();
      this.search.focus();
    });

    /* Attivazione: un solo ascoltatore sulla lista, non uno per riga.
       Le righe sono centinaia e si rimettono in fila a ogni tasto; appenderci
       un listener ciascuna li moltiplicherebbe per il numero di ricostruzioni.
       Il `click` sintetizzato da Invio/Spazio passa di qui come il tocco, così
       l'attivazione resta un percorso solo (stessa scelta di `wireEvents`). */
    this.list?.addEventListener('click', (e) => {
      const row = e.target.closest?.('.launcher-row');
      if (row?.dataset.key) this._activate(row.dataset.key);
    });

    /* I nomi dei tipi cambiano con la lingua, e questa lista la costruisce JS:
       `_applyStaticTranslations()` passa sui `data-i18n` che *sono già in
       pagina*, quindi copre le righe esistenti ma non quelle che nasceranno
       dopo. Ridisegnare al cambio di lingua le copre entrambe. Il primo disegno
       non è qui ma in `open()`: al boot le traduzioni non sono ancora arrivate
       (`i18n.load` è asincrona) e disegnare adesso vorrebbe dire scrivere le
       chiavi grezze in un foglio che nessuno sta guardando. */
    i18n.onLocaleChange(() => {
      // Le righe portano dentro testo tradotto (il tipo, l'errore di un
      // manifest rotto): la cache va buttata, non riordinata.
      this._rows.clear();
      this._render();
    });
  }

  /** Il foglio è a schermo e reattivo? Letto da `_overlayLayers().present`. */
  isOpen() {
    return this._open;
  }

  open() {
    if (!this.sheet || this._open) return;
    this._open = true;
    this._lastFocus = document.activeElement;
    /* Ogni apertura riparte dal campo vuoto. Ritrovare la query di ieri
       vorrebbe dire aprire il cassetto su tre voci su settanta senza aver
       chiesto niente — e il costo di ricominciare è una parola, mentre il costo
       di non capire perché manca tutto è un cassetto che sembra rotto. */
    if (this.search) this.search.value = '';
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
    // foglio (D6, type-ahead invece di autofocus — il type-ahead è del passo 4).
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

  /** I dati sono cambiati: si rilegge la lista, si aggiorna la cache delle
   *  righe e si ridisegna. È il percorso *costoso*, e per questo non è quello
   *  che gira quando si digita — v. `_onQueryChanged`. */
  _render() {
    this._syncEntries();
    this._renderList();
  }

  /** Rilegge `launcherEntries()` e riallinea la cache delle righe.
   *
   *  Una riga si ricostruisce solo se il suo contenuto è cambiato davvero: la
   *  firma tiene tutto ciò che finisce nel DOM. Senza il confronto, ogni
   *  `apps_list_changed` — che arriva a ogni turno dell'agente in cui
   *  `workspace/apps/` si è mossa — butterebbe via le 47 icone base64 già
   *  decodificate per riscriverle identiche.
   */
  _syncEntries() {
    this._entries = this._apps ? this._apps.launcherEntries() : [];
    const next = new Map();
    for (const entry of this._entries) {
      /* Separatore NUL *scritto come escape*, non incollato: un byte zero nel
         sorgente rende il file binario per grep, e la ricerca fallisce in
         silenzio (gotcha noto del progetto su `mobile-chat.js`). Serve perché
         uno spazio darebbe la stessa firma a un nome che finisce dove la
         descrizione comincia, e quella riga resterebbe indietro. */
      const sig = [entry.name, entry.description, entry.problem, entry.kind,
        entry.glyph, entry.hasServer].join('\u0000');
      const cached = this._rows.get(entry.key);
      /* L'icona sta fuori dalla firma: è una data URL da decine di kB, e
         concatenarla vorrebbe dire costruire un megabyte di stringhe a ogni
         cambio per confrontare byte che si confrontano benissimo da soli. */
      next.set(entry.key, (cached && cached.sig === sig && cached.icon === entry.icon)
        ? cached
        : { sig, icon: entry.icon, el: this._buildRow(entry) });
    }
    this._rows = next;
  }

  /** Il campo è cambiato: si riordina e si rimette in fila, **senza ricostruire
   *  niente** (difetto 07 del rilievo). Le righe sono già nel DOM: qui si
   *  spostano, e le escluse si staccano restando in cache. */
  _onQueryChanged() {
    this._renderList();
  }

  /** Filtra, ordina e impagina. L'unica funzione che tocca `this.list`.
   *
   *  L'ordine è quello di D9/3.3 — pertinenza, poi frequenza, poi recenza — e
   *  vive in `shared/launcher-rank.js`, che non conosce il DOM ed è provabile
   *  sotto node.
   */
  _renderList() {
    if (!this.list) return;
    const apps = this._apps;
    const query = this.search?.value || '';
    this._syncHeading(query);
    if (!this._entries.length) {
      // Due stati vuoti diversi, non uno solo: "non c'è niente" e "non è
      // ancora arrivato niente" sono la stessa schermata nella scheda di oggi,
      // ed è un difetto noto (v. 6.2). Qui si parte già distinti.
      const key = (!apps || apps.isLoadingLists()) ? 'launcher.loading' : 'launcher.empty';
      this.list.replaceChildren(this._note(key));
      return;
    }
    const ranked = rankEntries(this._entries, query, this._usage, i18n.locale);
    if (!ranked.length) {
      // Terzo stato, distinto dai due di sopra: le voci ci sono, è la query a
      // non trovarle. Dirlo *con dentro la query* è la differenza fra "non c'è"
      // e "non c'è **questo**".
      this.list.replaceChildren(this._note('launcher.noResults', { query: query.trim() }));
      return;
    }
    /* `replaceChildren` con i nodi già esistenti: quelli che restano vengono
       spostati, non ricreati, e quelli fuori dai risultati si staccano ma
       sopravvivono in `this._rows`. Una sola scrittura sul DOM per tasto. */
    this.list.replaceChildren(...ranked.map(entry => this._rows.get(entry.key).el));
    // La lista è stata riordinata sotto il dito: si riparte dall'alto, dove sta
    // il risultato migliore. Senza questo, dopo aver scorso e poi digitato si
    // resterebbe a metà di una lista che nel frattempo si è accorciata.
    this.list.scrollTop = 0;
  }

  /** Il titolo del foglio dice in che ordine si sta guardando: a campo vuoto è
   *  la lista di ciò che si usa ("Recenti", 3.3), appena si digita sono i
   *  risultati di una ricerca. */
  _syncHeading(query) {
    if (!this.titleEl) return;
    const key = query.trim() ? 'launcher.results' : 'launcher.recent';
    this.titleEl.dataset.i18n = key;
    this.titleEl.textContent = i18n.t(key);
    this.clearBtn?.classList.toggle('visible', !!query);
  }

  _note(key, params) {
    const note = document.createElement('p');
    note.className = 'launcher-note';
    // Niente `data-i18n` quando c'è un parametro: `_applyStaticTranslations()`
    // riscriverebbe il nodo con la stringa grezza, `{query}` compreso.
    if (!params) note.dataset.i18n = key;
    note.textContent = i18n.t(key, params);
    return note;
  }

  /** Apre la voce toccata e ne registra l'uso (3.4).
   *
   *  La chiave si rilegge dalla lista corrente e non dal `dataset` soltanto: fra
   *  il tocco e qui la lista può essere cambiata (una app disinstallata mentre
   *  il foglio è aperto è proprio il caso che il passo 2 ha verificato), e
   *  lanciare qualcosa che non c'è più è peggio che non fare niente.
   */
  _activate(key) {
    const entry = this._entries.find(e => e.key === key);
    if (!entry) return;
    /* L'uso si registra **prima** di avviare: `launchAndroidApp` porta via il
       task, e da lì in poi non è detto che questo JS giri ancora. Il costo di
       registrare un avvio poi fallito è una posizione in classifica; il costo
       opposto è un cassetto che non impara mai le app che si usano di più. */
    this._usage.record(entry.key);
    this._apps?.activateEntry(entry);
    /* Una app Android se ne va con tutto il task: il foglio deve chiudersi, o
       al ritorno lo si ritroverebbe aperto sopra la conversazione senza averlo
       chiesto. Le altre due no — una Jenny App si apre *sopra* il foglio e
       Indietro ci riporta (1.7), e una skill o apre un dialog sopra il foglio o
       cambia vista, e allora è `switchMode` a chiuderlo (1.5). */
    if (entry.kind === 'android') this.close();
  }

  /** Una riga. Costruita nel DOM, non concatenando HTML: i nomi delle app
   *  arrivano dal PackageManager e dai manifest, cioè da testo che non
   *  scriviamo noi. */
  _buildRow(entry) {
    const row = document.createElement('div');
    row.className = 'launcher-row';
    row.dataset.key = entry.key;
    /* Semantica giusta dalla nascita, non aggiunta dopo: le celle della scheda
       sono `<div>` a cui `wireEvents` appiccica `tabindex`/`role` a ogni
       ridisegno. Qui la riga *è* un pulsante per TalkBack e per Tab, e il passo
       4 ci troverà già il terreno pronto. */
    row.setAttribute('role', 'button');
    row.setAttribute('tabindex', '0');

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

    /* La seconda riga (difetto 02 e difetto 05 insieme). È **una sola**, e su
       una riga sola: un guasto ha la precedenza sulla descrizione perché è
       quello che spiega cosa fare adesso, e sta *dentro* la riga con
       `text-overflow: ellipsis` invece che in un blocco che si allarga. Nella
       griglia di oggi l'errore dentro la cella alza tutta la riga da 100 a 147
       px — un manifest rotto deforma la pagina di tutti gli altri. */
    const secondary = entry.problem || entry.description;
    if (secondary) {
      const desc = document.createElement('span');
      desc.className = entry.problem
        ? 'launcher-row-desc launcher-row-desc--problem' : 'launcher-row-desc';
      desc.textContent = secondary;
      // Il testo è tagliato dall'ellissi: il titolo lo dà per intero a chi ci
      // resta sopra, e TalkBack lo legge comunque dal contenuto.
      desc.title = secondary;
      text.appendChild(desc);
    }
    row.appendChild(text);

    /* `has_server`: una Jenny App con un backend proprio non è la stessa cosa
       di una che vive nel workspace, e il gateway lo dice già. Un glifo, non
       una parola: la colonna di destra è stretta e il tipo ci sta già. */
    if (entry.hasServer) {
      const server = document.createElement('i');
      server.className = 'ti ti-cloud launcher-row-server';
      server.title = i18n.t('launcher.hasServer');
      row.appendChild(server);
    }

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
