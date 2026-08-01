#!/usr/bin/env python3
"""Generate all Android launcher + notification icons from icon.png.

Source `icon.png` is the Jenny mascot: an opaque white face with black line-art
details on a transparent background. On a black background the mascot reads as-is
(white face floats on black, dark features sit on top of the white fill), so the
launcher/large icons need no colour inversion.

Outputs (all under app/src/main/res/):
  A) mipmap-<dpi>/ic_launcher_foreground.png  adaptive foreground (mascot only)
  B) drawable-<dpi>/ic_stat_jenny.png  monochrome white status-bar silhouette
  C) drawable-nodpi/ic_notification_large.png  black bg + mascot (expanded notif)

No legacy raster launcher icons (ic_launcher.png / ic_launcher_round.png): with
minSdk 26 every device uses the adaptive icon, so legacy PNGs would only add a
second, square-cropped rendering in some surfaces and clash with the adaptive
(masked) one. Adaptive icon = single source of truth.

Re-run after tweaking any tuning constant; it is idempotent.
"""

from pathlib import Path

from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "icon.png"
RES = ROOT.parent / "app" / "src" / "main" / "res"

BLACK = (0, 0, 0, 255)

# density bucket -> px for a 108dp adaptive canvas / 24dp stat icon
FOREGROUND = {"mdpi": 108, "hdpi": 162, "xhdpi": 216, "xxhdpi": 324, "xxxhdpi": 432}
STAT = {"mdpi": 24, "hdpi": 36, "xhdpi": 48, "xxhdpi": 72, "xxxhdpi": 96}


def load_mascot() -> Image.Image:
    """Load icon.png and tightly crop to its non-transparent bounding box."""
    im = Image.open(SRC).convert("RGBA")
    bbox = im.getbbox()
    return im.crop(bbox)


def fit(mascot: Image.Image, size: int, fraction: float) -> Image.Image:
    """Return a size x size transparent canvas with mascot scaled+centered."""
    target = max(1, int(round(size * fraction)))
    w, h = mascot.size
    scale = target / max(w, h)
    resized = mascot.resize(
        (max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS
    )
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(resized, ((size - resized.width) // 2, (size - resized.height) // 2), resized)
    return canvas


def make_stat_master(mascot: Image.Image) -> Image.Image:
    """Clean white silhouette: filled head with eyes/mouth punched out.

    Built at a fixed working resolution so the morphological kernel size is
    predictable. The head shape = every opaque source pixel. Feature holes =
    dark line pixels AFTER an opening that removes thin strokes (outline, hair,
    lashes) while keeping thick masses (eyes, mouth).
    """
    work = 512
    w, h = mascot.size
    scale = (work * 0.9) / max(w, h)
    m = mascot.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
    canvas = Image.new("RGBA", (work, work), (0, 0, 0, 0))
    canvas.paste(m, ((work - m.width) // 2, (work - m.height) // 2), m)

    r, g, b, a = canvas.split()
    alpha = a.point(lambda v: 255 if v > 96 else 0)
    lum = Image.merge("RGB", (r, g, b)).convert("L")
    # dark = opaque AND low luminance (the black line-art)
    dark = Image.new("L", (work, work), 0)
    dark.paste(lum.point(lambda v: 255 if v < 110 else 0), (0, 0), alpha)
    # opening (erode then dilate) removes thin strokes, keeps eye/mouth masses
    k = 9
    opened = dark.filter(ImageFilter.MinFilter(k)).filter(ImageFilter.MaxFilter(k))

    out_alpha = Image.new("L", (work, work), 0)
    out_alpha.paste(alpha)  # start from full head silhouette
    # punch feature holes to transparent
    from PIL import ImageChops

    out_alpha = ImageChops.subtract(out_alpha, opened)
    white = Image.new("RGBA", (work, work), (255, 255, 255, 0))
    white.putalpha(out_alpha)
    return white


def save(img: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    print(f"  {path.relative_to(ROOT.parent)}  ({img.width}x{img.height})")


def main() -> None:
    mascot = load_mascot()
    print(f"source mascot cropped to {mascot.size}")

    # 0.54: the whole mascot (antenna + hair included) sits fully inside the
    # launcher's circular mask with an even black margin — nothing is clipped.
    # The mask circle measures ~0.76 of the canvas, whose inscribed square caps
    # the mascot's max dimension at ~0.54 of the canvas.
    print("A) adaptive foreground")
    for dpi, size in FOREGROUND.items():
        save(fit(mascot, size, 0.54), RES / f"mipmap-{dpi}" / "ic_launcher_foreground.png")

    print("B) status-bar silhouette")
    master = make_stat_master(mascot)
    for dpi, size in STAT.items():
        pad = max(1, round(size * 0.08))
        inner = size - 2 * pad
        s = master.resize((inner, inner), Image.LANCZOS)
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        canvas.paste(s, (pad, pad), s)
        save(canvas, RES / f"drawable-{dpi}" / "ic_stat_jenny.png")

    print("C) notification large icon")
    big = Image.new("RGBA", (256, 256), BLACK)
    big.alpha_composite(fit(mascot, 256, 0.80))
    save(big, RES / "drawable-nodpi" / "ic_notification_large.png")

    print("done.")


if __name__ == "__main__":
    main()
