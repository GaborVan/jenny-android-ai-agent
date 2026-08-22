/** Scope chip — dice a quale scope va il prossimo messaggio, ed è il modo di cambiarlo.
 *
 * Sta ancorato sopra il composer perché quello è l'unico punto su cui l'occhio è
 * già posato prima di scrivere: la riga d'identità della chat (`.chat-identity`)
 * è il parente più prossimo ma è il primo elemento dell'area di scroll, quindi
 * scorre via. Un messaggio mandato allo scope sbagliato non si ritira, ed è il
 * solo guasto irrecuperabile del disegno delle sessioni-progetto.
 *
 * Scegliere uno scope cambia **davvero** conversazione: la chiave della
 * sessione diventa `project:<nome>`, e da quella il gateway ricava sia la
 * cartella su cui l'agente lavora sia il thread da disegnare. Il chip non manda
 * mai un percorso: manda un nome, e la cartella la deduce il server — così la
 * sessione e la sua cartella non possono divergere.
 *
 * Chi possiede la chat passa :attr:`onSwitch`, che viene chiamata dopo il
 * cambio: ricaricare quel che è a schermo non è mestiere di questo modulo.
 */

import { i18n } from './i18n.js';
import { AppState } from './state.js';
import { api } from './api-client.js';
import { rpc } from './rpc-client.js';
import { showToast } from './utils.js';
import { promptDialog } from './dialog.js';

/** Cartella che ospita i progetti, finche' il backend non dice la sua.
 *
 *  Un progetto **e' una wiki**: non esiste una `projects/` separata, e questo
 *  modulo ne leggeva una che non c'era. Il nome vero arriva da `/api/projects`
 *  (`config.wiki.wikis_dir` e' configurabile); questo e' il valore mostrato nel
 *  frattempo, cioe' il default della config.
 */
const DEFAULT_DIR = 'wikis';

/** Un nome di progetto è un nome di cartella: niente separatori né path. */
const VALID_NAME = /^[A-Za-z0-9._-]+$/;

export class ScopeChip {
  constructor() {
    this.el = document.getElementById('scope-chip');
    this.menu = document.getElementById('scope-menu');
    // Chat e onboarding condividono l'index: se il blocco non c'è, il modulo
    // non fa nulla invece di sollevare al primo getElementById nullo.
    this.enabled = Boolean(this.el && this.menu);
    // Scope corrente: ``null`` come nome significa sessione personale.
    this.scope = { kind: 'personal', name: null };
    this._projects = null;   // cache dell'ultimo elenco letto da disco
    this._dir = DEFAULT_DIR; // nome vero della cartella, dal backend
    this._open = false;
    // Chiamata dopo un cambio di scope, con la nuova chiave di sessione. La
    // monta chi possiede la chat; senza, il chip resta presentazione.
    this.onSwitch = null;
  }

