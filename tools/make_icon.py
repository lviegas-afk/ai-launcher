"""Genera los íconos de la app. Uso: make_icon.py <carpeta_salida>

- AppIcon.icns  (macOS, sólo si existe `iconutil`)
- AppIcon.ico   (Windows)
- menubar.png   (barra de menú de macOS, imagen "template" en negro)
- tray.png      (bandeja de Windows)
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)

# rayo en coordenadas 0..1
BOLT = [(0.58, 0.06), (0.22, 0.56), (0.47, 0.56), (0.40, 0.94), (0.78, 0.40), (0.53, 0.40), (0.62, 0.06)]


def app_icon(size=1024):
    s = size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    # fondo con degradé dentro de un "squircle" al estilo macOS (margen de 10%)
    m, r = int(s * 0.1), int(s * 0.18)
    grad = Image.new("RGBA", (s, s))
    top, bottom = (99, 102, 241), (168, 85, 247)
    px = grad.load()
    for y in range(s):
        t = y / (s - 1)
        c = tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3)) + (255,)
        for x in range(s):
            px[x, y] = c
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle((m, m, s - m, s - m), r, fill=255)
    shadow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((m, m + s * 0.012, s - m, s - m + s * 0.012), r, fill=(0, 0, 0, 90))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(s * 0.015)))
    img.paste(grad, (0, 0), mask)
    # rayo
    inner = s - 2 * m
    pts = [(m + inner * (0.12 + 0.76 * x), m + inner * (0.12 + 0.76 * y)) for x, y in BOLT]
    glow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(glow).polygon(pts, fill=(255, 255, 255, 120))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(s * 0.03)))
    ImageDraw.Draw(img).polygon(pts, fill=(255, 255, 255, 255))
    return img


def menubar_icon(px=36):
    img = Image.new("RGBA", (px * 4, px * 4), (0, 0, 0, 0))
    S = px * 4
    pts = [(S * (0.1 + 0.8 * x), S * (0.04 + 0.92 * y)) for x, y in BOLT]
    ImageDraw.Draw(img).polygon(pts, fill=(0, 0, 0, 255))
    return img.resize((px, px), Image.LANCZOS)


def tray_icon(px=64):
    # sin margen: en la bandeja de Windows el ícono es muy chico
    img = app_icon(512)
    return img.crop((51, 51, 461, 461)).resize((px, px), Image.LANCZOS)


base = app_icon()
if shutil.which("iconutil"):
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "AppIcon.iconset"
        iconset.mkdir()
        for sz in (16, 32, 128, 256, 512):
            base.resize((sz, sz), Image.LANCZOS).save(iconset / ("icon_%dx%d.png" % (sz, sz)))
            base.resize((sz * 2, sz * 2), Image.LANCZOS).save(iconset / ("icon_%dx%d@2x.png" % (sz, sz)))
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out / "AppIcon.icns")], check=True)
base.crop((80, 80, 944, 944)).save(out / "AppIcon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                                                                (64, 64), (128, 128), (256, 256)])
menubar_icon().save(out / "menubar.png")
tray_icon().save(out / "tray.png")
base.resize((256, 256), Image.LANCZOS).save(out / "icon_preview.png")
print("íconos en", out)
