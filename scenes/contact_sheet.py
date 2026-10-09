"""Contact sheet of asset thumbnails -> build/asset_contact_sheet.png (run with .venv/bin/python)."""
import json, sys
from PIL import Image, ImageDraw
lib = json.load(open("assets/library.json"))["assets"]
ids = [a for a in lib if not a.startswith("FIXTURE")]
W, H, cols = 480, 270, 4
rows = (len(ids) + cols - 1) // cols
sheet = Image.new("RGB", (W * cols, (H + 22) * rows), "white")
d = ImageDraw.Draw(sheet)
for i, a in enumerate(ids):
    im = Image.open(lib[a]["thumbnail"]).convert("RGB").resize((W, H))
    x, y = (i % cols) * W, (i // cols) * (H + 22)
    sheet.paste(im, (x, y + 22))
    d.text((x + 6, y + 5), f"{lib[a]['part_number'][:34]}  [{lib[a]['family']}]", fill="black")
sheet.save("build/asset_contact_sheet.png")
print(sheet.size)
