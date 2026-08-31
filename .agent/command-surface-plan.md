# Le manopole in Impostazioni, i comandi ai verbi — piano

Stato: **proposto**, nulla implementato.
Scritto: 2026-08-31.
Lista di esecuzione: [`command-surface-checklist.md`](./command-surface-checklist.md).

## La regola, in una riga

> **Un comando è un verbo: fa qualcosa adesso, in questa conversazione. Una manopola è
> una preferenza che sopravvive al turno: vive in Impostazioni.**

Oggi quella riga è vera per tutta l'app tranne che per due comandi, e per un terzo
lavoratore periodico non è vera in nessun senso perché non ha nessuna superficie.

---

## Cosa è stabilito

**Tre lavoratori periodici interni**, registrati come job di sistema dal composition root
con tre blocchi identici: Dream (`container.py:394`), Atlas (`container.py:409`), il
giardiniere (`container.py:426`). Le loro manopole stanno in `config.json` sotto
`agents.defaults.{dream,atlas,gardener}`, più `agents.defaults.compact_projects_when_idle`.

**La copertura è a macchia di leopardo**, e non per disegno:

| | acceso/spento | intervallo | manopole di merito | dove |
| --- | --- | --- | --- | --- |
| Dream | ✗ | ✗ | ✓ (3 tetti + cadenza review) | `/dream budget` |
| giardiniere | ✓ | ✓ | ✓ (silenzio, distanza, compact) | `/gardener settings` |
| Atlas | ✗ | ✗ | ✗ | **nessuna superficie** |

**Le impostazioni normali stanno in una schermata**, non nei comandi: provider, modello
persistito, context window, max tokens, temperature, reasoning effort, fuso, nome e icona
del bot, hint dei tool, ricerca web, posizione, batteria, Telegram, SSH, aggiornamenti.
Sono ~18 manopole, tutte in `mobile-settings.js` + `settings_api.py`.

**I due comandi che scrivono `config.json`** sono gli unici due writer fuori da quella
schermata (verificato: `store.mutate` in `command/builtin.py` compare a 1164, 1781, 1816,
1864, e in nessun altro file di `jenny/command/`).

**Atlas ha già un pezzo di payload che nessuno disegna.** `settings_payload()` serve
`runtime.dream.schedule` e `runtime.atlas.{enabled,schedule}`
(`settings_api.py:775-785`), e in tutta la WebUI non esiste un consumatore di quei campi —
`grep -rn atlas jenny/templates/ui/assets/` trova solo un commento e le chiavi i18n del
comando. È la stessa forma del difetto delle icone dei comandi (`as_dict()` senza
consumatore fino alla 0.9.0): un campo servito e mai letto.

**Perché quei due comandi esistono, e va conservato.** Prima del blocco
`/gardener settings` niente leggeva o scriveva `agents.defaults.gardener`: `enabled=False`
— la via d'uscita documentata — non era raggiungibile da nessuna superficie, e
`compactProjectsWhenIdle` è arrivato acceso sul device scritto a mano fuori da
`store.mutate()`, spento poi con un `sed -i` che ha rotto l'etichetta SELinux del file. Il
comando è il rimedio a quella classe di incidente. **C3 non lo annulla: lo sposta dove
stanno le altre diciotto manopole, e lo estende al lavoratore che è rimasto scoperto.**

---

## Cosa cambia, e cosa no

Cambia **dove** si girano le manopole. Non cambiano: i tetti e i range (letti dallo
schema), le regole (il pavimento 12 della cadenza review resta e resta lato server), la
prosa che spiega *perché* un numero ha un tetto — quella si trasferisce ai file i18n,
parola per parola, perché è l'unico posto in cui è scritta.

Restano comandi tutti i **verbi**: `/dream` (consolida adesso), `/atlas` (ricompila la
rubrica adesso), `/gardener` (una passata adesso). Sono la ragione per cui i tre lavoratori
sono collaudabili senza aspettare i loro orologi, e quella ragione non c'entra con le
manopole.

---

## Stadio 1 — il payload (letture)

