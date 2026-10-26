"""Fonts for drawing synthetic scanned pages in the tests."""
import os

from PIL import ImageFont

FONT_DIRS = ("/usr/share/fonts/truetype/dejavu/",       # Linux
             "/System/Library/Fonts/Supplemental/")      # macOS
FONT_FILES = {(False, False): ("DejaVuSans.ttf", "Arial.ttf"),
              (True, False): ("DejaVuSans-Bold.ttf", "Arial Bold.ttf"),
              (False, True): ("DejaVuSerif.ttf", "Times New Roman.ttf")}


def font(size, bold=False, serif=False):
    """A real TrueType font at `size` px for drawing scanned pages: DejaVu on Linux,
    Arial/Times on macOS. Pillow's tiny default font is too small for OCR."""
    for d in FONT_DIRS:
        for name in FONT_FILES[(bold, serif)]:
            if os.path.exists(d + name):
                return ImageFont.truetype(d + name, size)
    try:
        return ImageFont.load_default(size)      # Pillow >= 10.1
    except TypeError:
        return ImageFont.load_default()
