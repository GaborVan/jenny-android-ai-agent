# Manopole in Impostazioni, comandi ai verbi — lista di esecuzione

Stato di [`command-surface-plan.md`](./command-surface-plan.md). Il ragionamento sta là, qui
c'è solo cosa è fatto. Si spunta quando è **girato**, e per una manopola «girato» include il
tocco sul telefono (stadio 7).

Ramo: da aprire. **Niente fatto al 31/08/2026.**

Ordine obbligato: **1→2→3 insieme, poi 4.** Cancellare i comandi prima che la schermata
scriva lascia il device senza nessuna superficie. Lo stadio 5 è indipendente.

---

## Stadio 1 — payload (letture)

- [ ] **1.1** Nuovo `jenny/webui/worker_settings.py` con `memory_settings_payload()` e `worker_settings_payload()`
- [ ] **1.2** `memory_settings_payload`: Dream (`enabled`, `interval_h`, `schedule`), i tre tetti, `review_every_runs`, `files[] = {label, chars, budget}` da `budget_report`, `runs_since_review`, `stuck_runs`, `nothing_new_runs`
- [ ] **1.3** `worker_settings_payload`: Atlas (`enabled`, `interval_h`, `max_context_tokens`, `schedule`), giardiniere (4 campi + `schedule`), `compact_projects_when_idle`
- [ ] **1.4** Ogni numero viaggia come `{value, min, max}`, con i bound letti da `model_fields` — **nessun range riscritto a mano** (v. `_gardener_range`)
- [ ] **1.5** Fail-soft: file mancante o illeggibile → `chars: null`, la schermata si apre comunque
- [ ] **1.6** `settings_payload()` guadagna `memory` e `workers` e **perde** `runtime.dream` / `runtime.atlas` (campi senza consumatore)
- [ ] **1.7** Test: forma del payload; workspace senza `SOUL.md`; un `config.json` con un numero fuori range scritto da una versione precedente (`clamp_raw`) non fa saltare la lettura

## Stadio 2 — scritture

- [ ] **2.1** `update_memory_settings(query)` e `update_worker_settings(query)`, una `store.mutate` ciascuna, campo assente = non toccato
- [ ] **2.2** Valore identico → la callback ritorna `False`: file non riscritto, `.bak` non ruotato *(è quel che i test dei comandi pinnano oggi)*
- [ ] **2.3** Misure lette **prima** di entrare in `mutate` (il lock resta preso per tutta la callback)
- [ ] **2.4** Fuori range → rifiuto che **nomina il range**; le tre frasi `out_of_range` di `_GARDENER_NUMBERS` portate in i18n, non riscritte
- [ ] **2.5** Pavimento cadenza review: `review_every_runs < 12` senza `confirm_back_to_back=1` → rifiuto, col testo misurato di `_review_cadence_refusal` **spostato** parola per parola
- [ ] **2.6** `refresh_system_job(cron, worker)` in `cron_dispatch.py` — tabella di tre righe; `refresh_gardener_job` resta come alias finché i suoi test non sono portati
- [ ] **2.7** Gancio `on_jobs_changed(worker)` cablato nei quattro punti: `settings_routes.py`, `gateway_services.py`, `ws_http.py`, `container.py`
- [ ] **2.8** Test: ri-armo chiamato per `enabled` e intervallo di tutti e tre; **non** chiamato per `idle_min`, `min_hours_between_passes`, i tetti, `review_every_runs`, `max_context_tokens`
- [ ] **2.9** Test: spegnere il giardiniere funziona da un `config.json` con un `intervalMin` **fuori range** scritto da una versione precedente *(la clemenza di `clamp_raw`; è la sola strada per cui spegnere non deve mai fallire)*
- [ ] **2.10** `compact_projects_when_idle` alza `requires_restart` nella risposta
- [ ] **2.11** Rotte `/api/settings/memory/update` e `/api/settings/workers/update` in `settings_routes.py`
- [ ] **2.12** Test: un valore scritto si rilegge; uno fuori range no e il file resta intatto; la cadenza sotto 12 passa **solo** col flag

## Stadio 3 — schermata

- [ ] **3.1** `api-client.js`: `updateMemorySettings`, `updateWorkerSettings`
- [ ] **3.2** Sezione `memory` (`ti-sparkles`, *Memoria*): Dream (interruttore, intervallo, cadenza) · tetti dei tre file con misura e barra · riga di stato delle passate dall'ultima review
- [ ] **3.3** Sezione `wiki` (`ti-map`, *Wiki e progetti*): Atlas (3 campi) · giardiniere (4 campi) · `compact` con il costo scritto e la nota del riavvio
- [ ] **3.4** Inserite in `render()` dopo `tools`, prima della batteria
- [ ] **3.5** Cablaggio con gli idiomi esistenti: `change` + debounce per famiglia, ottimistico con **rollback** sull'errore, toast
- [ ] **3.6** Sotto l'interruttore del giardiniere, il testo di `_gardener_off_line()`: spegnere non è disinstallare, `/gardener` a mano continua a funzionare
- [ ] **3.7** Dialogo di conferma della cadenza (`dialog.js`) con dentro le misure vere
- [ ] **3.8** i18n: `settings.memory.*` e `settings.wiki.*` in `it.json` **e** `en.json` (`test_i18n_parity.py`)
- [ ] **3.9** Test client in node: rollback di un toggle su errore; senza conferma la cadenza sotto 12 non parte