**Modulo nuovo: `jenny/webui/worker_settings.py`.** Non dentro `settings_api.py`, che è già
oltre 1.200 righe e porta provider, onboarding, update e diagnostica energetica; e non
dentro `webui/commands.py`, che è la superficie RPC per le scritture *con contenuto*. Qui
non c'è contenuto: sono numeri e booleani, quindi query string come le altre famiglie di
impostazioni.

Due funzioni di lettura:

```
memory_settings_payload() -> dict     # Dream + i tre file
worker_settings_payload() -> dict     # Atlas + giardiniere + compattazione progetti
```

**`memory_settings_payload()`** — quel che oggi stampa `/dream budget`:

| campo | fonte | note |
| --- | --- | --- |
| `enabled`, `interval_h`, `schedule` | `agents.defaults.dream` + `describe_schedule()` | `interval_h` ha `ge=1` |
| `memory_budget_chars`, `user_budget_chars`, `soul_budget_chars` | idem | default 3000 / 3000 / 0; `ge=0`, `0` = misura e non applica |
| `review_every_runs` | idem | `ge=1` nello schema, **pavimento operativo 12** (v. stadio 2.3) |
| `files[]` = `{label, chars, budget}` | `budget_report(MemoryStore(workspace), …)` | ordine di rendering MEMORY, USER, SOUL: è contratto, `render_gauge` non riordina |
| `runs_since_review`, `stuck_runs` | `MemoryStore.get_review_state()` | legge `memory/.dream_review` |
| `nothing_new_runs` | `MemoryStore.get_nothing_new_runs()` | |

**Fail-soft, e non è cortesia.** Queste letture aprono quattro file dell'utente. Una
schermata Impostazioni che non si apre perché `SOUL.md` è illeggibile sarebbe un guasto
peggiore di quello che sta segnalando — e sarebbe anche la schermata da cui si spengono i
lavoratori. Un file mancante o illeggibile vale `chars: null`, e la UI lo dice; non solleva.

**`worker_settings_payload()`** — Atlas (`enabled`, `interval_h` `ge=1`,
`max_context_tokens` `ge=100`, `schedule`), giardiniere (`enabled`, `interval_min` 1–1440,
`idle_min` 0–1440, `min_hours_between_passes` 0–8760, `schedule`), più
`compact_projects_when_idle`.

**I range non si riscrivono qui.** Si leggono da `model_fields` come già fa
`_gardener_range` (`builtin.py:1473`): un range scritto due volte diventa due range appena
uno dei due si muove, ed è la cosa che il comando racconta all'utente nei suoi rifiuti.
Il payload li porta al client (`{value, min, max}`), così l'`<input type=number>` non
inventa i propri.

**Innesto nel payload esistente.** `settings_payload()` guadagna due chiavi, `memory` e
`workers`, e **perde** `runtime.dream` e `runtime.atlas`: sono i due campi senza
consumatore, e lasciarli sarebbe tenere due verità sullo stesso oggetto. Nessun client
dipende da loro (verificato).

`settings_payload()` è sincrona e chiamata da un handler sincrono: quattro `stat` + quattro
letture piccole sull'event loop. Accettabile, e va detto nel commento; se un giorno il
profilo dice altro, la via è `asyncio.to_thread` sul solo blocco di misura.

## Stadio 2 — le scritture

Due funzioni, una `store.mutate` ciascuna, nello stesso stile di `_apply_agent_settings`:
parse → validazione → mutate → payload di ritorno.

```
update_memory_settings(query) -> dict
update_worker_settings(query) -> dict
```

**2.1 Disciplina delle scritture.** Ogni campo assente dalla query non si tocca (patch, non
PUT). Un valore identico ritorna `False` dalla callback: `config.json` non viene riscritto e
il `.bak` non ruota per nulla — è il comportamento che i comandi hanno oggi e che i loro
test pinnano. Le letture di misura stanno **prima** di entrare in `mutate`: quel lock resta
preso per tutta la callback.

