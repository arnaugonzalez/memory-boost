"""Render docs/logo-400.png (Cline / Glama logo) in the docs/social.png palette.

    uv run --no-project --with pillow python scripts/render-logo.py docs/logo-400.png
"""
import sys

from PIL import Image, ImageDraw, ImageFont

out = sys.argv[1]
S = 4  # supersample
W = 400 * S
img = Image.new("RGB", (W, W), "#1e1e2e")
d = ImageDraw.Draw(img)
bold = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
reg = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
pad = 40 * S
d.rounded_rectangle([pad - 4*S, 92*S, W - pad + 4*S, 300*S], radius=18*S,
                    fill="#181825", outline="#313244", width=2*S)
f_big = ImageFont.truetype(bold, 78 * S)
f_small = ImageFont.truetype(reg, 24 * S)
d.text((pad + 16*S, 110*S), "memory", font=f_big, fill="#f5e0dc")
d.text((pad + 16*S, 195*S), "-boost", font=f_big, fill="#f5e0dc")
d.text((pad + 16*S, 48*S), "$ drift", font=f_small, fill="#89b4fa")
d.text((pad + 16*S, 318*S), "reads it back.", font=f_small, fill="#a6e3a1")
img.resize((400, 400), Image.LANCZOS).save(out, optimize=True)
