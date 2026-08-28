/** Tendina dei comandi slash, accanto al chip dello scope e all'interruttore.
 *
 * **Perché esiste.** I comandi c'erano da sempre e non li vedeva nessuno:
 * `BUILTIN_COMMAND_SPECS` dava a ognuno titolo, descrizione e icona, ma
 * `as_dict()` non aveva un consumatore in tutto il repo — nessun pulsante,
 * nessuna palette, nessun autocomplete sullo `/`, e le stringhe `/new` e `/help`
 * non comparivano in nessun file i18n. L'unico modo di scoprirli era indovinare
 * `/help`. Su Telegram il menu comandi nativo li elenca, nella WebUI no: la
 * issue #11 è arrivata da lì — «non ho trovato un modo di azzerare la chat», con
 * `/new` a un tocco di distanza da tre anni di conversazione.
 *
 * **Sta nella riga del composer** e non in un header perché la chat non ne ha
 * (`ViewTitleController`: «chat and onboarding have no mount»), e perché è la
 * stessa riga delle altre due domande che si fanno *prima* di premere invio —
 * dove sto lavorando, e se quel che mando può scrivere.
 *
 * **L'elenco viene dal server**, cioè dalla stessa tabella che compone `/help`:
 * un secondo elenco qui dentro sarebbe una cosa da tenere allineata per sempre,
 * ed è il difetto che questo modulo esiste per non ripetere. Dal server arriva
 * anche l'icona (nome Tabler) e `arg_hint`; dai file i18n arriva la prosa,
 * perché la tabella Python è in inglese e le stringhe dell'interfaccia sono
 * localizzate. Chi decide *quali* comandi esistono è il backend, chi decide
 * *come si dicono* sono i JSON: se una chiave manca, si mostra l'inglese del
 * server invece di una chiave grezza.
 */

import { api } from './api-client.js';
import { i18n } from './i18n.js';
import { scopeChip } from './scope-chip.js';

export class CommandsChip {
  constructor() {
    this.el = document.getElementById('commands-chip');
    this.menu = document.getElementById('commands-menu');
    // Chat e onboarding condividono l'index: senza il blocco il modulo non fa
    // nulla invece di sollevare al primo getElementById nullo (come gli altri
    // due controlli della riga).
    this.enabled = Boolean(this.el && this.menu);
    this._open = false;
    this._initialized = false;
    // `null` = mai chiesto, `[]` = chiesto e vuoto. La differenza si vede: la
    // tendina dice "carico" nel primo caso e "nessun comando" nel secondo, e
    // dire il secondo mentre è vero il primo è la sola bugia possibile qui.
    this._commands = null;
    this._loadFailed = false;
    /** Lo monta chi possiede la chat: `(command) => void`. Senza, il chip è
     *  presentazione — stessa disciplina di `scopeChip.onSwitch`. */
    this.onPick = null;
  }