**2.2 Fuori range.** Rifiuto con `WebUISettingsError` che **nomina il range** e non solo
"invalid": le tre frasi `out_of_range` di `_GARDENER_NUMBERS` (`builtin.py:1502`) sono
scritte bene e vanno portate in i18n, non riscritte. Restano lato server perché un client
vecchio non deve poter scrivere un valore che questo non accetta.

**2.3 Il pavimento della cadenza review.** Resta 12, resta lato server, e cambia solo la
forma del consenso:

- oggi: `/dream budget review 1` rifiuta e stampa la frase
  `i-accept-back-to-back-reviews` da ribattere;
- domani: `review_every_runs=1` senza `confirm_back_to_back=1` → rifiuto con lo stesso
  testo; il client lo trasforma in un dialogo (`dialog.js`) e solo un "sì" esplicito rimanda
  la richiesta col flag.

La frase era l'idioma di una chat — «qualcosa che decidi di digitare, non che ti viene
aggiunto». In una UI l'equivalente è un secondo consenso che non è un tap distratto: un
dialogo con dentro le misure vere (USER.md da 3.524 a 1.626 caratteri in due passate, 31%
sulla seconda, cinque voci reali rimosse). **Quel testo è l'unico posto in cui il perché è
scritto: si sposta, non si riassume.**

**2.4 Il ri-armo, che oggi esiste per uno su tre.** `refresh_gardener_job`
(`cron_dispatch.py:256`) esiste perché `interval_min` non vive nel `Config` letto a ogni
tick — è diventato lo `schedule` del `CronJob` nello store del cron — e perché il job è
registrato **solo se acceso**: su un gateway partito col giardiniere spento, un
`enabled=True` scritto nel file non lo leggerebbe nessuno.

Le stesse due cose valgono per Dream e Atlas, e per loro non c'è nessuna funzione. Quindi:

> `refresh_system_job(cron, worker: "dream" | "atlas" | "gardener") -> str | None`,
> una tabella di tre righe `(job id, accessor di config)`, con `refresh_gardener_job`
> conservato come sottile alias finché i suoi test non sono portati.

Chi ha bisogno del ri-armo: `enabled` e l'intervallo di tutti e tre. Chi non ne ha bisogno
(letti a ogni tick o a ogni prompt): `idle_min`, `min_hours_between_passes`, i tre tetti,
`review_every_runs`, `max_context_tokens`. Questa distinzione va nei test, o si degrada in
un ri-armo a ogni salvataggio — che non rompe niente ma sposta la prossima scadenza a ogni
tocco di un numero che non c'entra.

**2.5 Il gancio.** `_fire_settings_changed` ricostruisce provider e modello: non è questo.
Serve un secondo gancio, `on_jobs_changed(worker)`, con i quattro punti di cablaggio già
percorsi dal primo — `settings_routes.py` (init + chiamata), `gateway_services.py:64`,
`ws_http.py:180`, `container.py:357` — e nel container l'implementazione è una riga:
`refresh_system_job(self.cron, worker)`.

**2.5-bis L'interruttore del giardiniere è *la* via d'uscita, e cambia casa.** Tre cose che
il comando faceva e che l'interruttore deve continuare a fare, o la via d'uscita si degrada:

- **Effetto immediato.** `CronDispatcher._run_gardener` rilegge la config a ogni tick,
  quindi `enabled=False` vale subito da qualunque parte sia stato scritto. Riaccendere
  invece ha bisogno del ri-armo (2.4): su un gateway partito col giardiniere spento il job
  **non è registrato**.
- **Deve funzionare da una config che lo schema di oggi boccerebbe.** È scritto nella
  docstring di `_set_gardener_enabled`: chi ha un `intervalMin` fuori range scritto da una
  versione precedente passa comunque, perché `GardenerConfig.clamp_raw` riporta i numeri
  dentro i tetti al parse. Lo eredita `store.mutate`, che rilegge il file dentro il proprio
  lock — ma va **pinnato con un test**, perché è la sola strada per cui spegnere non deve
  mai fallire.
