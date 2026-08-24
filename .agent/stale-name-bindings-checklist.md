# Nomi liberati a metà — lista di esecuzione

Stato di [`stale-name-bindings-plan.md`](./stale-name-bindings-plan.md). Il
ragionamento sta là, qui c'è solo cosa è fatto. Si spunta quando è **girato**, e
per una garanzia che vive in un prompt «girato» include la calibrazione.

Ramo: `jenny-memory`. **Fatti il 24/08/2026: 3, 2, 4 e 1a.** 1b non serve — 1a passa.
L'unica cosa aperta è 1a.3, la seconda lettura con un altro modello, che questa
installazione non può dare.

---

## Passo 3 — il preset che nomina un provider cancellato *(il più piccolo)*

- [x] **3.1** Dentro `delete_provider::_apply`: i preset con `provider == <nome cancellato>` si vedono azzerare il campo
- [x] **3.2** Una riga di log con quanti preset sono stati toccati (silenzioso è peggio di rumoroso, qui)
- [x] **3.3** Test: due preset, uno nomina il provider e uno no → solo il primo cambia, e il preset **resta** (non si cancella)
- [x] **3.4** Test: il provider cancellato era anche `providers.default` → entrambe le riparazioni nella stessa `mutate`

## Passo 2 — `cron/runs/`

- [x] **2.1** `remove_job()` toglie i `runs/<job_id>_*.json` di quel job
- [x] **2.2** Test: due job con record, se ne cancella uno → restano **solo** quelli dell'altro *(il caso che un match per sottostringa sbaglierebbe)*
- [x] **2.3** Potatura al `start()`: si tengono i 500 più recenti, ordinati per il `<ms>` nel nome del file
- [x] **2.4** Test della potatura **senza toccare l'orologio**: i nomi si scrivono a mano con i loro `<ms>`
- [x] **2.5** Test: un nome che non ha la forma `<id>_<ms>_<rand>` non fa saltare la potatura né viene cancellato per sbaglio

## Passo 1a — la sonda su Atlas *(nessun codice; dice se 1b serve)*

- [x] **1a.1** Scritta come quinta sonda in [`memory-probes.md`](./memory-probes.md), stessa forma delle altre
- [x] **1a.2** Girata sul telefono 24/08: **passa**, e in entrambe le direzioni (riga tolta *e* riga mancante aggiunta)
- [ ] **1a.3** Girata contro **un secondo modello** — **non fatta, e non per svista**: questa installazione ha
      un provider solo (`deepseek`) e nessun preset, quindi un secondo lettore non c'è senza aggiungere un
      provider e una chiave. Da rifare **prima** di fidarsi il giorno che si cambia modello
- [x] **1a.4** Esito scritto in [`stale-name-bindings-plan.md`](./stale-name-bindings-plan.md) — non in
      `memory-plan.md`: questa sonda sorveglia Atlas, non una fase della memoria

## Passo 1b — la deriva calcolata *(**non serve**: 1a passa)*

> Nessuna di queste caselle va spuntata. Restano scritte perché una sonda è una
> misura di oggi: se un giorno dà l'altro esito, la correzione è già pensata.

- [ ] **1b.1** Estrattore *di sola lettura* delle voci `## Wikis` di `WIKI.md` (formato fissato da `atlas.md`: `→ wikis/<slug>/wiki/index.md`)
- [ ] **1b.2** Differenza fra insiemi contro `discover_wiki_roots`, nei due versi: righe senza cartella, cartelle senza riga
- [ ] **1b.3** La differenza entra nel prompt di Atlas accanto all'inventario. **Nessuna scrittura su `WIKI.md` da codice**
- [ ] **1b.4** Test: un `WIKI.md` storto non fa sollevare niente e non elenca nulla *(leggere deve fallire piano)*
- [ ] **1b.5** Rigirare 1a: adesso la riga sparisce?
- [ ] **1b.6** Calibrare: togliere la lista calcolata e vedere che il difetto torni

## Passo 4 — `disabled_skills`

- [x] **4.1** Nessuna correzione. Resta scritto nel piano *(spuntabile subito: è la decisione, non il lavoro)*

---

## Cancello per ognuno

- [x] *(3 e 2)* Gira con `ruff` pulito, pyright a zero sul sottoinsieme bloccante, e la suite verde
- [x] *(3 e 2)* Il test è stato visto **fallire** togliendo la garanzia, prima di essere considerato buono
- [x] *(3)* Niente `config.json` scritto fuori da `store.mutate()`
- [ ] Un commit per passo: 3, 2 e 1 non dipendono l'uno dall'altro e non devono viaggiare insieme

---

## Calibrazione, 24/08/2026

Nessuna garanzia è stata data per buona senza vederla cadere.

| garanzia tolta | cosa si è rotto |
| --- | --- |
| i preset non vengono ripuntati | 3 test su 6 — gli altri 3 sono i controlli, e restano verdi apposta |
| `remove_job` non tocca i record | 2 test |
| confronto per sottostringa invece che per segmento | 1 test (`ab` si portava via `abcd`) |
| i nomi non capiti vengono cancellati lo stesso | 1 test |