  init() {
    if (!this.enabled || this._initialized) return;
    this._initialized = true;
    this.el.addEventListener('click', (e) => {
      e.stopPropagation();
      this.toggle();
    });
    this.menu.addEventListener('click', (e) => e.stopPropagation());
    // Chiusura: tap fuori ed Escape, come gli sheet dell'app e come lo scope
    // chip. È anche ciò che rende i due menu mutuamente esclusivi senza che
    // nessuno dei due sappia dell'altro: aprire l'uno è un click sul document,
    // che chiude l'altro.
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

  render() {
    if (!this.enabled) return;
    const label = this.el.querySelector('.commands-chip-label');
    if (label) label.textContent = i18n.t('commands.label');
    this.el.setAttribute('aria-label', i18n.t('commands.aria'));
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
    this._alignToChip();
    await this._load();
    if (this._open) {
      this._renderMenu();
      // Di nuovo dopo la lista vera: la larghezza del pannello cambia con il
      // contenuto, e il vincolo che lo tiene dentro la riga dipende da quella.
      this._alignToChip();
    }
  }

  /* Il pannello si allinea al **suo chip**, bordo sinistro con bordo sinistro.
   *
   * In CSS non si può dire: il contenitore posizionato è `.compose-scope`, larga
   * tutta la riga, quindi `left: 0` è il bordo della *riga* e `right: 0` quello
   * opposto — e questo chip non sta su nessuno dei due. Misurato sul telefono il
   * 28/08: il pannello si apriva accanto al chip, con in comune solo lo spigolo.
   * Lo scope chip non ha il problema perché è il primo elemento della riga.
   *
   * Il secondo termine è il vincolo: allineato al chip, ma **dentro la riga**.
   * Senza, su uno schermo stretto un pannello da 340px agganciato a un chip a
   * due terzi della riga uscirebbe dal bordo destro — e quel che si perde è la
   * fine delle descrizioni, cioè il motivo per cui la tendina esiste. Quando i
   * due desideri sono incompatibili vince restare a schermo, e il pannello si
   * appoggia al margine destro: è il comportamento che ogni popover ha al bordo.
   */
  _alignToChip() {
    const row = this.menu.offsetParent;   // .compose-scope
    if (!row) return;
    const available = row.clientWidth - this.menu.offsetWidth;
    const left = Math.max(0, Math.min(this.el.offsetLeft, available));
    this.menu.style.left = `${left}px`;
  }

  close() {
    if (!this.enabled || !this._open) return;
    this._open = false;
    this.menu.classList.remove('open');
    this.el.setAttribute('aria-expanded', 'false');
  }

  /** L'elenco si chiede una volta sola: la tabella è compilata nel backend e
   *  non cambia finché l'app è viva. Un fallimento invece si riprova, perché il
   *  primo tentativo può cadere sul gateway che non ha ancora finito di alzarsi
   *  — il caso normale su un telefono. */
  async _load() {
    if (this._commands !== null && !this._loadFailed) return;
    try {
      const data = await api.getCommands();
      this._commands = Array.isArray(data?.commands) ? data.commands : [];
      this._loadFailed = false;
    } catch (err) {
      console.warn('commands list failed:', err);
      this._loadFailed = true;
      if (this._commands === null) this._commands = [];
    }
  }

  /** Il testo di una voce: i JSON prima, l'inglese del server come ripiego.
   *
   *  Il ripiego non è cortesia: un comando aggiunto nel backend e non ancora
   *  tradotto deve comparire *con la sua descrizione*, non con
   *  `commands./foo.title` stampato in faccia all'utente. */
  _text(command, field, fallback) {
    const key = `commands.${command.replace('/', '')}.${field}`;
    const value = i18n.t(key);
    return value === key ? (fallback || '') : value;
  }

  _renderMenu() {
    this.menu.innerHTML = '';
    const inProject = scopeChip.scope?.kind === 'project';

    if (this._commands === null) {
      this.menu.appendChild(this._note(i18n.t('commands.loading')));
      return;
    }
    if (this._loadFailed && !this._commands.length) {
      this.menu.appendChild(this._note(i18n.t('commands.loadFailed'), true));
      return;
    }

    const list = document.createElement('div');
    list.className = 'commands-menu-scroll';
    this.menu.appendChild(list);

    for (const spec of this._commands) {
      // `/tidy` e `/init` fuori da un progetto non hanno un soggetto: offrirli
      // vorrebbe dire proporre due voci che non fanno niente.
      if (spec.scope === 'project' && !inProject) continue;
      list.appendChild(this._item(spec));
    }
  }

  _item(spec) {
    const btn = document.createElement('button');
    btn.className = 'commands-menu-item';
    btn.type = 'button';
    btn.setAttribute('role', 'menuitem');

    const icon = document.createElement('i');
    icon.className = `ti ti-${spec.icon || 'command'}`;
    btn.appendChild(icon);

    const text = document.createElement('span');
    text.className = 'commands-menu-text';

    const name = document.createElement('span');
    name.className = 'commands-menu-name';
    name.textContent = spec.arg_hint ? `${spec.command} ${spec.arg_hint}` : spec.command;
    text.appendChild(name);

    const desc = document.createElement('span');
    desc.className = 'commands-menu-desc';
    // Il titolo, non la descrizione: la riga è alta una riga sola, e la
    // descrizione lunga di `/gardener` la trasformerebbe in un paragrafo.
    desc.textContent = this._text(spec.command, 'title', spec.title);
    text.appendChild(desc);

    btn.appendChild(text);
    btn.addEventListener('click', () => {
      this.close();
      this.onPick?.(spec);
    });
    return btn;
  }

  _note(text, isError = false) {
    const el = document.createElement('div');
    el.className = 'commands-menu-note' + (isError ? ' is-error' : '');
    el.textContent = text;
    return el;
  }
}

export const commandsChip = new CommandsChip();
