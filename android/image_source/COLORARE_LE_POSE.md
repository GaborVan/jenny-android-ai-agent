# Guida rapida: colorare una posa e portarla sul telefono

Guida operativa passo-passo per il flusso "coloro (o ritocco) un sorgente →
rigenero i webp → li vedo sul dispositivo". Per il riferimento tecnico
completo (cosa fa ogni script, regole di scala, mapping runtime) vedi
[`README.md`](./README.md).

## 1. Tabella dei nomi file

Ogni posa ha **due sorgenti** in questa cartella, stesso canvas, stesso
soggetto: la line-art bianco/nero e il gemello colore. Modifica/sostituisci
il file **con lo stesso nome esatto** — lo script non ha bisogno di altro.

| Posa (runtime)      | Sorgente B/N              | Sorgente colore                  |
|---------------------|----------------------------|-----------------------------------|
| idle                | `idle.PNG`                 | `idle_color.PNG`                  |
| think               | `think.PNG`                | `think_color.PNG`                 |
| side                | `jenny-side.PNG`           | `jenny-side_color.PNG`            |
| side-talk           | `jenny-side-talk.PNG`      | `jenny-side-talk_color.PNG`       |
| talk1a (bocca aperta, mano alzata)  | `talk_1a.PNG` | `talk_1a_color.PNG`     |
| talk1b (bocca aperta, braccia giù) | `talk_1b.PNG` | `talk_1b_color.PNG`     |
| talk2a (bocca chiusa, mano alzata) | `talk_2a.PNG` | `talk_2a_color.PNG`     |
| talk2b (bocca chiusa, braccia giù) | `talk_2b.PNG` | `talk_2b_color.PNG`     |
| hang (appesa)       | `jenny-hang.PNG`           | `jenny-hang_color.PNG`            |
| fall (caduta)       | `jenny-fall.PNG`           | `jenny-fall_color.PNG`            |
| ground (atterrata)  | `jenny-ground.PNG`         | `jenny-ground_color.PNG`          |
| walk1               | `jenny-walk1.PNG`          | `jenny-walk1_color.PNG`           |
| walk2               | `jenny-walk2.PNG`          | `jenny-walk2_color.PNG`           |
| hello1              | `hello1.PNG`               | `hello1_color.PNG`                |
| hello2              | `hello2.PNG`               | `hello2_color.PNG`                |

L'**icona app** (`icon.png`) non ha variante colore: resta sempre bianco/nero,
non serve toccarla in questa guida.

## 2. Regole da rispettare quando esporti dal tool di disegno

- **Canvas esattamente 3000×3000**: lo script si ferma con un `assert` se non
  lo è.
- **Sfondo trasparente** (RGBA), non bianco pieno.
- **Stessa scala e stesso allineamento delle altre pose**: lo script non
  scala, non ritaglia e non ricentra nulla — se una posa esce più
  grande/piccola delle altre a runtime, il problema è nel PNG, non nello
  script.
- Colora pure solo qualche posa alla volta: quelle non ancora colorate
  restano identiche al B/N e va benissimo, il toggle mostrerà semplicemente
  B/N per quelle.

## 3. Rigenerare i webp

Dalla cartella `android/image_source/`:

```bash
python3 gen_pose_webp.py
```

Rigenera **tutti e 30** i webp (15 B/N + 15 colore) in
`jenny/templates/ui/assets/`, non solo quello che hai toccato — è normale e
voluto, è idempotente.

Se vedi `AssertionError: ... atteso canvas 3000x3000, trovato (...)` il
sorgente che hai salvato non è quadrato 3000×3000: ricontrolla l'export.

## 4. Caricare sul telefono

Verifica prima che il device sia collegato:

```bash
adb devices
```

Poi, dalla cartella `android/` (non da `image_source/`):

```bash
./gradlew app:installDebug
```

**Un riavvio dell'app non basta**: Chaquopy ri-estrae il bundle
`jenny/templates/ui` dentro l'APK solo a ogni installazione, quindi serve
davvero la build/install per vedere le immagini nuove sul dispositivo.

Poi, sul telefono: **Impostazioni → Personalizzazione → Mascotte a colori**
per attivare/disattivare il toggle e vedere la differenza.

## 5. Controllo rapido: quali pose sono già colorate?

I sorgenti `_color.PNG` partono come copie identiche del B/N (segnaposto) e
diventano "veri" solo quando li ricolori. Per sapere a colpo d'occhio quali
pose hai già colorato (byte diversi dal B/N):

```bash
# dalla cartella android/image_source/
for pair in "side:jenny-side" "side-talk:jenny-side-talk" "hang:jenny-hang" \
            "fall:jenny-fall" "ground:jenny-ground" "walk1:jenny-walk1" \
            "walk2:jenny-walk2" "hello1:hello1" "hello2:hello2" "idle:idle" \
            "think:think" "talk1a:talk_1a" "talk1b:talk_1b" \
            "talk2a:talk_2a" "talk2b:talk_2b"; do
  name="${pair%%:*}"; stem="${pair##*:}"
  bw=$(md5 -q "$stem.PNG"); col=$(md5 -q "${stem}_color.PNG")
  [ "$bw" = "$col" ] && echo "  $name: ancora B/N" || echo "✅ $name: COLORATO"
done
```

## Riepilogo one-liner

```bash
# dopo aver sostituito uno o più *_color.PNG (o *.PNG) in questa cartella:
cd android/image_source && python3 gen_pose_webp.py && cd .. && ./gradlew app:installDebug
```