  init() {
    if (!this.enabled) return;
    this.el.addEventListener('click', (e) => {
      e.stopPropagation();
      this.toggle();
    });
    this.menu.addEventListener('click', (e) => e.stopPropagation());
    // Chiusura: tap fuori ed Escape, come gli sheet dell'app.
    document.addEventListener('click', () => this.close());
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') this.close();
    });
    i18n.onLocaleChange(() => {
      this.render();
      if (this._open) this._renderMenu();
    });
    this.render();
  }

  // ── Stato ──────────────────────────────────────────────────────────────

  /** Scope attivo secondo il backend. ``null``/radice ⇒ sessione personale. */
  syncFromSession(workspaceScope) {
    if (!this.enabled) return;
    const path = workspaceScope?.project_path || '';
    // Se il backend non ha ancora risposto il nome e' il default: sbagliarlo
    // vorrebbe dire mostrare "personale" per un attimo, non mandare un
    // messaggio nel posto sbagliato — lo scope mostrato viene comunque
    // rirenderizzato quando l'elenco arriva.
    const marker = `/${this._dir}/`;
    const idx = path.indexOf(marker);
    if (idx === -1) {
      this.scope = { kind: 'personal', name: null };
    } else {
      const rest = path.slice(idx + marker.length).replace(/\/+$/, '');
      // Solo il primo segmento: uno scope più profondo di così non è un
      // progetto, e mostrarlo come tale mentirebbe sul confine.
      this.scope = { kind: 'project', name: rest.split('/')[0] || null };
      if (!this.scope.name) this.scope = { kind: 'personal', name: null };
    }
    this._publishPin();
    this.render();
  }

  /** Pubblica su ``AppState`` la wiki a cui le viste sono agganciate.
   *
   *  Le viste wiki e grafo hanno bisogno della stessa risposta che il chip ha
   *  gia' — *in quale progetto siamo* — e questo e' l'unico punto in cui
   *  cambia. Passa da ``AppState`` e non da un import diretto del chip perche'
   *  ``set`` avvisa chi ascolta: cambiare progetto mentre una vista e' aperta
   *  la deve riagganciare, e senza notifica resterebbe sul progetto di prima.
   *
   *  ``null`` = sessione personale, cioe' nessun aggancio: le viste tornano a
   *  mostrare tutte le wiki, che e' la Home di sempre.
   */
  _publishPin() {
    const pinned = this.scope.kind === 'project' ? this.scope.name : null;
    if (AppState.pinnedWiki === pinned) return;
    AppState.set('pinnedWiki', pinned);
  }

  /** Nome mostrato per la sessione personale (non è un nome di cartella). */
  get personalLabel() {
    return i18n.t('scope.personal');
  }

  /** Segmenti del percorso mostrati nel chip. */
  get pathSegments() {
    return this.scope.kind === 'project'
      ? [this._dir, this.scope.name]
      : [this.personalLabel];
  }

  // ── Chip ───────────────────────────────────────────────────────────────

  render() {
    if (!this.enabled) return;
    const isProject = this.scope.kind === 'project';
    this.el.dataset.scope = this.scope.kind;
    this.el.setAttribute('aria-label', i18n.t('scope.change'));

    const mark = this.el.querySelector('.scope-chip-mark');
    // La sessione personale porta il fiore, un progetto la cartella: due
    // stati che devono distinguersi anche prima di leggere il nome.
    mark.className = 'scope-chip-mark' + (isProject ? ' ti ti-folder' : '');
    mark.textContent = isProject ? '' : '✿';

    const pathEl = this.el.querySelector('.scope-chip-path');
    pathEl.innerHTML = '';
    this.pathSegments.forEach((part, i) => {
      if (i) {
        const sep = document.createElement('span');
        sep.className = 'scope-chip-sep';
        sep.textContent = '›';   // lo stesso separatore del breadcrumb
        pathEl.appendChild(sep);
      }
      const crumb = document.createElement('span');
      crumb.className = 'scope-chip-crumb';
      crumb.textContent = part;
      pathEl.appendChild(crumb);
    });

    this._syncPlaceholder();
  }

  /** Il placeholder nomina lo scope: è il testo su cui si sta per scrivere. */
  _syncPlaceholder() {
    const input = document.getElementById('chat-input');
    if (!input) return;
    input.placeholder = this.scope.kind === 'project'
      ? i18n.t('scope.askAbout', { name: this.scope.name })
      : i18n.t('chat.placeholder');
  }

  // ── Tendina ────────────────────────────────────────────────────────────

  toggle() {
    this._open ? this.close() : this.open();
  }

  async open() {
    if (!this.enabled) return;
    this._open = true;
    this.menu.classList.add('open');
    this.el.setAttribute('aria-expanded', 'true');
    this._renderMenu();                 // subito, con quel che c'è in cache
    await this._loadProjects();
    if (this._open) this._renderMenu();
  }

  close() {
    if (!this.enabled || !this._open) return;
    this._open = false;
    this.menu.classList.remove('open');
    this.el.setAttribute('aria-expanded', 'false');
  }

  /** Progetti = le wiki del workspace, lette dal backend. */
  async _loadProjects() {
    try {
      const data = await api.listProjects();
      this._dir = data?.dir || DEFAULT_DIR;
      // Dal piu' recente: l'ordine alfabetico del backend mette in cima la
      // wiki con la lettera piu' bassa, che non e' mai quella che si cerca.
      // Il criterio e' lo stesso `modified` che ogni riga stampa accanto al
      // nome, quindi l'elenco non puo' contraddire quel che mostra; a parita'
      // (mtime uguale, o mancante e quindi 0) decide il nome, per non avere
      // un ordine che cambia a ogni apertura.
      this._projects = (data?.projects || [])
        .map(it => ({ name: it.name, modified: it.modified }))
        .sort((a, b) => (b.modified || 0) - (a.modified || 0) || a.name.localeCompare(b.name));
      this.render();                    // il nome della cartella puo' essere cambiato
    } catch {
      // Nessuna wiki ancora, o la feature spenta in config: elenco vuoto, non
      // un errore da mostrare. Un guasto vero si vede al primo "Nuovo".
      this._projects = [];
    }
  }

  _renderMenu() {
    this.menu.innerHTML = '';
    this.menu.appendChild(this._label(i18n.t('scope.personalSection')));
    this.menu.appendChild(this._item({
      name: this.personalLabel, flower: true,
      active: this.scope.kind === 'personal',
      onPick: () => this.select({ kind: 'personal', name: null }),
    }));

    this.menu.appendChild(this._sep());
    this.menu.appendChild(this._label(i18n.t('scope.projectsSection')));
    // Solo i progetti stanno nel riquadro che scorre: il resto della tendina
    // e' alto quanto e' alto, e non deve sparire quando l'elenco cresce.
    const list = document.createElement('div');
    list.className = 'scope-menu-scroll';
    this.menu.appendChild(list);
    let activeEl = null;
    if (this._projects === null) {
      list.appendChild(this._note(i18n.t('scope.loading')));
    } else if (!this._projects.length) {
      list.appendChild(this._note(i18n.t('scope.noProjects')));
    } else {
      for (const project of this._projects) {
        const active = this.scope.kind === 'project' && this.scope.name === project.name;
        const item = this._item({
          name: project.name, icon: 'ti-folder',
          when: this._ago(project.modified),
          active,
          onPick: () => this.select({ kind: 'project', name: project.name }),
        });
        if (active) activeEl = item;
        list.appendChild(item);
      }
    }

    this.menu.appendChild(this._sep());
    const add = this._item({
      name: i18n.t('scope.newProject'), icon: 'ti-plus',
      onPick: () => this._createProject(),
    });
    add.classList.add('scope-menu-new');
    this.menu.appendChild(add);

    // Con l'elenco piu' lungo del riquadro il progetto in cui si sta puo'
    // essere sotto la piega: aprire la tendina senza vedere dove si e' toglie
    // alla tendina meta' del suo mestiere. `nearest` non muove niente quando
    // e' gia' visibile.
    activeEl?.scrollIntoView({ block: 'nearest' });
  }

  _label(text) {
    const el = document.createElement('div');
    el.className = 'scope-menu-label';
    el.textContent = text;
    return el;
  }

  _sep() {
    const el = document.createElement('div');
    el.className = 'scope-menu-sep';
    return el;
  }

  _note(text) {
    const el = document.createElement('div');
    el.className = 'scope-menu-note';
    el.textContent = text;
    return el;
  }

  _item({ name, icon, flower = false, when = '', active = false, onPick }) {
    const btn = document.createElement('button');
    btn.className = 'scope-menu-item';
    btn.type = 'button';
    btn.setAttribute('role', 'menuitemradio');
    btn.setAttribute('aria-checked', active ? 'true' : 'false');
    btn.dataset.active = active ? '1' : '0';

    if (flower) {
      const f = document.createElement('span');
      f.className = 'scope-menu-flower';
      f.textContent = '✿';
      btn.appendChild(f);
    } else {
      const i = document.createElement('i');
      i.className = `ti ${icon}`;
      btn.appendChild(i);
    }

    const text = document.createElement('span');
    text.className = 'scope-menu-text';
    const nameEl = document.createElement('span');
    nameEl.className = 'scope-menu-name';
    nameEl.textContent = name;
    text.appendChild(nameEl);
    if (when) {
      const whenEl = document.createElement('span');
      whenEl.className = 'scope-menu-when';
      whenEl.textContent = when;
      text.appendChild(whenEl);
    }
    btn.appendChild(text);

    const check = document.createElement('i');
    check.className = 'ti ti-check scope-menu-check';
    btn.appendChild(check);

    btn.addEventListener('click', () => {
      this.close();
      onPick();
    });
    return btn;
  }

  // ── Azioni ─────────────────────────────────────────────────────────────

  /** La chiave di sessione di uno scope. */
  static keyFor(scope) {
    return scope.kind === 'project' && scope.name
      ? `project:${scope.name}`
      : null;   // null = la conversazione personale, che la conosce il chiamante
  }

  /** Cambia scope: il chip, il placeholder, e la conversazione sotto. */
  select(scope) {
    const changed = scope.kind !== this.scope.kind || scope.name !== this.scope.name;
    this.scope = scope;
    this.render();
    if (changed) this.onSwitch?.(ScopeChip.keyFor(scope), scope);
  }

  /** Due domande, in quest'ordine: come si chiama, e di cosa si occupa.
   *
   *  La seconda non e' un extra da riempire dopo. Un progetto senza una riga di
   *  scope lascia il primo turno senza niente su cui appoggiarsi, e uno scope
   *  indovinato dall'agente e' peggio di nessuno scope, perche' tutto quel che
   *  viene archiviato dopo lo eredita. Per questo annullarla annulla la
   *  creazione: meglio nessun progetto che un progetto senza scopo. Niente
   *  viene creato su disco prima che entrambe le risposte ci siano.
   */
  async _createProject() {
    const name = await promptDialog(i18n.t('scope.newProjectName'), {
      placeholder: i18n.t('scope.newProjectPlaceholder'),
    });
    if (!name) return;
    const clean = name.trim();
    if (!VALID_NAME.test(clean)) {
      showToast(i18n.t('scope.invalidName'), 'error');
      return;
    }

    const seed = await promptDialog(i18n.t('scope.newProjectSeed', { name: clean }), {
      placeholder: i18n.t('scope.newProjectSeedPlaceholder'),
    });
    if (!seed || !seed.trim()) {
      showToast(i18n.t('scope.seedRequired'), 'info');
      return;
    }

    try {
      await rpc.createProject(clean, seed.trim());
    } catch (err) {
      showToast(i18n.t('scope.createFailed', { error: err.message }), 'error');
      return;
    }
    this._projects = null;              // forza la rilettura da disco
    showToast(i18n.t('scope.created', { name: clean }), 'success');
    this.open();
  }

  /** "2 ore fa" da un mtime unix in secondi. */
  _ago(modified) {
    if (!modified) return '';
    const minutes = Math.floor((Date.now() / 1000 - modified) / 60);
    if (minutes < 2) return i18n.t('scope.ago.now');
    if (minutes < 60) return i18n.t('scope.ago.minutes', { n: String(minutes) });
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return i18n.t('scope.ago.hours', { n: String(hours) });
    const days = Math.floor(hours / 24);
    if (days === 1) return i18n.t('scope.ago.yesterday');
    return i18n.t('scope.ago.days', { n: String(days) });
  }
}

export const scopeChip = new ScopeChip();
