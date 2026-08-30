# L'emulatore quadrato — creazione e uso

Un AVD che imita la geometria del Titan 2 (1440×1440 fisici, 480 dpi → 480×480 px
CSS) per misurare inset e gesture quando il telefono non è collegato.

**Non è il Titan 2.** Stesso schermo, altro Android (API 37 / Android 17), altra
shell di sistema, altre impostazioni di navigazione. Vale per capire le grandezze
in gioco e per far girare la UI; non chiude una domanda sul telefono vero.

---

## Prerequisiti d'ambiente

```bash
export ANDROID_HOME=$HOME/Library/Android/sdk
```

Serve ogni volta: **la variabile non è nell'ambiente e non esiste
`android/local.properties`** — senza il prefisso, `gradlew` muore con
"SDK location not found".

`emulator` non è nel PATH: si invoca come `$ANDROID_HOME/emulator/emulator`.

**`avdmanager` non è installato in questo SDK** (niente `cmdline-tools/`, niente
`tools/bin/`). Per questo l'AVD qui sotto si crea a mano scrivendo i due file
che `avdmanager` avrebbe scritto: l'emulatore crea da sé le immagini disco al
primo avvio, partendo dalla system image.

Immagine di sistema disponibile: **una sola**,
`system-images/android-37.0/google_apis_playstore_ps16k/arm64-v8a/`.

---

## Creare l'AVD (una volta sola)

```bash
export ANDROID_HOME=$HOME/Library/Android/sdk
AVD=$HOME/.android/avd/jenny_square.avd
mkdir -p "$AVD"

cat > "$HOME/.android/avd/jenny_square.ini" <<EOF
avd.ini.encoding=UTF-8
path=$AVD
path.rel=avd/jenny_square.avd
target=android-37.0
EOF

cat > "$AVD/config.ini" <<'EOF'
AvdId=jenny_square
PlayStore.enabled=false
abi.type=arm64-v8a
avd.ini.displayname=jenny_square
avd.ini.encoding=UTF-8
disk.dataPartition.size=6G
fastboot.forceColdBoot=no
fastboot.forceFastBoot=yes
hw.accelerometer=yes
hw.audioInput=yes
hw.battery=yes
hw.camera.back=none
hw.camera.front=none
hw.cpu.arch=arm64
hw.cpu.ncore=4
hw.dPad=no
hw.gps=yes
hw.gpu.enabled=yes
hw.gpu.mode=auto
hw.gyroscope=yes
hw.initialOrientation=portrait
hw.keyboard=yes
hw.lcd.density=480
hw.lcd.height=1440
hw.lcd.width=1440
hw.mainKeys=no
hw.ramSize=3072
hw.sdCard=no
hw.sensors.light=yes
hw.sensors.orientation=yes
hw.sensors.proximity=yes
hw.trackBall=no
image.sysdir.1=system-images/android-37.0/google_apis_playstore_ps16k/arm64-v8a/
runtime.network.latency=none
runtime.network.speed=full
showDeviceFrame=no
skin.dynamic=yes
tag.display=Google APIs PlayStore
tag.id=google_apis_playstore
target=android-37.0
vm.heapSize=256
EOF
```

Le tre righe che contano sono `hw.lcd.width` / `hw.lcd.height` / `hw.lcd.density`.
Nessun `hw.device.name`: un profilo di dispositivo riscriverebbe la geometria.

Verifica: `$ANDROID_HOME/emulator/emulator -list-avds` deve elencare
`jenny_square`.

---

## Avviarlo

```bash
export ANDROID_HOME=$HOME/Library/Android/sdk
nohup $ANDROID_HOME/emulator/emulator -avd jenny_square \
  -no-window -no-audio -no-boot-anim -no-snapshot \
  -port 5556 -gpu swiftshader_indirect > /tmp/emu.log 2>&1 &
```

Porta fissa 5556 → **serial `emulator-5556`**. Pinnarlo sempre:

```bash
export ANDROID_SERIAL=emulator-5556
adb devices -l          # deve mostrare un solo dispositivo
adb wait-for-device
until [ "$(adb shell getprop sys.boot_completed | tr -d '\r')" = "1" ]; do sleep 3; done
```

Primo avvio a freddo: ~1 minuto (`Boot completed in 13256 ms` nel log
dell'emulatore, poi il resto del sistema).

`-no-window` è headless. Per vedere lo schermo senza aprire la finestra:

```bash
adb exec-out screencap -p > /tmp/shot.png
```

Spegnerlo: `adb emu kill`.

### Verifica della geometria (prima di misurare qualunque cosa)

```bash
adb shell wm size      # atteso: Physical size: 1440x1440
adb shell wm density   # atteso: Physical density: 480
```

Se non tornano questi due numeri, l'AVD non ha rispettato il `config.ini` e ogni
misura successiva è sbagliata.

`wm size 1440x2200` / `wm size reset` servono per il test di controllo sulle
media query CSS legate all'altezza del viewport.

---

## Navigazione: gesture ↔ tre pulsanti

**Sull'immagine `android-37.0` la modalità di partenza è già gesture**
(`navigation_mode = 2`, overlay `…navbar.gestural` attivo).

```bash
# → gesture
adb shell cmd overlay enable  com.android.internal.systemui.navbar.gestural
adb shell cmd overlay disable com.android.internal.systemui.navbar.threebutton

# → tre pulsanti
adb shell cmd overlay enable  com.android.internal.systemui.navbar.threebutton

# verifica: 0 = tre pulsanti, 2 = gesture
adb shell settings get secure navigation_mode
adb shell cmd overlay list | grep navbar
```

Attenzione: `enable` non disabilita l'altro overlay. Con `gestural` e
`threebutton` entrambi `[x]`, `navigation_mode` resta `2` — per tornare davvero
ai tre pulsanti bisogna disabilitare `gestural`, o riabilitarlo per tornare a
gesture. Meglio spegnere esplicitamente quello che non si vuole.

---

## Build e installazione

```bash
cd <worktree>/android          # MAI dall'albero principale: srcDir("../../")
export ANDROID_HOME=$HOME/Library/Android/sdk
export ANDROID_SERIAL=emulator-5556
./gradlew app:installDebug
```

Solo **debug**: `assembleRelease` richiede `android/keystore.properties`, che è
gitignored e nei worktree non c'è. L'emulatore è vergine, il debug si installa
senza conflitti di firma.

Avvio e log:

```bash
adb shell monkey -p com.flagdizero.jenny -c android.intent.category.LAUNCHER 1
adb logcat -d -s Jenny:V python.stderr:V AndroidRuntime:E
```

L'immagine è `arm64-v8a` e l'APK debug contiene quell'ABI: Chaquopy parte
(gateway su `127.0.0.1:18790`, WebUI caricata). Il gateway si avvia **senza
provider configurato** e resta in attesa dell'onboarding: la SPA mostra il
wizard di primo avvio, non la chat.

---

## Vedi anche

- [`apps-drawer-plan.md`](./apps-drawer-plan.md) — le misure raccolte qui, e cosa
  resta ignoto sul Titan 2
