"""Convert the project's master artwork to a Windows icon at native UI sizes."""
from pathlib import Path
from PIL import Image

root = Path(__file__).resolve().parent.parent
artwork = root / 'desktop_app/assets/app.png'
with Image.open(artwork) as source:
    source.convert('RGBA').save(artwork.with_suffix('.ico'),
                               sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                                      (64, 64), (128, 128), (256, 256)])