## Stadio 4 — rimozione dai comandi *(dopo che 3 gira)*

- [ ] **4.1** `_int_or_zero` **spostata fuori** dal blocco che si cancella — è usata a `builtin.py:535`, nel ramo che lancia Dream
- [ ] **4.2** Via il ramo `if args:` di `cmd_dream` (359-364) e il blocco 725-1184
- [ ] **4.3** Via il blocco 1434-1872 e, in `cmd_gardener`, l'instradamento (1262-1263) e il ramo `if named:` (1264-1266)
- [ ] **4.4** Restano intatti: `_prefix_review_note`, `_format_dream_review_note`, `_format_dream_refusals`, `_format_dream_demotions`, `_format_dream_no_input_message`, `render_gauge`; `_reply`, `_gardener_no_target`, `_format_gardener_outcome`, `_gardener_map_pass_line`
- [ ] **4.5** `cron_dispatch.py:243`, l'allarme delle passate fallite: «Run /gardener {name}» → «apri quel progetto e lancia `/gardener`»
- [ ] **4.6** `arg_hint` a `""` per `/dream` e `/gardener` *(nella tendina diventano comandi che partono al tocco)*
- [ ] **4.7** I prefissi `"/dream "` e `"/gardener "` restano, puntati a una riga di migrazione che nomina la sezione di Impostazioni — **con una data nel commento**
- [ ] **4.8** Test: `/dream budget` e `/gardener settings` rispondono la riga di migrazione e **non** finiscono al modello
- [ ] **4.9** Portati (non cancellati) `test_dream_budget_command.py`, `test_gardener_settings_command.py` e la parte `budget` di `test_dream_run_budget.py` sui nuovi endpoint
- [ ] **4.10** `ruff check jenny/ tests/` pulito: la cancellazione lascia import morti

## Stadio 5 — scope dei comandi *(indipendente)*

- [ ] **5.1** `BuiltinCommandSpec.scope` accetta `personal`, e il campo è messo su tutte e 13 le voci
- [ ] **5.2** Nuovo `jenny/command/scope.py`: `available(spec, session_key)` e `visible_specs(session_key)`, sopra `session_kind`
- [ ] **5.3** Cancello in `CommandRouter.dispatch`, **un** rifiuto canonico che dice *dove* (forma di `_NO_PROJECT_REFUSAL`)
- [ ] **5.4** `build_help_text(session_key)` filtra — e Telegram smette di pubblicizzare `/tidy` e `/init`
- [ ] **5.5** `/api/webui/commands` filtra lato server; le due righe di filtro in `commands-chip.js` **rimosse**
- [ ] **5.6** `/tidy` e `/init` continuano a espandersi nel turno, ma la disponibilità la decide `scope.py`
- [ ] **5.7** `/gardener` non accetta più un nome di progetto *(già deciso: il lavoro di progetto si fa da dentro)*
- [ ] **5.8** Riscritto `test_command_specs.py::test_project_only_commands_are_the_two_that_expand_in_the_turn` + gemello per i `personal`
- [ ] **5.9** Aggiornati `test_project_init_command.py:174` e `test_project_tidy_command.py:305` (firma di `build_help_text`)
- [ ] **5.10** Muore `test_builtin_gardener.py::test_a_named_project_wins_over_the_current_one`; aggiornati `test_outside_a_project_it_refuses_and_says_how`, `test_is_dispatchable_with_and_without_a_project`, l'assert su `arg_hint`
- [ ] **5.11** Nuovi test del cancello in `tests/command/test_router_dispatch.py`: un `personal` dentro un progetto e un `project` fuori

## Stadio 6 — documentazione *(contenuto, mai rinomini: `docs/` è pubblicato dal sito)*

- [ ] **6.1** `docs/using/slash-commands.md` ristrutturato nelle tre sezioni; via le sette parole riservate (:214); via il `## Model` orfano (:88)
- [ ] **6.2** `docs/using/slash-commands.md`: **sezione `/tidy` aggiunta** (oggi solo menzionato a :9)
- [ ] **6.3** `docs/using/memory.md`: tabella :138-143, pavimento :185-195, e la riga :237 «None of this is exposed in the Settings UI today» resa vera
- [ ] **6.4** `docs/using/gardener.md`: tabella :86-91, via d'uscita :102-107
- [ ] **6.5** `docs/using/projects.md`:127 · `docs/reference/configuration.md`:93,104 · `docs/internals/privacy.md`:38
- [ ] **6.6** `.agent/gotchas.md`:149 annotato come la strada di allora *(non riscrivere la storia)*

## Stadio 7 — sul telefono

- [ ] **7.1** La schermata si apre con un `SOUL.md` illeggibile
- [ ] **7.2** Giardiniere spento dalla UI → `config.json` lo dice **e** il job si ri-arma senza riavvio (riga di log)
- [ ] **7.3** Dream spento e riacceso *(percorso che prima non esisteva: il più a rischio)*
- [ ] **7.4** Atlas spento e riacceso
- [ ] **7.5** Cadenza a 1: il dialogo compare, senza conferma non parte niente
- [ ] **7.6** I tetti mostrano le dimensioni vere; `0` si legge come «misura»
- [ ] **7.7** `compact` alzato: la nota del riavvio è sul posto, non in un toast
- [ ] **7.8** `/dream budget` e `/gardener settings` battuti a memoria rispondono la riga di migrazione