- **La frase che accompagna lo spegnimento.** `_gardener_off_line()` — «la passata periodica
  non girerà; `/gardener` a mano continua a funzionare» — diventa il testo di aiuto sotto
  l'interruttore. Spegnere non è disinstallare, e un utente che legge solo "off" non ha modo
  di saperlo.

**2.6 `compact_projects_when_idle`.** È l'unica che vale dal prossimo avvio del gateway
(`agents.defaults`, letta quando l'agente parte). Il payload ha già `requires_restart`: la
risposta lo alza e la UI lo dice sul posto, non in un toast che scorre via. La frase
`_COMPACT_TAKES_EFFECT` (`builtin.py:1650`) va in i18n insieme al costo — dopo, l'agente ha
in contesto quel che è stato *scritto* e non quel che è stato *detto*; il transcript
visibile non si tocca.

**2.7 Rotte.** `/api/settings/memory/update` e `/api/settings/workers/update` in
`settings_routes.py`, accanto a `web-search`, `location`, `power`. Query string: sono numeri
e booleani, quindi la superficie `/api/` basta (nessun contenuto, nessuna emoji, nessun
limite di riga da 8 KB).

## Stadio 3 — la schermata

**Due sezioni nuove, non una e non tre.** `render()` (`mobile-settings.js:175`) compone
otto sezioni «una per asse mentale»; queste due seguono il confine che il codice traccia già
altrove — la memoria personale da una parte, wiki e progetti dall'altra:

- **`memory`** — icona `ti-sparkles`, titolo *Memoria*. Sottotitolo «Dream»: interruttore,
  intervallo (h), cadenza review. Divisore. Sottotitolo «Tetti dei file»: tre righe
  `MEMORY.md / USER.md / SOUL.md`, ognuna con la misura attuale, una barra e il campo del
  tetto (`0` = misura e non applica). Sotto, una riga di stato: quante passate dall'ultima
  review, e — se non è zero — `stuck_runs`.
- **`wiki`** — icona `ti-map`, titolo *Wiki e progetti*. Sottotitolo «Atlas»: interruttore,
  intervallo (h), tetto della rubrica in contesto. Divisore. Sottotitolo «Giardiniere»:
  interruttore, intervallo (min), silenzio richiesto (min), distanza fra due passate (h).
  Divisore. Sottotitolo «Cronologia dei progetti»: l'interruttore `compact`, con il costo
  scritto sotto e la nota del riavvio.

Posizione: dopo `tools` e prima della batteria. `tools` sono le capacità dell'agente, queste
sono la sua memoria; la diagnostica sta in fondo.

**3.1** `api-client.js`: `updateMemorySettings`, `updateWorkerSettings`.

**3.2** Il cablaggio riusa gli idiomi già in `_wireSections()` (`mobile-settings.js:1886`):
salvataggio al `change` con debounce per famiglia, ottimistico con **rollback** sull'errore
(il toggle torna indietro, come `location`), toast di conferma. Le barre dei tetti si
disegnano con il markup delle statistiche d'uso (`settings-usage-*`) o un meter minimo:
niente dipendenze nuove.

**3.3** Il dialogo della cadenza (2.3) in `dialog.js`, testo da i18n.

**3.4** i18n: chiavi nuove sotto `settings.memory.*` e `settings.wiki.*`, in
`it.json` **e** `en.json` — `tests/webui/test_i18n_parity.py` non perdona un buco.

**3.5** Un test client in node nello stile di `test_commands_chip_client.py`: il rollback di
un toggle su errore, e il fatto che senza conferma la cadenza sotto 12 non parte.

## Stadio 4 — la rimozione dai comandi

Righe di oggi, verificate. Tutto in `jenny/command/builtin.py` (2.035 righe).

**4.1 `/dream`.** Cade il ramo con argomento (359-364) e il blocco che serve solo lui:
**725-1184**, dal banner `# /dream budget — leggere le misure e tarare i budget` fino a
prima di `cmd_atlas`. Dentro: `_DreamBudgetField`, `_REVIEW_CADENCE_FLOOR` (787),
`_REVIEW_CADENCE_OVERRIDE` (801), `_DREAM_BUDGET_FIELDS` (803), `_dream_usage` (851),
`_review_cadence_refusal` (870), `_format_dream_budget_report` (910),
`_parse_dream_budget_value` (1007), `_format_dream_budget_change` (1029),
`_dream_budget_command` (1074).

> **Attenzione, una trappola vera:** `_int_or_zero` (905) sta dentro quel blocco ma è usata
> a **535**, nel ramo che *lancia* Dream. Va **spostata**, non cancellata. Cancellarla
> insieme al resto rompe il percorso che il piano non voleva toccare.

Restano (stanno prima del banner, servono al run): `_prefix_review_note` (592),
`_format_dream_review_note` (597), `_format_dream_refusals`, `_format_dream_demotions`,
`_format_dream_no_input_message`, e `render_gauge`, importato a 390 e usato a 437.

**4.2 `/gardener`.** Cade il blocco **1434-1872**, dal banner
`# /gardener settings — leggere e tarare la passata periodica` fino al `return` di
`_set_compact_projects`, e in `cmd_gardener` le due righe che ci instradano (1262-1263) più
il ramo `if named:` (1264-1266).

Restano (prima del banner, servono alla passata): `_reply` (1306), `_gardener_no_target`
(1313), `_format_gardener_outcome` (1320), `_gardener_map_pass_line` (1400).

**4.3 Il ri-armo si sposta, non muore.** `_rearm_gardener_job` (1683) esce dai comandi ed
entra nel gancio dello stadio 2.5.

**4.3-bis Due testi fuori dai comandi che nominano forme che non esisteranno più.**
L'allarme delle passate fallite (`cron_dispatch.py:243`) dice *«Run /gardener {name} to see
the error»*: è la forma con il nome, che lo stadio 5 rimuove. Va riscritto in «apri quel
progetto e lancia `/gardener`». È l'unica superficie che suona quando il diario di un
progetto smette di diventare pagine: un allarme che indirizza a un comando inesistente è
peggio di nessun allarme.

**4.4 `arg_hint`.** `/dream`: `"[budget [name n]]"` → `""`. `/gardener`:
`"[project|settings]"` → `""` (il nome del progetto era già deciso: il lavoro di progetto si
fa da dentro). Conseguenza gradita nella tendina: entrambi diventano comandi che si mandano
al tocco invece di essere scritti nel composer.

**4.5 I prefissi restano, per una release.** `router.prefix("/dream ", …)` e
`router.prefix("/gardener ", …)` non si toccano: puntano a una riga sola di migrazione —
«questo comando non prende argomenti; le manopole sono in Impostazioni → Memoria (o → Wiki e
progetti)». Senza, `/dream budget` battuto a memoria non è un comando e finisce **al
modello** come messaggio. Va con una data nel commento, altrimenti diventa un ramo per
sempre.

**4.6 Il conto.** ~900 righe via da un file di 2.035. Non è il motivo del piano, ma è il
secondo guadagno: `builtin.py` torna a essere la tabella dei comandi più i loro verbi.

**4.7 I test non si cancellano: si portano.** `test_dream_budget_command.py` (750 righe),
`test_gardener_settings_command.py` (515) e la parte di `test_dream_run_budget.py` (811) che
guarda il ramo `budget` sono conoscenza accumulata: i range, la scrittura a vuoto che non
riscrive il file, il pavimento, la clemenza sul `clamp_raw` di una config vecchia. Quel che
cambia è il trasporto — testo di una risposta in chat → JSON e chiavi i18n. Questa è la voce
di costo più grossa del piano ed è meglio saperlo prima: **la stima onesta è che lo stadio 4
costi più dello stadio 2.**

## Stadio 5 — i comandi giusti nelle sezioni giuste

Finiti gli stadi 1-4, ogni comando è un verbo. La regola di scope diventa una frase con
zero eccezioni:

> **Il soggetto decide la sezione.** Questa conversazione → sempre. La memoria personale o
> l'installazione → solo chat personale. Questo progetto → solo da dentro il progetto.

| sezione | comandi | soggetto |
| --- | --- | --- |
| **Sempre** (6) | `/new` `/stop` `/status` `/history` `/goal` `/help` | questa conversazione |
| **Solo chat personale** (4) | `/dream` `/atlas` `/model` `/skill` | memoria personale, installazione |
| **Solo dentro un progetto** (3) | `/gardener` `/tidy` `/init` | questo progetto, preso dalla session key |

Perché quelle quattro non entrano in un progetto: `/dream` consolida `MEMORY.md`, che una
sessione `project:` **per costruzione** non alimenta (`session_kind`); `/atlas` ricompila la
rubrica di *tutte* le wiki, che `context.py:799` toglie deliberatamente dal prompt di
progetto; `/model` e `/skill` sono stato dell'installazione. È lo stesso confine del tool
cron, che dentro un progetto rifiuta in tutte e tre le azioni (`cron.py:208`), e la stessa
frase del prompt: chi sei viaggia, dove altro lavori no.

`/status` resta "sempre" per una ragione misurabile e non per gentilezza: conta anche i task
attivi di *quella* sessione (`builtin.py:203`).

**Implementazione.**

1. `BuiltinCommandSpec.scope: "any" | "personal" | "project"` (oggi solo `any`/`project`), e
   il campo messo su tutte e 13 le voci.
2. Modulo nuovo `jenny/command/scope.py`: `available(spec, session_key) -> bool` e
   `visible_specs(session_key)`, sopra `jenny.session.keys.session_kind`. La domanda di
   scope si risponde dove vive la tassonomia delle sessioni, non in tre posti.
3. Cancello nel dispatch: `CommandRouter.dispatch` risolve la spec della riga e, se non è
   disponibile, ritorna **un** rifiuto canonico — che dice *dove*, non solo che qui non si
   può, nella forma di `_NO_PROJECT_REFUSAL` (`journal.py:53`) e di `_gardener_no_target`.
   Oggi gli stili sono tre: il gate nel loop per `/tidy` e `/init`, la frase di
   `_gardener_no_target`, e niente per `/dream` e `/atlas`.
4. `build_help_text(session_key)` filtra con la stessa funzione. **Sistema Telegram
   gratis**: quel canale è sempre la sessione personale (`session_key_for_channel`), e oggi
   `/help` lì pubblicizza `/tidy` e `/init`, che su Telegram non possono funzionare mai.
5. `/api/webui/commands` riceve la session key e filtra lato server; le due righe di filtro
   nel client (`commands-chip.js:186`) **spariscono**. Il chip è nato per non tenere un
   secondo elenco e ne teneva una versione piccola: il filtro lato client non è
   applicazione, è cosmetica — non c'è autocomplete sullo `/`, quindi oggi `/dream` battuto
   dentro un progetto gira.
6. `/tidy` e `/init` continuano a **espandersi nel turno** (quella è una faccenda del turno,
   `loop.py:1583`), ma la loro *disponibilità* la decide la funzione condivisa: una regola,
   non due copie.

**Test toccati:** `test_command_specs.py::test_project_only_commands_are_the_two_that_expand_in_the_turn`
asserisce `== {"/tidy","/init"}` — è l'invariante che diventa sbagliata, va riscritta come
«i project-only sono quelli dichiarati tali», con una gemella per i `personal`.
`test_commands_chip_client.py` copre il filtro che si rimuove. `build_help_text()` cambia
firma: `tests/agent/test_project_init_command.py:174` e
`tests/agent/test_project_tidy_command.py:305`. Il cancello nuovo va in
`tests/command/test_router_dispatch.py`.

**Due asimmetrie che restano, coscienti.** `/model` è il solo accesso ai `model_presets`: la
schermata scrive `agents.defaults.model`, non i preset. Non è una manopola, è un commutatore
a runtime senza persistenza — resta comando, e un selettore di preset nella schermata è
un'altra storia. E `/skill` è un elenco in sola lettura: nessuna manopola da spostare.

## Stadio 6 — documentazione

`docs/**` ha un secondo consumatore fuori dal repo (il sito genera le rotte da questi file):
**si modifica il contenuto, non si spostano né rinominano i file.**

| file | cosa |
| --- | --- |
| `docs/using/slash-commands.md` | ristrutturare nelle tre sezioni dello stadio 5 (oggi è un elenco piatto con un `## Model` orfano a :88); via la prosa delle sette parole riservate a :214; **aggiungere la sezione `/tidy`**, che nel file esiste solo come menzione di passaggio a :9 |
| `docs/using/memory.md` | tabella :138-143, il pavimento :185-195, e la riga :237 «None of this is exposed in the Settings UI today» — quella frase diventa falsa, ed è la prova in una riga che il piano è atterrato |
| `docs/using/gardener.md` | tabella :86-91 e la via d'uscita :102-107 |
| `docs/using/projects.md` | :127, `/gardener compact on` |
| `docs/reference/configuration.md` | :93 e :104, dove è scritto che quelle chiavi «sono le uniche con una chat surface» |
| `docs/internals/privacy.md` | :38, `/gardener off` |
| `.agent/gotchas.md` | :149 cita `/dream budget memory 2000` come unica via: si annota come la strada di allora, **non si riscrive la storia** |

## Stadio 7 — verifica sul telefono

Le manopole sono l'unica superficie: vanno esercitate sul device, non solo in pytest.

1. La schermata si apre con un `SOUL.md` illeggibile (fail-soft dello stadio 1).
2. Giardiniere spento dalla UI → `config.json` sul device lo dice (root, e attenzione alle
   categorie MLS dell'etichetta SELinux), e **il job si ri-arma senza riavvio** — la riga di
   log lo dichiara.
3. Dream spento e riacceso: è il percorso che prima non esisteva, quindi è il più a rischio.
4. Cadenza review a 1: il dialogo compare, e senza conferma non parte niente.
5. I tetti mostrano le dimensioni vere dei tre file, e `0` si legge come «misura».
6. `compact` alzato → la nota del riavvio è sul posto, non in un toast.

Ogni cambio di JS richiede una rebuild: patchare `workspace/ui/` sul telefono non fa nulla.

---

## Rischi e decisioni

1. **Telegram perde queste manopole.** Non le ha mai avute (il menu del bot ha `/start` e
   `/new`), e `/help` lì continuerà a elencare solo verbi. Accettato.
2. **Si perde una via d'uscita stretta: `/gardener off` da Telegram.** Quel canale è sempre
   la sessione personale, quindi oggi le parole di `settings` ci funzionano — ed è la sola
   superficie di chat che sopravvive a una WebView rotta. Dopo C3 le strade per spegnere
   sono l'interruttore in Impostazioni e la modifica a mano di `config.json` (non bloccata,
   e su un telefono rootato praticabile). **Accettato**: se la WebView è rotta il guasto è
   più grande del giardiniere, e tenere in chat un albero di manopole per coprire quel caso
   è il prezzo sbagliato. Da rivedere solo se quel caso si presenta davvero.
3. **Le due sezioni non vanno dietro la modalità avanzata.** `/gardener off` è documentato
   come *la* via d'uscita: una via d'uscita dietro una modalità nascosta non è una via
   d'uscita. (E `advancedMode()` è puramente client-side, pensata per skill e file marcati
   `internal`.)
4. **Accendere/spegnere Dream e Atlas è comportamento nuovo, non uno spostamento.** Merita
   test propri, e nello stadio 7 il punto 3 è suo.
5. **Il pavimento 12 resta lato server** anche col dialogo: un client vecchio non deve poter
   parlare l'API in un valore che questa versione rifiuta.
6. **Ordine di atterraggio.** 1→2→3 vanno insieme (una schermata che legge e non scrive è
   peggio di niente). Lo stadio 4 può atterrare dopo, e va **dopo**: cancellare i comandi
   prima che la schermata funzioni lascia il device senza nessuna superficie, ed è
   esattamente lo stato da cui questo piano parte. Lo stadio 5 è indipendente da 1-4 e può
   atterrare prima o dopo; è però il solo stadio che chiude il difetto «dentro un progetto
   ti vengono offerti comandi che lì non hanno soggetto».
