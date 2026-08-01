"""Esporta l'arte di Jenny (riposo + volo Pegman) come webp per la WebUI.

Sorgenti: canvas QUADRATI 3000x3000 in questa cartella, disegnati
dall'artista gia' alla scala giusta e coerenti tra loro (personaggio della
stessa dimensione, teste allineate sul canvas). Qui NON si scala, NON si
ritaglia e NON si normalizza nulla: ogni webp e' il quadrato intero,
solo ridotto a SIZE x SIZE con lo stesso fattore per tutti (la scala
relativa tra le pose non viene mai toccata).

A runtime il layer di volo coincide esattamente col box del duo (tutte le
img sono width:100% del quadrato): l'unica costante calcolata qui a build
time e' il pivot della posa appesa — la punta della manica alzata (la
"mano"), misurata a mano sul canvas di jenny-hang.png perche' la sagoma
li' attorno e' ambigua (le ciocche superano la manica in altezza).

Output in jenny/templates/ui/assets/: jenny-{side,side-talk,hang,fall,
ground,walk1,walk2,hello1,hello2,idle,think,talk1a,talk1b,talk2a,
talk2b}.webp, tutti SIZE x SIZE. Nei sorgenti talk_* il numero indica la
bocca (1=aperta, 2=chiusa) e la lettera la posa (a=mano alzata, b=braccia
giu'): le coppie di animazione a runtime sono per posa (2a<->1a, 2b<->1b).

Ogni posa esiste in due varianti: bianco/nero (line-art, sorgente
<stem>.PNG) e colore (sorgente <stem>_color.PNG). La variante colore va nel
file jenny-<name>-color.webp; a runtime la scelta B/N<->colore e' una
preferenza client-side (shared/mascot.js) che rimappa il suffisso.
"""
from pathlib import Path

from PIL import Image

SRC = Path(__file__).resolve().parent
OUT = SRC.parent.parent / "jenny" / "templates" / "ui" / "assets"
SIZE = 768
QUALITY = 80

# Punta della manica alzata (la mano) sul canvas 3000x3000 di jenny-hang.png.
HAND_PIVOT = (1525, 1300)

FILES = [
    ("side", "jenny-side.png"),
    ("hang", "jenny-hang.png"),
    ("fall", "jenny-fall.png"),
    ("ground", "jenny-ground.png"),
    ("walk1", "jenny-walk1.png"),
    ("walk2", "jenny-walk2.png"),
    ("hello1", "hello1.png"),
    ("hello2", "hello2.png"),
    ("idle", "idle.png"),
    ("think", "think.png"),
    ("side-talk", "jenny-side-talk.png"),
    ("talk1a", "talk_1a.png"),
    ("talk1b", "talk_1b.png"),
    ("talk2a", "talk_2a.png"),
    ("talk2b", "talk_2b.png"),
]

def _export(src_png: Path, dest: Path) -> None:
    """Ridimensiona un sorgente 3000x3000 a SIZE x SIZE e salva in webp."""
    im = Image.open(src_png).convert("RGBA")
    assert im.size == (3000, 3000), f"{src_png.name}: atteso canvas 3000x3000, trovato {im.size}"
    im.resize((SIZE, SIZE), Image.LANCZOS).save(dest, "WEBP", quality=QUALITY)
    print(f"{dest.name}: {dest.stat().st_size // 1024} KB")


if __name__ == "__main__":
    for name, png in FILES:
        stem = Path(png).stem
        # Variante bianco/nero (line-art) + variante colore, per posa.
        _export(SRC / png, OUT / f"jenny-{name}.webp")
        _export(SRC / f"{stem}_color.PNG", OUT / f"jenny-{name}-color.webp")

    print(f"PIVOT_X = {HAND_PIVOT[0] / 3000:.4f}; PIVOT_Y = {HAND_PIVOT[1] / 3000:.4f}")
