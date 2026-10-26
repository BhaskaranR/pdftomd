#!/usr/bin/env python3
"""
pdf2md — PDF to Markdown converter.

A Python port of the pipeline behind Firecrawl's PDF scraping
(firecrawl/firecrawl apps/api/native/src/pdf.rs -> the MIT-licensed
`pdf-inspector` crate), rebuilt on pdfplumber/pdfminer for low-level parsing.

Pipeline (mirrors pdf-inspector):
  1. classify   – TextBased / Scanned / ImageBased / Mixed + confidence,
                  per-page OCR routing                  (detector.rs)
  2. extract    – position-aware words with font, size, bold/italic/mono
  3. tables     – rectangle/ruling-line tables, then text-alignment
                  (heuristic) tables                      (tables/*.rs)
  4. layout     – multi-column detection and reading order
  5. furniture  – strip repeated running headers/footers and page numbers
                                                          (markdown/furniture.rs)
  6. analysis   – body font size (with base-size correction), heading
                  tiers H1–H4 by font-size ratio          (markdown/analysis.rs)
  7. convert    – paragraphs, headings, lists, code blocks, bold/italic,
                  links, tables, images                   (markdown/convert.rs)
  8. postprocess – dehyphenation, whitespace cleanup      (markdown/postprocess.rs)

Additions over pdf-inspector: embedded images are extracted (saved to a
folder or inlined as base64), and scanned pages can be OCR'd with Tesseract.

Usage:
  python pdf2md.py input.pdf                      # markdown to stdout
  python pdf2md.py input.pdf -o out.md            # images saved to out_images/
  python pdf2md.py https://example.com/doc.pdf -o out.md
  python pdf2md.py input.pdf --images embed       # base64 data URIs
  python pdf2md.py input.pdf --images none --page-range 1-5 --mode ocr
  python pdf2md.py prepare input.pdf work/       # vision step, see section 12
  python pdf2md.py merge work/ -o out.md

Library:
  from pdf2md import convert
  result = convert("input.pdf", image_dir="images")
  print(result.markdown, result.pdf_type, result.confidence)
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import math
import os
import re
import statistics
import sys
import tempfile
import time
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Optional

import pdfplumber

# ───────────────────────────── constants (from pdf-inspector) ──────────────

TEXT_PAGE_RATIO_THRESHOLD = 0.6     # detector.rs DetectionConfig default
MIN_TEXT_CHARS_PER_PAGE = 20        # below this a page is "no text"
HEADING_RATIO = 1.2                 # analysis.rs heading gate
BOLD_TIER_RATIO = 1.05              # analysis.rs bold fallback
TIER_TOLERANCE = 0.5                # sizes within 0.5pt share a tier
MAX_TIERS = 4
EDGE_LINE_COUNT = 5                 # furniture.rs
FURNITURE_MIN_PAGES = 3
FURNITURE_PAGE_FRACTION = 0.30
FURNITURE_Y_STDDEV = 0.05
MIN_IMAGE_PT = 24                   # ignore tiny images (icons, bullets, rules)

BULLETS = "•●○◦▪▫■□‣\u2043∙·◆◇►▸➢➤✓✔-–—*"
BULLET_RE = re.compile(r"^\s*([•●○◦▪▫■□‣\u2043∙·◆◇►▸➢➤]|[-✓✔–—*](?=\s))\s*")   # ticks/dashes need a space
NUMBERED_RE = re.compile(
    r"^\s*(\(?(\d{1,3}|[a-zA-Z]|[ivxlcdm]{1,6})[.)])\s+(?=\S)", re.I)
MONO_RE = re.compile(r"mono|courier|consol|menlo|inconsolata|code|typewriter|"
                     r"lucidaconsole|sourcecodepro|firacode|dejavusansm", re.I)
BOLD_RE = re.compile(r"bold|black|heavy|semibold|demibold|extrabold|,b\b|-b$|\bbd\b", re.I)
ITALIC_RE = re.compile(r"italic|oblique|,i\b|-it$|\bit\b", re.I)
URL_RE = re.compile(r"https?://\S+")
CID_RE = re.compile(r"\(cid:\d+\)")
CID_MARK = "\x00cid"


# ───────────────────────────── data types ─────────────────────────────────

@dataclass
class Word:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    size: float
    font: str
    bold: bool
    italic: bool
    mono: bool
    link: Optional[str] = None
    dropcap: bool = False


@dataclass
class Line:
    page: int
    words: list
    x0: float
    x1: float
    top: float
    bottom: float
    kind: str = "text"        # text | table | image | ocr | chart
    payload: object = None    # table rows / image ref / ocr text / chart info
    colw: float = 0.0         # width of the column this line sits in
    region: int = 0           # layout region (side-by-side blocks) it belongs to

    @property
    def text(self) -> str:
        return join_words(self.words)

    @property
    def size(self) -> float:
        return dominant_size(self.words)

    @property
    def bold(self) -> bool:
        return mass_fraction(self.words, lambda w: w.bold) >= 0.5

    @property
    def all_bold(self) -> bool:
        return mass_fraction(self.words, lambda w: w.bold) >= 0.95

    @property
    def mono(self) -> bool:
        return mass_fraction(self.words, lambda w: w.mono) >= 0.9


@dataclass
class ImageRef:
    page: int
    index: int
    bbox: tuple
    path: Optional[str] = None
    data_uri: Optional[str] = None
    alt: str = ""


@dataclass
class Result:
    markdown: str
    pdf_type: str
    page_count: int
    confidence: float
    pages_needing_ocr: list
    title: Optional[str]
    processing_time_ms: float
    images: list = field(default_factory=list)
    is_complex: bool = False
    pages_with_tables: list = field(default_factory=list)    # pdf-inspector's names
    pages_with_columns: list = field(default_factory=list)   # (columns or side-by-side blocks)
    num_pages: int = 0
    pages: list = field(default_factory=list)
    blocks: list = field(default_factory=list)
    source_name: str = ""
    warnings: list = field(default_factory=list)


# ───────────────────────────── small helpers ──────────────────────────────

def mass_fraction(words, pred) -> float:
    total = sum(len(w.text) for w in words)
    if not total:
        return 0.0
    return sum(len(w.text) for w in words if pred(w)) / total


def dominant_size(words) -> float:
    """Character-weighted dominant font size (heading.rs dominant_font_size)."""
    c = Counter()
    for w in words:
        c[round(w.size * 2) / 2] += len(w.text)
    return c.most_common(1)[0][0] if c else 0.0


def join_words(words) -> str:
    out = []
    prev = None
    for w in words:
        if prev is not None:
            gap = w.x0 - prev.x1
            if gap > max(prev.size, w.size) * 0.12:
                out.append(" ")
        out.append(w.text)
        prev = w
    return "".join(out)


def overlaps(a, b, pad=0.0) -> bool:
    return not (a[2] < b[0] - pad or a[0] > b[2] + pad or
                a[3] < b[1] - pad or a[1] > b[3] + pad)


def center_inside(word_box, box, pad=1.0) -> bool:
    cx = (word_box[0] + word_box[2]) / 2
    cy = (word_box[1] + word_box[3]) / 2
    return box[0] - pad <= cx <= box[2] + pad and box[1] - pad <= cy <= box[3] + pad


def parse_pages(spec: Optional[str], n: int) -> list:
    if not spec:
        return list(range(n))
    pages = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            a = int(a) if a else 1
            b = int(b) if b else n
            pages.update(range(a - 1, min(b, n)))
        elif part:
            pages.add(int(part) - 1)
    return sorted(p for p in pages if 0 <= p < n)


def md_escape_cell(s: str) -> str:
    s = (s or "").replace("\n", " ").replace("|", "\\|")
    return re.sub(r"\s+", " ", s).strip()


def table_to_md(rows) -> str:
    rows = [[md_escape_cell(c) for c in r] for r in rows]
    rows = [r for r in rows if any(c for c in r)]
    if not rows:
        return ""
    ncol = max(len(r) for r in rows)
    rows = [r + [""] * (ncol - len(r)) for r in rows]
    # drop columns that are empty in every row (artifact of merged cells)
    keep = [j for j in range(ncol) if any(r[j] for r in rows)]
    rows = [[r[j] for j in keep] for r in rows]
    if not rows or not rows[0]:
        return ""
    out = ["| " + " | ".join(rows[0]) + " |",
           "| " + " | ".join("---" for _ in rows[0]) + " |"]
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(out)


# ───────────────────────────── 1. classification ──────────────────────────

def image_coverage(page) -> float:
    area = page.width * page.height or 1
    cov = 0.0
    px0, ptop, px1, pbottom = page.bbox
    for im in page.images:
        w = max(0, min(im["x1"], px1) - max(im["x0"], px0))
        h = max(0, min(im["bottom"], pbottom) - max(im["top"], ptop))
        cov += w * h
    return min(cov / area, 1.0)


SCAN_COVERAGE = 0.8          # an image this big is treated as a page scan
SCAN_MAX_NATIVE_CHARS = 300  # ...unless the page also carries this much real text
SCAN_MAX_MARKS = 20          # ...or the image has (almost) no marks: a plain background
INK_DPI = 100


def image_marks(page) -> int:
    """Pixels (at INK_DPI) where the page has a sharp mark of its own: darker or lighter than
    its blurred surroundings, outside the native text. A plain or gradient background has
    none; any print, stamp or signature in a scan has many."""
    from PIL import Image, ImageChops, ImageDraw, ImageFilter
    img = page.to_image(resolution=INK_DPI).original.convert("L")
    blur = 5
    sharp = ImageChops.difference(img, img.filter(ImageFilter.GaussianBlur(blur))).point(
        lambda v: 255 if v > 32 else 0)
    keep = Image.new("L", img.size, 255)
    k, (x0, y0) = INK_DPI / 72, page.bbox[:2]
    d = ImageDraw.Draw(keep)
    pad = 3 * blur                             # the blur spreads each stroke's difference this far
    for c in page.chars:                       # native text is not the image's ink
        d.rectangle(((c["x0"] - x0) * k - pad, (c["top"] - y0) * k - pad,
                     (c["x1"] - x0) * k + pad, (c["bottom"] - y0) * k + pad), fill=0)
    return ImageChops.multiply(sharp, keep).histogram()[255]


def page_stats(page) -> dict:
    """Per-page routing (detector.rs per-page sampling).
    kind: "text"   - real text layer, use native extraction
          "hybrid" - scan image plus a little native text (e.g. a typed header):
                     native text is kept and the scan is OCR'd
          "scan"   - no usable text, needs OCR
          "blank"  - nothing on the page
    A little native text on a plain background image (a cover title, a
    letterhead) is "text": the image has no marks of its own to OCR."""
    chars = sum(1 for c in page.chars if not c["text"].isspace())
    cov = image_coverage(page)
    has_graphics = bool(page.images) or len(page.curves) > 20
    if chars == 0:
        kind = "scan" if has_graphics else "blank"
    elif cov >= SCAN_COVERAGE and chars < SCAN_MAX_NATIVE_CHARS:
        kind = "hybrid"
    elif chars < MIN_TEXT_CHARS_PER_PAGE and cov >= 0.5:
        kind = "hybrid"
    else:
        kind = "text"
    if kind == "hybrid" and image_marks(page) <= SCAN_MAX_MARKS:
        kind = "text"
    return {"kind": kind, "chars": chars, "coverage": cov}


def classify(stats: list) -> tuple:
    """stats: [(pageno, page_stats)] -> (type, confidence, pages_needing_ocr)."""
    content = [(p, st) for p, st in stats if st["kind"] != "blank"]
    needs_ocr = [p for p, st in content if st["kind"] in ("scan", "hybrid")]
    if not content:
        return ("Empty", 0.0, [])
    text_pages = sum(1 for _, st in content if st["kind"] == "text")
    image_only = sum(1 for _, st in content if st["kind"] != "text" and st["coverage"] >= 0.5)
    n = len(content)
    ratio = text_pages / n
    if ratio >= TEXT_PAGE_RATIO_THRESHOLD:
        return ("TextBased", round(ratio, 3), needs_ocr)
    if text_pages == 0:
        if image_only == n:
            return ("Scanned", 0.95, needs_ocr)
        return ("ImageBased", 0.8, needs_ocr)
    return ("Mixed", 0.7, needs_ocr)


# ───────────────────────────── 2. extraction ──────────────────────────────

# ---- symbol fonts (port of pdf-inspector base14.rs / fonts.rs) ----
# Symbol and ZapfDingbats read through their own built-in encodings, which pdfminer doesn't apply
# (code 0x61 is α, not a). Word writes symbol fonts as private-use U+F000+code; those go through the
# font's own table when it is a known symbol font (Symbol, ZapfDingbats, Wingdings 1-3 — the Wingdings
# codes are Unicode's mapping, UTC L2/12-368), otherwise through pdf-inspector's rule (bullets and a
# check mark, else the plain character: text fonts that store ordinary letters there).
SYMBOL_FONT_TABLES = {   # font code -> Unicode, rows of 16 codes (2x = 0x20-0x2F), - = no glyph
    "symbol": """
    2x: 20 21 2200 23 2203 25 26 220B 28 29 2217 2B 2C 2212 2E 2F
    3x: 30 31 32 33 34 35 36 37 38 39 3A 3B 3C 3D 3E 3F
    4x: 2245 391 392 3A7 2206 395 3A6 393 397 399 3D1 39A 39B 39C 39D 39F
    5x: 3A0 398 3A1 3A3 3A4 3A5 3C2 2126 39E 3A8 396 5B 2234 5D 22A5 5F
    6x: F8E5 3B1 3B2 3C7 3B4 3B5 3C6 3B3 3B7 3B9 3D5 3BA 3BB B5 3BD 3BF
    7x: 3C0 3B8 3C1 3C3 3C4 3C5 3D6 3C9 3BE 3C8 3B6 7B 7C 7D 223C -
    Ax: 20AC 3D2 2032 2264 2044 221E 192 2663 2666 2665 2660 2194 2190 2191 2192 2193
    Bx: B0 B1 2033 2265 D7 221D 2202 2022 F7 2260 2261 2248 2026 F8E6 F8E7 21B5
    Cx: 2135 2111 211C 2118 2297 2295 2205 2229 222A 2283 2287 2284 2282 2286 2208 2209
    Dx: 2220 2207 F6DA F6D9 F6DB 220F 221A 22C5 AC 2227 2228 21D4 21D0 21D1 21D2 21D3
    Ex: 25CA 2329 F8E8 F8E9 F8EA 2211 F8EB F8EC F8ED F8EE F8EF F8F0 F8F1 F8F2 F8F3 F8F4
    Fx: - 232A 222B 2320 F8F5 2321 F8F6 F8F7 F8F8 F8F9 F8FA F8FB F8FC F8FD F8FE -""",
    "zapfdingbats": """
    2x: 20 2701 2702 2703 2704 260E 2706 2707 2708 2709 261B 261E 270C 270D 270E 270F
    3x: 2710 2711 2712 2713 2714 2715 2716 2717 2718 2719 271A 271B 271C 271D 271E 271F
    4x: 2720 2721 2722 2723 2724 2725 2726 2727 2605 2729 272A 272B 272C 272D 272E 272F
    5x: 2730 2731 2732 2733 2734 2735 2736 2737 2738 2739 273A 273B 273C 273D 273E 273F
    6x: 2740 2741 2742 2743 2744 2745 2746 2747 2748 2749 274A 274B 25CF 274D 25A0 274F
    7x: 2750 2751 2752 25B2 25BC 25C6 2756 25D7 2758 2759 275A 275B 275C 275D 275E -
    8x: 2768 2769 276A 276B 276C 276D 276E 276F 2770 2771 2772 2773 2774 2775 - -
    Ax: - 2761 2762 2763 2764 2765 2766 2767 2663 2666 2665 2660 2460 2461 2462 2463
    Bx: 2464 2465 2466 2467 2468 2469 2776 2777 2778 2779 277A 277B 277C 277D 277E 277F
    Cx: 2780 2781 2782 2783 2784 2785 2786 2787 2788 2789 278A 278B 278C 278D 278E 278F
    Dx: 2790 2791 2792 2793 2794 2192 2194 2195 2798 2799 279A 279B 279C 279D 279E 279F
    Ex: 27A0 27A1 27A2 27A3 27A4 27A5 27A6 27A7 27A8 27A9 27AA 27AB 27AC 27AD 27AE 27AF
    Fx: - 27B1 27B2 27B3 27B4 27B5 27B6 27B7 27B8 27B9 27BA 27BB 27BC 27BD 27BE -""",
    "wingdings": """
    2x: 20 1F589 2702 2701 1F453 1F56D 1F56E 1F56F 1F57F 2706 1F582 1F583 1F4EA 1F4EB 1F4EC 1F4ED
    3x: 1F5C0 1F5C1 1F5CE 1F5CF 1F5D0 1F5C4 23F3 1F5AE 1F5B0 1F5B2 1F5B3 1F5B4 1F5AB 1F5AC 2707 270D
    4x: 1F58E 270C 1F58F 1F44D 1F44E 261C 261E 261D 1F597 1F590 263A 1F610 2639 1F4A3 1F571 1F3F3
    5x: 1F3F1 2708 263C 1F322 2744 1F546 271E 1F548 2720 2721 262A 262F 1F549 2638 2648 2649
    6x: 264A 264B 264C 264D 264E 264F 2650 2651 2652 2653 1F670 1F675 26AB 1F53E 25FC 1F78F
    7x: 1F790 2751 2752 1F79F 29EB 25C6 2756 1F799 2327 2BB9 2318 1F3F5 1F3F6 1F676 1F677 -
    8x: 1F10B 2780 2781 2782 2783 2784 2785 2786 2787 2788 2789 1F10C 278A 278B 278C 278D
    9x: 278E 278F 2790 2791 2792 2793 1F662 1F660 1F661 1F663 1F65E 1F65C 1F65D 1F65F 2219 2022
    Ax: 2B1D 2B58 1F786 1F788 1F78A 1F78B 1F53F 25AA 1F78E 1F7C1 1F7C5 2605 1F7CB 1F7CF 1F7D3 1F7D1
    Bx: 2BD0 2316 2BCE 2BCF 2BD1 272A 2730 1F550 1F551 1F552 1F553 1F554 1F555 1F556 1F557 1F558
    Cx: 1F559 1F55A 1F55B 2BB0 2BB1 2BB2 2BB3 2BB4 2BB5 2BB6 2BB7 1F66A 1F66B 1F655 1F654 1F657
    Dx: 1F656 1F650 1F651 1F652 1F653 232B 2326 2B98 2B9A 2B99 2B9B 2B88 2B8A 2B89 2B8B 1F868
    Ex: 1F86A 1F869 1F86B 1F86C 1F86D 1F86F 1F86E 1F878 1F87A 1F879 1F87B 1F87C 1F87D 1F87F 1F87E 21E6
    Fx: 21E8 21E7 21E9 2B04 21F3 2B01 2B00 2B03 2B02 1F8AC 1F8AD 1F5F6 2713 1F5F7 1F5F9 -""",
    "wingdings2": """
    2x: 20 1F58A 1F58B 1F58C 1F58D 2704 2700 1F57E 1F57D 1F5C5 1F5C6 1F5C7 1F5C8 1F5C9 1F5CA 1F5CB
    3x: 1F5CC 1F5CD 1F4CB 1F5D1 1F5D4 1F5B5 1F5B6 1F5B7 1F5B8 1F5AD 1F5AF 1F5B1 1F592 1F593 1F598 1F599
    4x: 1F59A 1F59B 1F448 1F449 1F59C 1F59D 1F59E 1F59F 1F5A0 1F5A1 1F446 1F447 1F5A2 1F5A3 1F591 1F5F4
    5x: 1F5F8 1F5F5 2611 2BBD 2612 2BBE 2BBF 1F6C7 29B8 1F671 1F674 1F672 1F673 203D 1F679 1F67A
    6x: 1F67B 1F666 1F664 1F665 1F667 1F65A 1F658 1F659 1F65B 24EA 2460 2461 2462 2463 2464 2465
    7x: 2466 2467 2468 2469 24FF 2776 2777 2778 2779 277A 277B 277C 277D 277E 277F -
    8x: 2609 1F315 263D 263E 2E3F 271D 1F547 1F55C 1F55D 1F55E 1F55F 1F560 1F561 1F562 1F563 1F564
    9x: 1F565 1F566 1F567 1F668 1F669 22C5 1F784 2981 25CF 25CB 1F785 1F787 1F789 2299 29BF 1F78C
    Ax: 1F78D 25FE 25A0 25A1 1F791 1F792 1F793 1F794 25A3 1F795 1F796 1F797 1F798 2B29 2B25 25C7
    Bx: 1F79A 25C8 1F79B 1F79C 1F79D 1F79E 2B2A 2B27 25CA 1F7A0 25D6 25D7 2BCA 2BCB 2BC0 2BC1
    Cx: 2B1F 2BC2 2B23 2B22 2BC3 2BC4 1F7A1 1F7A2 1F7A3 1F7A4 1F7A5 1F7A6 1F7A7 1F7A8 1F7A9 1F7AA
    Dx: 1F7AB 1F7AC 1F7AD 1F7AE 1F7AF 1F7B0 1F7B1 1F7B2 1F7B3 1F7B4 1F7B5 1F7B6 1F7B7 1F7B8 1F7B9 1F7BA
    Ex: 1F7BB 1F7BC 1F7BD 1F7BE 1F7BF 1F7C0 1F7C2 1F7C4 1F7C6 1F7C9 1F7CA 2736 1F7CC 1F7CE 1F7D0 1F7D2
    Fx: 2739 1F7C3 1F7C7 272F 1F7CD 1F7D4 2BCC 2BCD 203B 2042 - - - - - -""",
    "wingdings3": """
    2x: 20 2B60 2B62 2B61 2B63 2B66 2B67 2B69 2B68 2B70 2B72 2B71 2B73 2B76 2B78 2B7B
    3x: 2B7D 2B64 2B65 2B6A 2B6C 2B6B 2B6D 2B4D 2BA0 2BA1 2BA2 2BA3 2BA4 2BA5 2BA6 2BA7
    4x: 2B90 2B91 2B92 2B93 2B80 2B83 2B7E 2B7F 2B84 2B86 2B85 2B87 2B8F 2B8D 2B8E 2B8C
    5x: 2B6E 2B6F 238B 2324 2303 2325 2423 237D 21EA 2BB8 1F8A0 1F8A1 1F8A2 1F8A3 1F8A4 1F8A5
    6x: 1F8A6 1F8A7 1F8A8 1F8A9 1F8AA 1F8AB 1F850 1F852 1F851 1F853 1F854 1F855 1F857 1F856 1F858 1F859
    7x: 25B2 25BC 25B3 25BD 25C0 25B6 25C1 25B7 25E3 25E2 25E4 25E5 1F780 1F782 1F781 -
    8x: 1F783 2BC5 2BC6 2BC7 2BC8 2B9C 2B9E 2B9D 2B9F 1F810 1F812 1F811 1F813 1F814 1F816 1F815
    9x: 1F817 1F818 1F81A 1F819 1F81B 1F81C 1F81E 1F81D 1F81F 1F800 1F802 1F801 1F803 1F804 1F806 1F805
    Ax: 1F807 1F808 1F80A 1F809 1F80B 1F820 1F822 1F824 1F826 1F828 1F82A 1F82C 1F89C 1F89D 1F89E 1F89F
    Bx: 1F82E 1F830 1F832 1F834 1F836 1F838 1F83A 1F839 1F83B 1F898 1F89A 1F899 1F89B 1F83C 1F83E 1F83D
    Cx: 1F83F 1F840 1F842 1F841 1F843 1F844 1F846 1F845 1F847 2BA8 2BA9 2BAA 2BAB 2BAC 2BAD 2BAE
    Dx: 2BAF 1F860 1F862 1F861 1F863 1F864 1F865 1F867 1F866 1F870 1F872 1F871 1F873 1F874 1F875 1F877
    Ex: 1F876 1F880 1F882 1F881 1F883 1F884 1F885 1F887 1F886 1F890 1F892 1F891 1F893 1F894 1F896 1F895
    Fx: 1F897 - - - - - - - - - - - - - - -""",
}


def _parse_symbol_table(rows: str) -> dict:
    out = {}
    for row in rows.strip().splitlines():
        hi, cells = row.split(":")
        for lo, cell in enumerate(cells.split()):
            if cell != "-":
                out[int(hi.strip()[0], 16) * 16 + lo] = chr(int(cell, 16))
    return out


SYMBOL_TABLES = {name: _parse_symbol_table(rows) for name, rows in SYMBOL_FONT_TABLES.items()}
# Standard-14 names only (plus MT/ITC aliases): a font that merely mentions Symbol is not Symbol.
BASE14_SYMBOL_NAMES = {"symbol": "symbol", "symbolmt": "symbol", "symbolitc": "symbol",
                       "zapfdingbats": "zapfdingbats", "dingbats": "zapfdingbats",
                       "itczapfdingbats": "zapfdingbats", "zapfdingbatsitc": "zapfdingbats"}
OWN_ENCODING = {"symbol": "SymbolEncoding", "zapfdingbats": "ZapfDingbatsEncoding"}


def _symbol_table_for(base_font: str) -> Optional[str]:
    """The symbol table a font name reads through, or None for an ordinary font."""
    prefix, sep, rest = (base_font or "").partition("+")
    name = rest if sep and len(prefix) == 6 and prefix.isalpha() and prefix.isupper() else (base_font or "")
    lower = name.lower()
    if lower in BASE14_SYMBOL_NAMES:
        return BASE14_SYMBOL_NAMES[lower]
    m = re.fullmatch(r"wingdings[ -]?([23])?(?:[ ,-]*(?:regular|normal))?", lower)
    return "wingdings" + (m.group(1) or "") if m else None


def _clean_private_use(text: str, table: Optional[str]) -> str:
    """U+F000-F0FF (Word's symbol-font codes): through the font's table, else pdf-inspector's rule."""
    if not any("\uf000" <= c <= "\uf0ff" for c in text):
        return text
    out = []
    for c in text:
        low = ord(c) - 0xF000
        if not 0 <= low <= 0xFF:
            out.append(c)
        elif table and low in SYMBOL_TABLES[table]:
            out.append(SYMBOL_TABLES[table][low])
        elif low in (0xA1, 0xA7, 0xB7):
            out.append("\u2022")
        elif low == 0xFC:
            out.append("\u2713")
        else:
            out.append(chr(low) if low >= 0x20 else c)
    return "".join(out)


def _fix_symbol_font(font, spec) -> None:
    """Give a just-loaded pdfminer font its symbol encoding and private-use cleanup."""
    from pdfminer.encodingdb import name2unicode
    from pdfminer.pdffont import PDFSimpleFont
    from pdfminer.psparser import PSLiteral
    from pdfminer.pdftypes import resolve1
    if getattr(font, "_pdf2md_fixed", False):
        return
    font._pdf2md_fixed = True
    base = resolve1(spec.get("BaseFont"))
    table = _symbol_table_for(base.name if isinstance(base, PSLiteral) else str(base or ""))
    if table and isinstance(font, PDFSimpleFont) and font.unicode_map is None:
        enc = resolve1(spec.get("Encoding"))
        named = enc.name if isinstance(enc, PSLiteral) else None
        if named is None or named == OWN_ENCODING.get(table):      # a named encoding replaces it
            cmap = dict(SYMBOL_TABLES[table])
            if isinstance(enc, dict):                                  # /Differences still win
                code = 0
                for item in resolve1(enc.get("Differences")) or []:
                    item = resolve1(item)
                    if isinstance(item, int):
                        code = item
                    elif isinstance(item, PSLiteral):
                        try:
                            cmap[code] = name2unicode(item.name)
                        except (KeyError, ValueError):
                            pass
                        code += 1
            font.cid2unicode = cmap
    original = font.to_unichr

    def to_unichr(cid):
        return _clean_private_use(original(cid), table)
    font.to_unichr = to_unichr


def open_pdf(data: bytes, password: Optional[str] = None):
    """pdfplumber.open, with each font fixed as it loads (symbol fonts; see above)."""
    pdf = pdfplumber.open(io.BytesIO(data), password=password or "")
    get_font = pdf.rsrcmgr.get_font

    def fixed_get_font(objid, spec):
        font = get_font(objid, spec)
        _fix_symbol_font(font, spec)
        return font
    pdf.rsrcmgr.get_font = fixed_get_font
    return pdf


def page_origin(page) -> tuple:
    """pdfplumber coordinates are absolute; pages whose MediaBox/CropBox does
    not start at (0, 0) need shifting to page-relative coordinates."""
    return page.bbox[0], page.bbox[1]


def extract_words(page, pageno) -> list:
    ox, oy = page_origin(page)
    raw = page.extract_words(
        x_tolerance=1.5, y_tolerance=2, keep_blank_chars=False,
        use_text_flow=False, extra_attrs=["fontname", "size"])
    links = [(h["x0"], h["top"], h["x1"], h["bottom"], h.get("uri"))
             for h in (page.hyperlinks or []) if h.get("uri")]
    words = []
    for r in raw:
        if not r.get("upright", True):
            # degenerate drop-cap glyph (zero height, flagged rotated)?
            if len(r["text"]) == 1 and r["text"].isalpha() and r["bottom"] - r["top"] < 1 \
                    and r["x1"] - r["x0"] >= 4:
                r = dict(r, top=r["top"] - (r["x1"] - r["x0"]), bottom=r["top"], size=r["x1"] - r["x0"],
                         upright=True, dropcap=True)
            else:
                continue  # rotated margin stamps / axis titles
        txt = r["text"].replace("\u00ad", "")  # soft hyphen
        if CID_RE.fullmatch(txt):
            txt = CID_MARK                     # unmapped glyph, maybe a bullet
        else:
            txt = CID_RE.sub("", txt)
        if not txt.strip():
            continue
        font = r.get("fontname") or ""
        base = font.split("+", 1)[-1]
        w = Word(txt, r["x0"], r["x1"], r["top"], r["bottom"],  # absolute for now
                 float(r.get("size") or 0), base,
                 bool(BOLD_RE.search(base)), bool(ITALIC_RE.search(base)),
                 bool(MONO_RE.search(base)), dropcap=bool(r.get("dropcap")))
        for lk in links:
            if center_inside((w.x0, w.top, w.x1, w.bottom), lk[:4]):
                w.link = lk[4]
                break
        w.x0, w.x1, w.top, w.bottom = w.x0 - ox, w.x1 - ox, w.top - oy, w.bottom - oy
        words.append(w)
    return words


def group_lines(words, pageno) -> list:
    """Group words into visual lines (baseline-overlap clustering)."""
    caps = [w for w in words if w.dropcap]
    words = sorted((w for w in words if not w.dropcap), key=lambda w: (round(w.top, 1), w.x0))
    lines = []
    for w in words:
        h = w.bottom - w.top
        placed = False
        for ln in reversed(lines[-6:]):
            lh = ln.bottom - ln.top
            ov = min(ln.bottom, w.bottom) - max(ln.top, w.top)
            if ov >= 0.5 * min(h, lh):
                # don't glue text across a wide gap that already holds a word
                ln.words.append(w)
                ln.x0, ln.x1 = min(ln.x0, w.x0), max(ln.x1, w.x1)
                ln.top, ln.bottom = min(ln.top, w.top), max(ln.bottom, w.bottom)
                placed = True
                break
        if not placed:
            lines.append(Line(pageno, [w], w.x0, w.x1, w.top, w.bottom))
    for ln in lines:
        ln.words.sort(key=lambda w: w.x0)
        if ln.words and ln.words[0].text == CID_MARK and len(ln.words) > 1:
            ln.words[0].text = "•"
        ln.words = [w for w in ln.words if w.text != CID_MARK]
    lines = [l for l in lines if l.words]
    lines.sort(key=lambda l: (l.top, l.x0))
    for cap in caps:
        cands = [l for l in lines if 0 <= l.x0 - cap.x1 <= 30
                 and cap.top - 6 <= l.top <= cap.bottom]
        if cands:
            tgt = min(cands, key=lambda l: l.top)
            tgt.words[0].text = cap.text + tgt.words[0].text
        else:
            lines.append(Line(pageno, [cap], cap.x0, cap.x1, cap.top, cap.bottom))
    return merge_drop_caps(sorted(lines, key=lambda l: (l.top, l.x0)))


def merge_drop_caps(lines) -> list:
    """Attach a lone large initial capital to the word it begins ('T'+'HE')."""
    out = []
    for ln in lines:
        w = ln.words[0]
        if len(ln.words) == 1 and len(w.text) == 1 and w.text.isupper():
            cands = [l for l in lines if l is not ln and l.words
                     and 0 <= l.x0 - w.x1 <= 30 and w.top - 6 <= l.top <= w.bottom + 6
                     and l.words[0].text[:1].isupper()]
            if cands:
                tgt = min(cands, key=lambda l: l.top)
                first = tgt.words[0]
                first.text = w.text + first.text
                first.x0 = w.x0
                tgt.x0 = min(tgt.x0, w.x0)
                continue
        out.append(ln)
    return out


def segments(line, gap_factor=1.0, min_gap=6.0) -> list:
    """Split a line into horizontally separated cell segments."""
    segs, cur = [], [line.words[0]]
    for prev, w in zip(line.words, line.words[1:]):
        if w.x0 - prev.x1 > max(gap_factor * max(prev.size, w.size), min_gap):
            segs.append(cur)
            cur = []
        cur.append(w)
    segs.append(cur)
    return segs


# ───────────────────────────── 3. tables ──────────────────────────────────

def ruled_tables(page, origin=None) -> list:
    """Rectangle / ruling-line tables (tables/detect_rects.rs, detect_lines.rs).
    `page` may be a cropped region; `origin` is the full page's origin.
    Returns [(bbox, rows, column x-ranges)], page-relative."""
    found = []
    try:
        tables = page.find_tables(table_settings={
            "vertical_strategy": "lines", "horizontal_strategy": "lines",
            "snap_tolerance": 3, "join_tolerance": 3, "intersection_tolerance": 3,
        })
    except Exception:
        return found
    ox, oy = origin or page_origin(page)
    for t in tables:
        rows = t.extract()
        if not rows or len(rows) < 2:
            continue
        ncol = max(len(r) for r in rows)
        if ncol < 2:
            continue  # a framed box / callout, not a table
        cells = [c for r in rows for c in r]
        filled = sum(1 for c in cells if c and c.strip())
        if filled < 3 or filled / max(len(cells), 1) < 0.25:
            continue
        b = t.bbox
        try:
            cols = [(c.bbox[0] - ox, c.bbox[2] - ox) for c in t.columns]
        except Exception:
            cols = []
        found.append(((b[0] - ox, b[1] - oy, b[2] - ox, b[3] - oy), rows, cols))
    return found


def table_header_above(tbox, rows, cols, words):
    """A header row sitting just above a ruled table belongs to it: banded tables
    often shade only the body rows, so the header falls outside the detected grid.
    Returns (rows, header_words)."""
    x0, top, x1, _ = tbox
    if len(cols) < 2:
        return rows, []
    inside = [w for w in words if top - 16 <= w.bottom <= top + 1.5
              and x0 - 4 <= (w.x0 + w.x1) / 2 <= x1 + 4]
    if not inside:
        return rows, []
    base = max(w.bottom for w in inside)
    line = sorted((w for w in inside if abs(w.bottom - base) <= 2), key=lambda w: w.x0)
    # a caption or sentence that runs past the table is not a header
    if any(abs(w.bottom - base) <= 2 and not (x0 - 4 <= (w.x0 + w.x1) / 2 <= x1 + 4)
           for w in words):
        return rows, []
    text = join_words(line)
    if CAPTION_RE.match(text) or re.search(r"\([^)]*(million|thousand|bn|%|EUR|USD|GBP)[^)]*\)", text, re.I):
        return rows, []                     # a caption or units line, not a header
    segs = segments(Line(0, line, line[0].x0, line[-1].x1, line[0].top, line[0].bottom))
    if len(segs) < 2:
        return rows, []
    ncol = max(len(r) for r in rows)
    header = [""] * ncol
    for sg in segs:
        sx0, sx1 = sg[0].x0, sg[-1].x1
        hits = [k for k, (cx0, cx1) in enumerate(cols[:ncol]) if sx0 < cx1 - 2 and sx1 > cx0 + 2]
        if len(hits) != 1:
            return rows, []                 # straddles columns: not a header row
        k = hits[0]
        header[k] = (header[k] + " " + join_words(sg)).strip()
    data_cols = [k for k in range(ncol) if any(k < len(r) and r[k] and r[k].strip() for r in rows)]
    filled = sum(1 for h in header if h)
    if filled < 2 or filled < len(data_cols) - 1:
        return rows, []
    return [header] + [list(r) for r in rows], line


def column_gaps(intervals, min_gap) -> list:
    """Gaps in the union of x-intervals."""
    if not intervals:
        return []
    iv = sorted(intervals)
    gaps, cur_end = [], iv[0][1]
    for a, b in iv[1:]:
        if a - cur_end >= min_gap:
            gaps.append((cur_end, a))
        cur_end = max(cur_end, b)
    return gaps


MAX_TABLE_SCAN_ROWS = 80


def add_interval(merged, a, b, tol=6.0):
    """Insert [a, b] into a sorted list of disjoint intervals, merging any that
    are closer than tol. Returns a new list (O(k))."""
    out = []
    placed = False
    for x, y in merged:
        if y + tol <= a:          # entirely left, with a real gap
            out.append((x, y))
        elif b + tol <= x:        # entirely right
            if not placed:
                out.append((a, b))
                placed = True
            out.append((x, y))
        else:                     # overlaps / too close -> merge
            a, b = min(a, x), max(b, y)
    if not placed:
        out.append((a, b))
    out.sort()
    return out


def header_ruled(first, second, rules) -> bool:
    """A rule between two rows that spans most of them: a table's header rule."""
    lo, hi = min(first.x0, second.x0), max(first.x1, second.x1)
    span = max(hi - lo, 1.0)
    for rx0, rx1, ry in rules:
        if first.top < ry <= second.top + 1:
            if (min(rx1, hi) - max(rx0, lo)) / span >= 0.8:
                return True
    return False


def heuristic_tables(lines, page_width, rules=()) -> list:
    """Text-alignment tables (tables/detect_heuristic.rs, simplified):
    ≥3 consecutive rows whose cell segments leave consistent vertical
    gutters, with short cells (prose in two columns is rejected). A header row
    underlined by a rule makes a 2-row table (header + one data row) valid too.
    `page_width` is the width of the region being scanned."""
    out = []
    seg_cache = {}

    def segs_of(ln):
        k = id(ln)
        if k not in seg_cache:
            seg_cache[k] = segments(ln)
        return seg_cache[k]

    i, n = 0, len(lines)
    while i < n:
        if len(segs_of(lines[i])) < 2:
            i += 1
            continue
        best_j, best_gaps = None, None
        merged = []
        multi = 0
        for j in range(i, min(n, i + MAX_TABLE_SCAN_ROWS)):
            ln = lines[j]
            if j > i:
                prev = lines[j - 1]
                if ln.top - prev.bottom > 2.2 * max(prev.size, 1):
                    break
            # a header underlined by its own rule after a blank row starts a new table
            if j > i + 1 and rules and j + 1 < n:
                pitch = statistics.median(lines[k + 1].top - lines[k].top for k in range(i, j - 1))
                blank_gap = not any(lines[j - 1].bottom < ry < ln.top for _, _, ry in rules)
                wordy = sum(1 for w in ln.words if sum(ch.isalpha() for ch in w.text) >= 2) * 2 \
                    >= len(ln.words)
                if ln.top - lines[j - 1].top > 1.5 * pitch and blank_gap and wordy \
                        and lines[j + 1].top - ln.top <= 1.5 * pitch \
                        and header_ruled(ln, lines[j + 1], rules):
                    break
            segs = segs_of(ln)
            if len(segs) >= 2:
                multi += 1
            trial = merged
            for sg in segs:
                trial = add_interval(trial, sg[0].x0, sg[-1].x1)
            if len(trial) < 2:
                break                      # no vertical gutter survives
            merged = trial
            if j - i + 1 >= 3 and multi >= max(2, 0.6 * (j - i + 1)):
                best_j, best_gaps = j, merged
            elif j - i + 1 == 2 and multi == 2 and best_j is None and rules \
                    and len(segs_of(lines[i])) >= 3 and len(segs_of(lines[j])) >= 3 \
                    and header_ruled(lines[i], lines[j], rules):
                best_j, best_gaps = j, merged
        if best_j is None:
            i += 1
            continue
        rows_lines = lines[i:best_j + 1]
        min_rows = 2 if (len(rows_lines) == 2 and rules and len(segs_of(rows_lines[0])) >= 3
                         and header_ruled(rows_lines[0], rows_lines[1], rules)) else 3
        # trim trailing single-segment rows (likely prose after the table)
        while len(rows_lines) > 3 and len(segs_of(rows_lines[-1])) < 2:
            rows_lines.pop()
        seg_words = [len(s) for ln in rows_lines for s in segs_of(ln)]
        avg_words = sum(seg_words) / max(len(seg_words), 1)
        span = rows_lines[0].words and (max(l.x1 for l in rows_lines) -
                                        min(l.x0 for l in rows_lines))
        firsts = [segments(l)[0] for l in rows_lines]
        markerish = sum(1 for f in firsts if len(f) == 1 and (
            re.fullmatch(r"[•●○◦▪▫■□‣\u2043∙·◆◇►▸➢➤✓✔*\-–—]|\(?\w{1,3}[.)]?", f[0].text)
            or f[0].text == CID_MARK))
        if markerish == len(firsts) and all(len(segments(l)) == 2 for l in rows_lines):
            i = best_j + 1
            continue  # a list with hanging indents, not a table
        if avg_words > 6 or len(rows_lines) < min_rows:
            i += 1
            continue
        edges = [g[0] + (g[1] - g[0]) / 2 for g in column_gaps(
            [(s[0].x0, s[-1].x1) for ln in rows_lines for s in segments(ln)], 6.0)]
        ncol = len(edges) + 1
        if ncol < 2 or (ncol == 2 and span < page_width * 0.25):
            i += 1
            continue
        rows = []
        for ln in rows_lines:
            row = [[] for _ in range(ncol)]
            for w in ln.words:
                cx = (w.x0 + w.x1) / 2
                k = sum(1 for e in edges if cx > e)
                row[k].append(w)
            rows.append([join_words(c) for c in row])
        # merge continuation rows: first column empty and only one cell filled
        merged = []
        for r in rows:
            if merged and not r[0] and sum(1 for c in r if c) == 1:
                k = next(k for k, c in enumerate(r) if c)
                merged[-1][k] = (merged[-1][k] + " " + r[k]).strip()
            else:
                merged.append(r)
        if len(merged) >= 2:
            out.append((i, best_j - (len(lines[i:best_j + 1]) - len(rows_lines)), merged))
        i = best_j + 1
    return out


# ───────────────────────────── 4. images ──────────────────────────────────

def page_images(page, pageno, skip_fraction) -> list:
    """Embedded images worth keeping. ImageRef.bbox is absolute (for cropping);
    images covering >= skip_fraction of the page are dropped (backgrounds,
    watermarks, or the scan itself once it has been OCR'd)."""
    area = page.width * page.height
    px0, ptop, px1, pbottom = page.bbox
    refs, seen = [], set()
    for im in page.images:
        x0, top = max(im["x0"], px0), max(im["top"], ptop)
        x1, bottom = min(im["x1"], px1), min(im["bottom"], pbottom)
        w, h = x1 - x0, bottom - top
        if w < MIN_IMAGE_PT or h < MIN_IMAGE_PT:
            continue
        if w * h >= skip_fraction * area:
            continue
        key = (round(x0), round(top), round(x1), round(bottom))
        if key in seen:
            continue
        seen.add(key)
        refs.append(ImageRef(pageno, len(refs) + 1, (x0, top, x1, bottom)))
    return refs


def render_region(page, bbox, dpi=150):
    return page.crop(bbox).to_image(resolution=dpi).original


def save_image(page, ref, mode, image_dir, stem, dpi):
    try:
        img = render_region(page, ref.bbox, dpi)
    except Exception:
        return False
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    if mode == "embed":
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True)
        ref.data_uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    else:
        os.makedirs(image_dir, exist_ok=True)
        name = f"{stem}_p{ref.page}_img{ref.index}.png"
        img.save(os.path.join(image_dir, name), "PNG", optimize=True)
        ref.path = os.path.join(image_dir, name)
    return True


# ───────────────────────────── 5. OCR ─────────────────────────────────────

# ───────────────────────────── 4b. vector graphics: rules and charts ───────

CHART_DILATE = 18.0       # shapes closer than this belong to one chart
MAX_SHAPES = 4000         # beyond this a page is a drawing/map: skip chart detection


def color_rgb(c):
    """pdfplumber colour (gray / RGB / CMYK, number or tuple) -> RGB tuple, or None."""
    if c is None:
        return None
    if isinstance(c, (int, float)):
        c = (c,)
    try:
        vals = [float(v) for v in c]
    except (TypeError, ValueError):
        return None                       # pattern or named colour space
    if len(vals) == 1:
        return (vals[0],) * 3
    if len(vals) == 3:
        return tuple(vals)
    if len(vals) == 4:
        cy, m, y, k = vals
        return ((1 - cy) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
    return None


def whiteish(rgb) -> bool:
    return rgb is not None and min(rgb) >= 0.92


def horizontal_rules(page, ox, oy, min_len=20.0) -> list:
    """Horizontal edges of stroked lines, rectangles and paths, page-relative:
    [(x0, x1, y)]."""
    out = []
    for e in page.horizontal_edges:
        x0, x1 = e["x0"] - ox, e["x1"] - ox
        if x1 - x0 >= min_len:
            out.append((x0, x1, e["top"] - oy))
    return out


class _Grid:
    """Bucketed points for fast 'is any point inside this box' queries."""

    def __init__(self, pts, cell=24.0):
        self.cell, self.b = cell, defaultdict(list)
        for p in pts:
            self.b[(int(p[0] // cell), int(p[1] // cell))].append(p)

    def any_in(self, x0, top, x1, bottom) -> bool:
        c = self.cell
        for gx in range(int(x0 // c), int(x1 // c) + 1):
            for gy in range(int(top // c), int(bottom // c) + 1):
                for px, py in self.b.get((gx, gy), ()):
                    if x0 <= px <= x1 and top <= py <= bottom:
                        return True
        return False


def _shape(o, ox, oy) -> dict:
    x0, top, x1, bottom = o["x0"] - ox, o["top"] - oy, o["x1"] - ox, o["bottom"] - oy
    rectlike = o["object_type"] == "rect"
    curved = any(seg and seg[0] in ("c", "v", "y") for seg in (o.get("path") or []))
    if not rectlike and not curved:     # straight-edged path whose points all sit on its box
        pts = [(p[0] - ox, p[1] - oy) for p in (o.get("pts") or [])]
        rectlike = len(pts) >= 4 and all(
            min(abs(px - x0), abs(px - x1)) < 0.6 or min(abs(py - top), abs(py - bottom)) < 0.6
            for px, py in pts)
    fc = color_rgb(o.get("non_stroking_color"))
    return {"x0": x0, "top": top, "x1": x1, "bottom": bottom, "fill": bool(o.get("fill")),
            "stroke": bool(o.get("stroke")), "fc": fc, "rectlike": rectlike,
            "ck": tuple(round(v, 2) for v in fc) if fc else (0.0, 0.0, 0.0)}


def _bbox(items) -> tuple:
    return (min(i["x0"] for i in items), min(i["top"] for i in items),
            max(i["x1"] for i in items), max(i["bottom"] for i in items))


def _union(a, b) -> tuple:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _cv(vals) -> float:
    m = statistics.mean(vals)
    return statistics.pstdev(vals) / m if m else 0.0


def vector_figures(page, words, ox, oy) -> list:
    """Charts drawn with vector graphics, as boxes (page-relative) to save as
    pictures. Reading numbers out of chart drawings isn't reliable across PDFs
    (missed bars, axes guessed from stray numbers), so nothing is extracted and no
    text is moved: the chart just gets an image next to its own labels."""
    objs = page.rects + page.curves
    if len(objs) > MAX_SHAPES:
        return []
    W, H = page.width, page.height
    shapes = [_shape(o, ox, oy) for o in objs]
    grid = _Grid([((w.x0 + w.x1) / 2, (w.top + w.bottom) / 2) for w in words])
    marks = []
    for s in shapes:
        w_, h_ = s["x1"] - s["x0"], s["bottom"] - s["top"]
        if not s["fill"] or whiteish(s["fc"]) or w_ * h_ > 0.35 * W * H:
            continue
        if min(w_, h_) < 2.5 or w_ * h_ < 20:
            continue                       # rules, borders, dots
        if grid.any_in(s["x0"], s["top"], s["x1"], s["bottom"]):
            continue                       # text sits on it: a cell fill or highlight
        marks.append(s)
    if len(marks) < 3:
        return []
    # cluster nearby marks (bucketed; dense buckets are merged wholesale)
    parent = list(range(len(marks)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    D = CHART_DILATE
    buckets = defaultdict(list)
    for i, m in enumerate(marks):
        for gx in range(int((m["x0"] - D) // 48), int((m["x1"] + D) // 48) + 1):
            for gy in range(int((m["top"] - D) // 48), int((m["bottom"] + D) // 48) + 1):
                buckets[(gx, gy)].append(i)
    for b in buckets.values():
        if len(b) > 64:
            for j in b[1:]:
                parent[find(j)] = find(b[0])
            continue
        for p in range(len(b)):
            for q in range(p + 1, len(b)):
                m1, m2 = marks[b[p]], marks[b[q]]
                if m1["x0"] - D <= m2["x1"] and m2["x0"] - D <= m1["x1"] and \
                        m1["top"] - D <= m2["bottom"] and m2["top"] - D <= m1["bottom"]:
                    parent[find(b[p])] = find(b[q])
    # groups of bars spread along one axis line are one chart
    axes = [s for s in shapes if min(s["x1"] - s["x0"], s["bottom"] - s["top"]) < 1.5
            and max(s["x1"] - s["x0"], s["bottom"] - s["top"]) >= 30] + \
        [{"x0": l_["x0"] - ox, "x1": l_["x1"] - ox, "top": l_["top"] - oy, "bottom": l_["bottom"] - oy}
         for l_ in page.lines if max(l_["x1"] - l_["x0"], l_["bottom"] - l_["top"]) >= 30]
    groups = defaultdict(list)
    for i in range(len(marks)):
        groups[find(i)].append(i)
    gbox = {k: _bbox([marks[i] for i in v]) for k, v in groups.items()}
    for a_ in axes:
        hit = [k for k, bx in gbox.items() if a_["x0"] <= bx[2] + 6 and a_["x1"] >= bx[0] - 6
               and a_["top"] <= bx[3] + 6 and a_["bottom"] >= bx[1] - 6]
        for k in hit[1:]:
            parent[find(k)] = find(hit[0])
    clusters = defaultdict(list)
    for i in range(len(marks)):
        clusters[find(i)].append(marks[i])
    figs = []
    for ms in clusters.values():
        if len(ms) < 3 or len({m["ck"] for m in ms}) < 2:
            continue
        box = _bbox(ms)
        if (box[2] - box[0]) * (box[3] - box[1]) < 1200:
            continue
        if _cv([m["x1"] - m["x0"] for m in ms]) < 0.05 and _cv([m["bottom"] - m["top"] for m in ms]) < 0.05:
            continue                       # uniform grid of coloured cells: a table, not a chart
        # include the chart's own labels: short text lines right next to the marks
        near = [w for w in words if box[0] - 90 <= w.x0 and w.x1 <= box[2] + 90
                and box[1] - 20 <= w.top and w.bottom <= box[3] + 30]
        for ln in group_lines(near, 0):
            if len(ln.words) <= 6:
                box = _union(box, (ln.x0, ln.top, ln.x1, ln.bottom))
        figs.append({"bbox": box})
    return figs


_OCR_WARNED = False


def ocr_blocks(page, lang="eng", dpi=300) -> Optional[list]:
    """OCR a page with Tesseract. Returns paragraphs as
    [{"text", "bbox": (x0, top, x1, bottom) in PDF points, "conf": 0-1}]."""
    if not ocr_available():
        return None
    try:
        img = page.to_image(resolution=dpi).original
    except Exception:
        return None
    k = 72.0 / dpi
    rot = detect_rotation(img)
    if not rot:
        return ocr_image(img, lang, k)
    # scanned sideways / upside down: OCR an upright copy, map positions back
    W0, H0 = img.width * k, img.height * k

    def unrotate(x, y):
        if rot == 90:
            return y, H0 - x
        if rot == 180:
            return W0 - x, H0 - y
        return W0 - y, x                       # 270
    return ocr_image(img.rotate(-rot, expand=True), lang, k, unrotate)


def detect_rotation(img) -> int:
    """Clockwise turn (0/90/180/270) that makes a page image upright, from
    Tesseract's orientation detection. 0 when unsure or unavailable."""
    try:
        import pytesseract
        small = img.copy()
        small.thumbnail((1400, 1400))           # orientation needs far fewer pixels than OCR
        osd = pytesseract.image_to_osd(small, output_type=pytesseract.Output.DICT)
        rot = int(osd.get("rotate", 0))
        if rot in (90, 180, 270) and float(osd.get("orientation_conf", 0)) >= 2.0:
            return rot
    except Exception:
        pass                                    # too little text to tell, or no osd data
    return 0


def ocr_available() -> bool:
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def flatten_alpha(img):
    """Transparent pixels -> white, so dark text on transparency survives OCR."""
    from PIL import Image
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, "white")
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg
    return img.convert("RGB")


def ocr_image(img, lang="eng", k=1.0, unrotate=None) -> Optional[list]:
    """OCR a PIL image; bboxes are scaled by k (pixels -> points for pages) and,
    for a page OCR'd upright from a rotated scan, mapped back by `unrotate`."""
    global _OCR_WARNED
    try:
        import pytesseract
        d = pytesseract.image_to_data(img, lang=lang, output_type=pytesseract.Output.DICT)
    except Exception as e:
        if not _OCR_WARNED:
            print(f"warning: OCR unavailable ({type(e).__name__}); install tesseract + "
                  f"pytesseract for scanned pages", file=sys.stderr)
            _OCR_WARNED = True
        return None
    paras = defaultdict(lambda: defaultdict(list))   # (block, par) -> line -> words
    for i, txt in enumerate(d["text"]):
        if not txt.strip() or float(d["conf"][i]) < 0:
            continue
        paras[(d["block_num"][i], d["par_num"][i])][d["line_num"][i]].append(i)
    out = []
    for key in sorted(paras):
        lines = []
        for ln in sorted(paras[key]):
            ids = paras[key][ln]
            lines.append({
                "text": " ".join(d["text"][i] for i in ids),
                "box": (min(d["left"][i] for i in ids) * k, min(d["top"][i] for i in ids) * k,
                        max(d["left"][i] + d["width"][i] for i in ids) * k,
                        max(d["top"][i] + d["height"][i] for i in ids) * k),
                "conf": [float(d["conf"][i]) for i in ids]})
        longest = max(len(l["text"]) for l in lines)
        group = [lines[0]]
        groups = []
        for prev, l in zip(lines, lines[1:]):
            if len(prev["text"]) >= 0.6 * longest and longest > 40:   # wrapped line
                group.append(l)
            else:                                                   # separate short line
                groups.append(group)
                group = [l]
        groups.append(group)
        for g in groups:
            text = ""
            for l in g:
                t = l["text"]
                text = (text[:-1] + t) if text.endswith("-") else (text + " " + t).strip()
            confs = [c for l in g for c in l["conf"]]
            out.append({"text": text, "line_h": statistics.median(l["box"][3] - l["box"][1] for l in g),
                        "bbox": (min(l["box"][0] for l in g), min(l["box"][1] for l in g),
                                 max(l["box"][2] for l in g), max(l["box"][3] for l in g)),
                        "conf": round(sum(confs) / len(confs) / 100, 3) if confs else None})
    if unrotate:
        for o in out:
            x0, y0, x1, y1 = o["bbox"]
            (ax, ay), (bx, by) = unrotate(x0, y0), unrotate(x1, y1)
            o["bbox"] = (min(ax, bx), min(ay, by), max(ax, bx), max(ay, by))
    return out


# ───────────────────────────── 6. layout / reading order ──────────────────

def segment_regions(words, rules, obstacles, box, depth=0) -> list:
    """Split a page area into reading-order regions where independent blocks sit
    side by side, e.g. two tables exported from one spreadsheet that share a row
    grid. Whitespace alone can't tell those apart from the columns of one table,
    so a split needs rule lines that stop at the gutter on both sides (a single
    table's rules run straight across). Returns region boxes in reading order:
    above, left, right, below (each split further if needed)."""
    bx0, by0, bx1, by1 = box
    ws = [w for w in words if bx0 <= (w.x0 + w.x1) / 2 <= bx1 and by0 <= (w.top + w.bottom) / 2 <= by1]
    # rules clipped to the box: a full-width separator still crosses the gutter
    rs = [(max(r[0], bx0), min(r[1], bx1), r[2]) for r in rules
          if r[1] > bx0 and r[0] < bx1 and by0 <= r[2] <= by1 and min(r[1], bx1) - max(r[0], bx0) >= 20]
    if depth > 3 or len(ws) < 6 or len(rs) < 2 or len(rs) > 300:
        return [box]                       # >300 rules: a drawing, not a page of tables
    width = bx1 - bx0
    lo, hi = bx0 + 0.15 * width, bx0 + 0.85 * width
    cands = set()
    for x0, x1, _ in rs:
        cands.update(x for x in (x1 + 3, x0 - 3) if lo <= x <= hi)
    by_y = defaultdict(list)
    for r in rs:
        by_y[round(r[2])].append(r)
    for row in by_y.values():
        row.sort()
        for a, b in zip(row, row[1:]):
            if b[0] - a[1] >= 4 and lo <= (a[1] + b[0]) / 2 <= hi:
                cands.add((a[1] + b[0]) / 2)

    def nlines(group):
        return len({round(w.top / 2) for w in group})

    def alpha_lines(group):
        return len({round(w.top / 2) for w in group if sum(ch.isalpha() for ch in w.text) >= 3})

    rows = group_lines(ws, 0)
    best = None
    for x in sorted(cands):
        crossing = [(w.top, w.bottom) for w in ws if w.x0 - 1 < x < w.x1 + 1]
        for ln in rows:                    # a sentence running across x (normal word space)
            lw = [w for w in ln.words if w.x1 <= x]
            rw = [w for w in ln.words if w.x0 >= x]
            if lw and rw:
                a, b = max(lw, key=lambda w: w.x1), min(rw, key=lambda w: w.x0)
                if b.x0 - a.x1 <= 0.8 * max(a.size, b.size):
                    crossing.append((ln.top, ln.bottom))
        crossing += [(r[2] - 0.5, r[2] + 0.5) for r in rs if r[0] - 2 <= x <= r[1] + 2]
        crossing += [(o[1], o[3]) for o in obstacles if o[0] < x < o[2] and o[1] < by1 and o[3] > by0]
        runs, cur = [], by0
        for t, b in sorted(crossing):
            if t > cur:
                runs.append((cur, t))
            cur = max(cur, b)
        if cur < by1:
            runs.append((cur, by1))
        for ry0, ry1 in runs:
            if ry1 - ry0 < 30:
                continue
            L = [w for w in ws if ry0 <= (w.top + w.bottom) / 2 <= ry1 and (w.x0 + w.x1) / 2 < x]
            R = [w for w in ws if ry0 <= (w.top + w.bottom) / 2 <= ry1 and (w.x0 + w.x1) / 2 > x]
            if nlines(L) < 3 or nlines(R) < 3:
                continue
            # both sides must be text blocks: a statement's number columns are not
            if alpha_lines(L) < max(2, 0.5 * nlines(L)) or alpha_lines(R) < max(2, 0.5 * nlines(R)):
                continue
            lr = [r for r in rs if ry0 < r[2] < ry1 and r[1] <= x - 1]
            rr = [r for r in rs if ry0 < r[2] < ry1 and r[0] >= x + 1]
            if not lr or not rr:
                continue
            lx0, lx1 = min(w.x0 for w in L), max(w.x1 for w in L)
            rx0, rx1 = min(w.x0 for w in R), max(w.x1 for w in R)
            cov_l = max((min(r[1], lx1) - max(r[0], lx0)) / max(lx1 - lx0, 1) for r in lr)
            cov_r = max((min(r[1], rx1) - max(r[0], rx0)) / max(rx1 - rx0, 1) for r in rr)
            if cov_l < 0.5 or cov_r < 0.5:
                continue
            # one table with a rule under each column has its rules at the same
            # heights on both sides; independent blocks mostly don't
            ly = sorted({round(r[2], 1) for r in lr})
            ryy = sorted({round(r[2], 1) for r in rr})
            shared = sum(1 for y in ly if any(abs(y - y2) <= 1.5 for y2 in ryy))
            if shared / min(len(ly), len(ryy)) >= 0.75 and not (
                    rule_spans_columns(max(lr, key=lambda r: r[1] - r[0]), L)
                    and rule_spans_columns(max(rr, key=lambda r: r[1] - r[0]), R)):
                continue          # same heights and one column per rule: one table
            score = (ry1 - ry0, -abs(x - (bx0 + bx1) / 2))
            if best is None or score > best[0]:
                xl = max([w.x1 for w in L] + [r[1] for r in lr])
                xr = min([w.x0 for w in R] + [r[0] for r in rr])
                # the side-by-side part ends where the shorter side ends; what
                # follows (e.g. a new section) is read after both sides
                end = min(max([w.bottom for w in L] + [r[2] for r in lr]),
                          max([w.bottom for w in R] + [r[2] for r in rr])) + 1
                longer = L if max(w.bottom for w in L) > max(w.bottom for w in R) else R
                tops = sorted({round(w.top, 1) for w in longer})
                pitch = statistics.median([b - a for a, b in zip(tops, tops[1:])] or [12.0])
                for t in tops:             # rows that continue at the side's own spacing
                    if t <= end:
                        continue
                    if t - end > 1.25 * pitch:
                        break
                    end = max(w.bottom for w in longer if round(w.top, 1) == t) + 1
                for o in obstacles:            # never cut through a chart or picture
                    if o[1] < end < o[3] and o[2] > bx0 and o[0] < bx1:
                        end = o[3] + 1
                best = (score, (xl + xr) / 2 if xr > xl else x, ry0, max(min(ry1, end), ry0 + 1))
    if best is None:
        return [box]
    _, sx, ry0, ry1 = best
    out = []
    for sub in ((bx0, by0, bx1, ry0), (bx0, ry0, sx, ry1), (sx, ry0, bx1, ry1), (bx0, ry1, bx1, by1)):
        if sub[2] - sub[0] > 1 and sub[3] - sub[1] > 1:
            out.extend(segment_regions(words, rules, obstacles, sub, depth + 1))
    return out or [box]


def rule_spans_columns(rule, words) -> bool:
    """Is this the header rule of a multi-column table (the header line just above
    it has 2+ separate cells), rather than a rule under a single column?"""
    x0, x1, y = rule
    above = [w for w in words if y - 15 <= w.bottom <= y + 1 and x0 - 2 <= (w.x0 + w.x1) / 2 <= x1 + 2]
    if not above:
        return False
    base = max(w.bottom for w in above)
    row = sorted((w for w in above if abs(w.bottom - base) <= 2), key=lambda w: w.x0)
    cells = 1 + sum(1 for a, b in zip(row, row[1:]) if b.x0 - a.x1 > max(a.size, b.size, 6))
    return cells >= 2


def region_of(box, regions) -> int:
    """Index of the region holding the centre of `box` (nearest one otherwise)."""
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    best, best_d = 0, None
    for k, (x0, y0, x1, y1) in enumerate(regions):
        dx = max(x0 - cx, 0, cx - x1)
        dy = max(y0 - cy, 0, cy - y1)
        d = dx * dx + dy * dy
        if best_d is None or d < best_d:
            best, best_d = k, d
            if d == 0:
                break
    return best


def find_gutter(lines, x0, x1=None) -> Optional[float]:
    """Detect a two-column gutter: an x in the middle band that many lines
    have text on both sides of, and almost none cross. Scans the area x0..x1
    (called as find_gutter(lines, page_width) it scans the whole page)."""
    if x1 is None:
        x0, x1 = 0.0, x0
    text_lines = [l for l in lines if l.kind == "text"]
    if len(text_lines) < 8:
        return None
    best, best_score = None, 0
    x = x0 + (x1 - x0) * 0.3
    while x <= x0 + (x1 - x0) * 0.7:
        both = cross = 0
        for ln in text_lines:
            if any(w.x0 < x < w.x1 for w in ln.words):
                cross += 1
                continue
            if any(w.x1 <= x for w in ln.words) and any(w.x0 >= x for w in ln.words):
                left = max(w.x1 for w in ln.words if w.x1 <= x)
                right = min(w.x0 for w in ln.words if w.x0 >= x)
                if right - left >= 8:
                    both += 1
        score = both - 3 * cross
        if both >= max(5, 0.35 * len(text_lines)) and cross <= 0.15 * len(text_lines) \
                and score > best_score:
            best, best_score = x, score
        x += 2
    return best


def split_at(line, x) -> list:
    left = [w for w in line.words if (w.x0 + w.x1) / 2 < x]
    right = [w for w in line.words if (w.x0 + w.x1) / 2 >= x]
    parts = []
    for ws in (left, right):
        if ws:
            parts.append(Line(line.page, ws, min(w.x0 for w in ws), max(w.x1 for w in ws),
                              min(w.top for w in ws), max(w.bottom for w in ws)))
    return parts


def reading_order(items, x0, x1=None) -> list:
    """Order lines + block items (tables/images). Two-column regions are read
    left column then right column; full-width items act as separators.
    Scans x0..x1 for the gutter (reading_order(items, page_width) = whole page)."""
    gutter = find_gutter(items, x0, x1)
    items = sorted(items, key=lambda l: (l.top, l.x0))
    if gutter is None:
        set_colw([l for l in items if l.kind == "text"])
        return items, False
    out, left, right = [], [], []

    def flush():
        out.extend(sorted(left, key=lambda l: l.top))
        out.extend(sorted(right, key=lambda l: l.top))
        left.clear()
        right.clear()

    for it in items:
        if it.kind != "text":
            if it.x0 < gutter - 4 and it.x1 > gutter + 4:
                flush()
                out.append(it)
            elif (it.x0 + it.x1) / 2 < gutter:
                left.append(it)
            else:
                right.append(it)
            continue
        if any(w.x0 < gutter < w.x1 for w in it.words):
            flush()
            out.append(it)
            continue
        for part in split_at(it, gutter):
            (left if part.x1 <= gutter + 1 else right).append(part)
    flush()
    tl = [l for l in out if l.kind == "text"]
    set_colw([l for l in tl if l.x1 <= gutter + 1])
    set_colw([l for l in tl if l.x0 >= gutter - 1])
    set_colw([l for l in tl if l.x0 < gutter - 1 < gutter + 1 < l.x1])
    return out, True


def set_colw(lines):
    ws = [l.x1 - l.x0 for l in lines if len(l.words) > 3]
    if not ws:
        return
    w = sorted(ws)[int(len(ws) * 0.8)]   # 80th percentile ≈ full line width
    for l in lines:
        l.colw = w


# ───────────────────────────── 7. furniture ───────────────────────────────

def normalize_for_comparison(s: str) -> str:
    s = " ".join(s.split())
    s = re.sub(r"^\d+\s*", "", s)
    s = re.sub(r"\s*\d+$", "", s)
    return s.strip()


def is_structural(text: str) -> bool:
    t = text.lstrip()
    numbered = t[:1].isdigit() and (". " in t or ") " in t)
    return (t.startswith(("#", "- ", "* ", "• ")) or numbered or
            bool(re.match(r"^\((\d{1,3}|[a-z]{1,3})\)", t)))


PAGE_NUM_RE = re.compile(
    r"^\s*(page\s+)?[-–—(\[]?\s*\d{1,4}\s*[-–—)\]]?\s*((of|/)\s*\d{1,4})?\s*$", re.I)


def line_text(l) -> str:
    return l.payload["text"] if l.kind == "ocr" else l.text


EXPLICIT_PAGE_NUM_RE = re.compile(r"page\s+\d|\d\s*(of|/)\s*\d", re.I)


def strip_furniture(pages_lines: dict, page_heights: dict) -> dict:
    """Port of furniture.rs strip_repeated_lines + page-number stripping.
    Works on native text lines and OCR paragraphs alike."""
    page_count = len(pages_lines)
    FURN = ("text", "ocr")

    def edge_set(lines):
        tops = sorted({round(l.top, 1) for l in lines if l.kind in FURN})
        if len(tops) <= EDGE_LINE_COUNT * 2:
            return set(tops)
        return set(tops[:EDGE_LINE_COUNT] + tops[-EDGE_LINE_COUNT:])

    edges = {p: edge_set(ls) for p, ls in pages_lines.items()}
    freq = defaultdict(set)
    ypos = defaultdict(list)
    for p, ls in pages_lines.items():
        for l in ls:
            if l.kind not in FURN or round(l.top, 1) not in edges[p]:
                continue
            t = line_text(l)
            if is_structural(t):
                continue
            norm = normalize_for_comparison(t)
            if len(norm) < 4 or len(set(norm)) == 1:
                continue
            freq[norm].add(p)
            ypos[norm].append(l.top)

    threshold = max(FURNITURE_MIN_PAGES, int(page_count * FURNITURE_PAGE_FRACTION))
    avg_span = statistics.mean(page_heights.values()) if page_heights else 1
    candidates = set()
    if page_count >= FURNITURE_MIN_PAGES:
        for norm, ps in freq.items():
            if len(ps) < threshold:
                continue
            ys = ypos[norm]
            if len(ys) >= 2 and statistics.pstdev(ys) / avg_span >= FURNITURE_Y_STDDEV:
                continue
            candidates.add(norm)

    # Bare numbers in the margin count as page numbers only with evidence:
    # an explicit form ("Page 3", "3 of 9", "3/9"), or bare numbers in the same
    # margin band on several pages that increase with the page number.
    bare = {}   # band -> [(page, value)]
    for p, ls in pages_lines.items():
        h = page_heights[p]
        for l in ls:
            if l.kind in FURN and round(l.top, 1) in edges[p]:
                t = line_text(l).strip()
                if (l.top < h * 0.12 or l.bottom > h * 0.88) and re.fullmatch(r"\d{1,4}", t):
                    bare.setdefault("top" if l.top < h / 2 else "bottom", []).append((p, int(t)))
    numbered_bands = set()
    for band, vals in bare.items():
        vals.sort()
        rising = all(b[1] > a[1] for a, b in zip(vals, vals[1:]))
        if len({p for p, _ in vals}) >= max(2, int(0.5 * page_count)) and rising:
            numbered_bands.add(band)

    out, removed = {}, {}
    for p, ls in pages_lines.items():
        kept, gone = [], []
        h = page_heights[p]
        for l in ls:
            if l.kind in FURN and round(l.top, 1) in edges[p]:
                t = line_text(l)
                in_margin = l.top < h * 0.12 or l.bottom > h * 0.88
                band = "top" if l.top < h / 2 else "bottom"
                role = None
                if in_margin and PAGE_NUM_RE.match(t) and (
                        EXPLICIT_PAGE_NUM_RE.search(t) or band in numbered_bands):
                    role = "page_number"
                elif normalize_for_comparison(t) in candidates:
                    role = "page_header" if l.top < h / 2 else "page_footer"
                if role:
                    gone.append((role, l))
                    continue
            kept.append(l)
        out[p], removed[p] = kept, gone
    return out, removed


# ───────────────────────────── 8. font analysis ───────────────────────────

def body_font_size(lines) -> float:
    c = Counter()
    for l in lines:
        if l.kind == "text":
            for w in l.words:
                c[round(w.size * 2) / 2] += len(w.text)
    return c.most_common(1)[0][0] if c else 10.0


def correct_base_size(lines, base) -> float:
    """analysis.rs correct_base_size: if ≥1/3 of text lines clear the heading
    gate and they read like prose, the promoted size is really the body."""
    text_lines = 0
    promoted, wordy = Counter(), Counter()
    for l in lines:
        if l.kind != "text":
            continue
        t = l.text.strip()
        if sum(ch.isalpha() for ch in t) < 3:
            continue
        s = l.size
        text_lines += 1
        if base and s / base >= HEADING_RATIO:
            promoted[s] += 1
            words = t.split()
            prose = sum(1 for w in words if next((c for c in w if c.isalpha()), "A").islower())
            if len(words) >= 6 or (len(t) >= 30 and prose * 2 >= len(words)):
                wordy[s] += 1
    total = sum(promoted.values())
    if text_lines < 8 or total * 3 < text_lines:
        return base
    size, at = max(promoted.items(), key=lambda kv: (kv[1], -kv[0]))
    if wordy[size] * 2 < at:
        return base
    return size


def heading_tiers(lines, base) -> list:
    """analysis.rs compute_heading_tiers."""
    sizes = []
    for l in lines:
        if l.kind != "text" or not l.words:
            continue
        t = l.text.strip()
        if not any(c.isalpha() for c in t):
            continue
        s = l.size
        if s / base >= HEADING_RATIO and len(t) <= 200:
            sizes.append(s)
    sizes.sort(reverse=True)
    tiers = []
    for s in sizes:
        if not any(abs(t - s) < TIER_TOLERANCE for t in tiers):
            tiers.append(s)
    if not tiers:
        bold = sorted({l.size for l in lines if l.kind == "text" and l.words and l.bold
                       and l.size / base >= BOLD_TIER_RATIO
                       and any(c.isalpha() for c in l.text)}, reverse=True)
        for s in bold:
            if not any(abs(t - s) < TIER_TOLERANCE for t in tiers):
                tiers.append(s)
    return tiers[:MAX_TIERS]


def header_level(size, base, tiers, bold) -> Optional[int]:
    """analysis.rs detect_header_level."""
    ratio = size / base if base else 1
    if BOLD_TIER_RATIO <= ratio < HEADING_RATIO and bold and tiers:
        for i, t in enumerate(tiers):
            if abs(size - t) < TIER_TOLERANCE:
                return i + 1
    if ratio < HEADING_RATIO:
        return None
    if tiers:
        for i, t in enumerate(tiers):
            if abs(size - t) < TIER_TOLERANCE:
                return i + 1
        if ratio >= 1.5:
            return min(len(tiers) + 1, 4)
        return None
    if ratio >= 2.0:
        return 1
    if ratio >= 1.5:
        return 2
    if ratio >= 1.25:
        return 3
    return 4


# ───────────────────────────── 9. markdown conversion ─────────────────────

def is_list_item(text: str) -> bool:
    return bool(BULLET_RE.match(text) or NUMBERED_RE.match(text))


def hanging_number(line) -> bool:
    """'1   Item text' — a bare number set off by a wide hanging-indent gap."""
    w = line.words
    return (len(w) > 1 and re.fullmatch(r"\d{1,3}", w[0].text) is not None
            and w[1].x0 - w[0].x1 > w[0].size * 0.8)


def list_marker(text: str):
    """Return (markdown_marker, rest)."""
    m = BULLET_RE.match(text)
    if m:
        tick = m.group(1) if m.group(1) in "✓✔" else ""     # a tick is content: "- ✓ Done"
        return "-", (tick + " " if tick else "") + text[m.end():]
    m = NUMBERED_RE.match(text)
    if m:
        tok = m.group(2)
        if tok.isdigit():
            return f"{tok}.", text[m.end():]
        return "-", f"{m.group(1)} " + text[m.end():]
    return None, text


def styled_text(words, allow_style=True) -> str:
    """Inline formatting: bold/italic runs and hyperlinks."""
    if not words:
        return ""
    runs = []  # (bold, italic, link, [words])
    for w in words:
        key = (w.bold and allow_style, w.italic and allow_style, w.link)
        if runs and runs[-1][0] == key:
            runs[-1][1].append(w)
        else:
            runs.append((key, [w]))
    # if the whole line is bold/italic, the caller decides (heading etc.)
    out = []
    prev_last = None
    for (b, it, link), ws in runs:
        txt = join_words(ws)
        lead = ""
        if prev_last is not None and ws[0].x0 - prev_last.x1 > max(prev_last.size, ws[0].size) * 0.12:
            lead = " "
        core = txt
        if any(ch.isalnum() for ch in core):
            if b and it:
                core = f"***{core}***"
            elif b:
                core = f"**{core}**"
            elif it:
                core = f"*{core}*"
        if link and not link.startswith("javascript"):
            core = f"[{core}]({link})"
        out.append(lead + core)
        prev_last = ws[-1]
    return "".join(out)


def join_paragraph(parts) -> str:
    text = ""
    for p in parts:
        if not text:
            text = p
        elif re.search(r"[A-Za-z]-$", text) and re.match(r"^[a-z]", p):
            text = text[:-1] + p          # dehyphenate "exam-" + "ple"
        else:
            text += " " + p
    return text


CAPTION_RE = re.compile(r"^\**\s*(figure|fig\.|table|chart|exhibit|source|note)s?\b", re.I)


def box_of(ln):
    return (ln.x0, ln.top, ln.x1, ln.bottom)


def render_blocks(ordered, base, tiers, col_width, image_md) -> list:
    """Turn one page's ordered lines into typed blocks:
    {"type", "md", "boxes", "source", "conf", "list", "level"}."""
    blocks = []
    para, para_lines = [], []
    cur_list = None    # (marker, [parts], indent_x, [lines])
    code = []

    def add(btype, md, lines_or_boxes, source="native_text", conf=None, is_list=False, level=None):
        boxes = [b if isinstance(b, tuple) else box_of(b) for b in lines_or_boxes]
        blocks.append({"type": btype, "md": md, "boxes": boxes, "source": source,
                       "conf": conf, "list": is_list, "level": level})

    def flush_para():
        nonlocal para, para_lines
        if para:
            if len(para_lines) == 1:
                ln = para_lines[0]
                t = ln.text.strip()
                # bold standalone short line -> heading (analysis.rs bold_heading_level)
                if ln.all_bold and len(t) <= 80 and len(t.split()) <= 12 \
                        and not t.endswith((".", ",", ";")) and any(c.isalpha() for c in t) \
                        and not is_list_item(t) and not CAPTION_RE.match(t):
                    lvl = min(len(tiers) + 1, 4)
                    add("section_header", "#" * lvl + " " + t, para_lines, level=lvl)
                    para, para_lines = [], []
                    return
            add("text", join_paragraph(para), para_lines)
        para, para_lines = [], []

    def flush_list():
        nonlocal cur_list
        if cur_list:
            marker, parts, _, lns = cur_list
            add("text", f"{marker} {join_paragraph(parts)}", lns, is_list=True)
            cur_list = None

    def flush_code():
        nonlocal code
        if code:
            minx = min(l.x0 for l in code)
            cw = statistics.median(
                [(w.x1 - w.x0) / max(len(w.text), 1) for l in code for w in l.words]) or 6
            body = "\n".join(" " * int(round((l.x0 - minx) / cw)) + l.text for l in code)
            add("text", "```\n" + body + "\n```", code)
            code = []

    def flush_all():
        flush_para()
        flush_list()
        flush_code()

    prev = None
    for ln in ordered:
        if ln.kind in ("table", "image", "ocr"):
            flush_all()
            if ln.kind == "table":
                md = table_to_md(ln.payload)
                if md:
                    add("table", md, [ln])
            elif ln.kind == "image":
                md = image_md(ln.payload)
                if md:
                    add("figure", md, [ln])
            elif ln.payload.get("level"):
                lv = ln.payload["level"]
                add("section_header", "#" * lv + " " + ln.payload["text"], [ln], source="ocr",
                    conf=ln.payload["conf"], level=lv)
            else:
                add("text", ln.payload["text"], [ln], source="ocr", conf=ln.payload["conf"])
            prev = None
            continue

        text = ln.text.strip()
        if not text:
            continue
        size = ln.size

        # code blocks: monospace lines
        if ln.mono and not is_list_item(text):
            flush_para()
            flush_list()
            code.append(ln)
            prev = ln
            continue
        flush_code()

        gap = (ln.top - prev.bottom) if prev and prev.page == ln.page else None
        lvl = header_level(size, base, tiers, ln.bold)

        # headings (merge wrapped multi-line headings of same level)
        if lvl and len(text) <= 200:
            flush_para()
            flush_list()
            plain = join_words(ln.words).strip()
            last = blocks[-1] if blocks else None
            if last and last["level"] == lvl and last["type"] == "section_header" \
                    and prev is not None and gap is not None and gap < size * 0.8 \
                    and ln.top > prev.top \
                    and abs(prev.size - size) < 0.5 \
                    and header_level(prev.size, base, tiers, prev.bold) == lvl:
                last["md"] += " " + plain
                last["boxes"].append(box_of(ln))
            else:
                add("section_header", "#" * lvl + " " + plain, [ln], level=lvl)
            prev = ln
            continue

        styled = styled_text(ln.words)

        if hanging_number(ln) and not is_list_item(text):
            text = ln.words[0].text + ". " + join_words(ln.words[1:])
            ln = Line(ln.page, [Word(ln.words[0].text + ".", *[getattr(ln.words[0], f) for f in
                       ("x0", "x1", "top", "bottom", "size", "font", "bold", "italic", "mono")])]
                      + ln.words[1:], ln.x0, ln.x1, ln.top, ln.bottom, colw=ln.colw)

        if is_list_item(text):
            flush_para()
            flush_list()
            marker, rest_plain = list_marker(text)
            first = ln.words[0].text.strip()
            if len(ln.words) > 1 and (re.fullmatch(r"[•●○◦▪▫■□‣\u2043∙·◆◇►▸➢➤✓✔*\-–—]", first)
                                      or re.fullmatch(r"\(?\d{1,3}[.)]", first)):
                rest = styled_text(ln.words[1:])   # keep inline styling
                if first in "✓✔":
                    rest = f"{first} {rest}"
            else:
                rest = rest_plain                  # lettered markers stay in text
            cur_list = (marker, [rest.strip()], ln.x0, [ln])
            prev = ln
            continue

        if cur_list is not None:
            # continuation of list item: indented under it, small gap
            if gap is not None and gap < size * 0.9 and ln.x0 >= cur_list[2] - 1:
                cur_list[1].append(styled)
                cur_list[3].append(ln)
                prev = ln
                continue
            flush_list()

        if para:
            p = para_lines[-1]
            new_para = (
                gap is None or gap > max(p.size, size) * 0.75 or
                abs(p.size - size) >= 1.0 or
                # previous line ended short with terminal punctuation
                (p.x1 - p.x0 < (p.colw or col_width) * 0.8 and re.search(r"[.!?:]\W*$", p.text)) or
                # first-line indent
                (ln.x0 - p.x0 > size * 1.5 and re.search(r"[.!?:]\W*$", p.text))
            )
            if new_para:
                flush_para()
        para.append(styled)
        para_lines.append(ln)
        prev = ln

    flush_all()

    # captions: short text right before/after a figure or table
    for i, b in enumerate(blocks):
        if b["type"] == "text" and not b["list"] and len(b["md"]) <= 300 \
                and CAPTION_RE.match(b["md"]):
            near = [blocks[j]["type"] for j in (i - 1, i + 1) if 0 <= j < len(blocks)]
            if "figure" in near or "table" in near:
                b["type"] = "caption"
    return blocks


def postprocess_fragment(md: str) -> str:
    """markdown/postprocess.rs equivalent, applied per block so that
    markdownSpan offsets stay exact."""
    if md.startswith("```") or md.startswith("|"):
        return md.strip("\n")
    md = re.sub(r"(?<!\*)\*\*\s*\*\*(?!\*)", "", md)   # empty bold (not inside ***)
    for _ in range(3):   # re-join runs split by a line wrap: widest markers first
        md = re.sub(r"(?<!\*)\*\*\*([^*\n]+?)\*\*\* \*\*\*([^*\n]+?)\*\*\*(?!\*)",
                    r"***\1 \2***", md)
        md = re.sub(r"(?<!\*)\*\*([^*\n]+?)\*\* \*\*([^*\n]+?)\*\*(?!\*)", r"**\1 \2**", md)
        md = re.sub(r"(?<!\*)\*([^*\n]+?)\* \*([^*\n]+?)\*(?!\*)", r"*\1 \2*", md)
    md = re.sub(r"[ \t]+\n", "\n", md)
    return md.strip()


def overlap_fraction(a, b) -> float:
    """Share of box a's area that lies inside box b."""
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    area = max((a[2] - a[0]) * (a[3] - a[1]), 1e-6)
    return max(w, 0) * max(h, 0) / area


def union_box(boxes):
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def norm_bbox(box, w, h):
    if not w or not h:
        return None
    return [round(max(0.0, min(1.0, v)), 4) for v in
            (box[0] / w, box[1] / h, box[2] / w, box[3] / h)]


# ───────────────────────────── 10. driver ─────────────────────────────────

class PDFOCRRequiredError(ValueError):
    """Same behaviour as Firecrawl's SCRAPE_PDF_OCR_REQUIRED: fast mode on a
    scanned / image-based PDF is an error, not an empty document."""
    code = "SCRAPE_PDF_OCR_REQUIRED"

    def __init__(self, pdf_type):
        kind = "scanned" if pdf_type == "Scanned" else "image-based"
        super().__init__(
            f'This PDF is {kind} and requires OCR for text extraction, but the requested '
            f'PDF mode is "fast" which only supports text-based PDFs. Use mode "auto" '
            f'(falls back to OCR automatically) or mode "ocr" (forces OCR).')
        self.pdf_type = pdf_type


class UnsupportedFileError(ValueError):
    """Same as Firecrawl's SCRAPE_UNSUPPORTED_FILE_ERROR: not a PDF, and not an
    image the image parser may OCR."""
    code = "SCRAPE_UNSUPPORTED_FILE_ERROR"

    def __init__(self, reason):
        super().__init__(
            f"The file type cannot be processed: {reason}. Supported are PDFs and raster "
            f"images (PNG, JPEG, JPEG 2000, TIFF, GIF, BMP, WebP, AVIF with Pillow 11.3+), "
            f'which are OCR\'d when the parsers option includes "image" (the default).')
        self.reason = reason


PAGE_MARKER = "---\n\n<!-- page {n} -->"   # Firecrawl's pageMarkers join, between pages


def load_source(src):
    """path | URL | bytes -> (bytes, display name, is_url)."""
    return _fetch(src)[:3]


def _fetch(src):
    """path | URL | bytes -> (bytes, display name, is_url, content type or None)."""
    if isinstance(src, (bytes, bytearray)):
        return bytes(src), "document.pdf", False, None
    if isinstance(src, str) and re.match(r"^https?://", src):
        req = urllib.request.Request(src, headers={"User-Agent": "pdf2md/1.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
            ctype = r.headers.get("Content-Type")
        name = os.path.basename(src.split("?")[0].rstrip("/")) or "document.pdf"
        return data, name, True, ctype
    with open(src, "rb") as f:
        return f.read(), os.path.basename(src), False, None


def convert(src, mode: str = "auto", max_pages: Optional[int] = None,
            page_range: Optional[str] = None, page_markers=False,
            images: str = "save", image_dir: Optional[str] = None,
            link_base: Optional[str] = None, ocr_lang: str = "eng",
            image_dpi: int = 150, password: Optional[str] = None,
            name: Optional[str] = None) -> Result:
    """Convert a PDF (path, URL, or bytes) to Markdown.
    mode       "fast": embedded text only, pages without text are skipped
               "auto": embedded text, OCR for pages without a text layer (default)
               "ocr":  OCR every page
    max_pages  parse at most this many pages (after page_range)
    page_range e.g. "1-5,8"
    page_markers True: Firecrawl's "---" + <!-- page N --> between pages;
               "every": <!-- page N --> before every page (vision draft, for splitting)
    images     "save" (PNG files in image_dir), "embed" (base64), or "none"
    link_base  folder that image links are written relative to
    """
    if mode not in ("fast", "auto", "ocr"):
        raise ValueError("mode must be 'fast', 'auto' or 'ocr'")
    if max_pages is not None:
        if isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages < 1:
            raise ValueError("maxPages must be an integer >= 1")
    t0 = time.time()
    data, src_name, _ = load_source(src)
    name = name or src_name
    stem = re.sub(r"\W+", "_", os.path.splitext(name)[0]) or "document"
    if images == "save" and not image_dir:
        image_dir = f"{stem}_images"

    with open_pdf(data, password) as pdf:
        n = len(pdf.pages)
        idx = parse_pages(page_range, n)
        if max_pages:
            idx = idx[:max_pages]
        stats = []
        pages_lines, heights, widths, page_regions = {}, {}, {}, {}
        all_images = []
        complex_layout = False

        ocr_missing = []       # pages that needed OCR but Tesseract was unavailable
        skipped = []           # pages fast mode skipped

        for i in idx:
            page = pdf.pages[i]
            pageno = i + 1
            heights[pageno], widths[pageno] = page.height, page.width
            st = page_stats(page)
            stats.append((pageno, st))
            kind = st["kind"]
            items = []

            if kind == "blank" or (mode == "fast" and kind == "scan"):
                if kind == "scan":
                    skipped.append(pageno)       # fast mode skips scanned pages
                pages_lines[pageno] = items
                page.close()
                continue

            # which path: native text, OCR, or both (hybrid)
            want_ocr = mode == "ocr" or (mode == "auto" and kind in ("scan", "hybrid"))
            ocr = ocr_blocks(page, ocr_lang) if want_ocr else None
            if want_ocr and ocr is None:
                ocr_missing.append(pageno)
            use_native = not (mode == "ocr" and ocr is not None) and kind != "scan"

            # embedded images; once a page is OCR'd its scan isn't repeated as a picture
            if images != "none":
                skip = 0.5 if ocr is not None else (0.9 if use_native else 1.01)
                refs = page_images(page, pageno, skip)
            else:
                refs = []
            ox, oy = page_origin(page)
            figs = []
            for ref in refs:
                if save_image(page, ref, images, image_dir, stem, image_dpi):
                    all_images.append(ref)
                    b = ref.bbox
                    fig = Line(pageno, [], b[0] - ox, b[2] - ox, b[1] - oy, b[3] - oy, "image", ref)
                    items.append(fig)
                    figs.append(fig)

            words = extract_words(page, pageno) if use_native else []

            if ocr is not None:
                # headings from text height (OCR has no font sizes)
                hs = [para["line_h"] for para in ocr if len(para["text"]) > 30] or \
                     [para["line_h"] for para in ocr]
                body_h = statistics.median(hs) if hs else 0
                for para in ocr:
                    r_h = para["line_h"] / body_h if body_h else 1
                    short = len(para["text"]) <= 100 and not para["text"].endswith(".")
                    para["level"] = (1 if r_h >= 1.8 else 2 if r_h >= 1.3 else None) \
                        if short else None
                ocr_items = []
                for para in ocr:
                    bx = para["bbox"]
                    if any(center_inside(bx, (f.x0, f.top, f.x1, f.bottom)) for f in figs):
                        continue                     # text inside a kept picture
                    if words and any(center_inside((w.x0, w.top, w.x1, w.bottom), bx)
                                     for w in words):
                        continue                     # native text already covers it
                    ocr_items.append(Line(pageno, [], bx[0], bx[2], bx[1], bx[3], "ocr", para))
                # keep Tesseract's reading order; slot figures in by position
                for fig in sorted(figs, key=lambda f: f.top):
                    items.remove(fig)
                    k = next((n for n, o in enumerate(ocr_items) if o.top > fig.top),
                             len(ocr_items))
                    ocr_items.insert(k, fig)
                items = ocr_items

            if use_native:
                layout = ocr is None          # hybrid (scan + text) pages keep the simple path
                full = (0.0, 0.0, page.width, page.height)
                rules = horizontal_rules(page, ox, oy) if layout else []
                # vector charts: saved as a picture next to their labels (text untouched)
                chart_boxes = [vf["bbox"] for vf in vector_figures(page, words, ox, oy)] if layout else []
                if images != "none":
                    for n_, fb in enumerate(chart_boxes):
                        px0, ptop, px1, pbottom = page.bbox
                        ab = (max(fb[0] + ox - 2, px0), max(fb[1] + oy - 2, ptop),
                              min(fb[2] + ox + 2, px1), min(fb[3] + oy + 2, pbottom))
                        ref = ImageRef(pageno, len(refs) + n_ + 1, ab, alt=f"Chart (page {pageno})")
                        if save_image(page, ref, images, image_dir, stem, image_dpi):
                            all_images.append(ref)
                            items.append(Line(pageno, [], fb[0], fb[2], fb[1], fb[3], "image", ref))
                # side-by-side blocks: split the page into regions, read each on its own
                obstacles = chart_boxes + [box_of(it) for it in items if it.kind == "image"]
                regions = segment_regions(words, rules, obstacles, full) if layout else [full]
                page_regions[pageno] = regions
                multi_region = len(regions) > 1
                for it in items:
                    it.region = region_of(box_of(it), regions)
                for k, rb in enumerate(regions):
                    rw = [w for w in words if region_of((w.x0, w.top, w.x1, w.bottom), regions) == k] \
                        if multi_region else words
                    rpage = page
                    if multi_region:
                        px0, ptop, px1, pbottom = page.bbox
                        try:
                            rpage = page.crop((max(rb[0] + ox, px0), max(rb[1] + oy, ptop),
                                               min(rb[2] + ox, px1), min(rb[3] + oy, pbottom)))
                        except Exception:
                            rpage = None
                    # tables (ruled first, then heuristic on the remaining text)
                    for bbox, rows, cols in (ruled_tables(rpage, (ox, oy)) if rpage is not None else []):
                        rows, head = table_header_above(bbox, rows, cols, rw)
                        hid = {id(w) for w in head}
                        tb = (bbox[0], min([bbox[1]] + [w.top for w in head]), bbox[2], bbox[3])
                        tw = [w for w in rw if id(w) in hid
                              or center_inside((w.x0, w.top, w.x1, w.bottom), bbox)]
                        tids = {id(w) for w in tw}
                        rw = [w for w in rw if id(w) not in tids]
                        items.append(Line(pageno, tw, tb[0], tb[2], tb[1], tb[3], "table", rows,
                                          region=k))
                    lines = group_lines(rw, pageno)
                    rrules = [r for r in rules if rb[0] - 1 <= r[0] and r[1] <= rb[2] + 1
                              and rb[1] <= r[2] <= rb[3]] if multi_region else rules
                    for a_, b_, rows in heuristic_tables(lines, rb[2] - rb[0], rrules):
                        tl = lines[a_:b_ + 1]
                        items.append(Line(pageno, [w for l in tl for w in l.words],
                                          min(l.x0 for l in tl), max(l.x1 for l in tl),
                                          tl[0].top, tl[-1].bottom, "table", rows, region=k))
                        for l in tl:
                            l.kind = "consumed"
                    for l in lines:
                        if l.kind == "text":
                            l.region = k
                            items.append(l)
            pages_lines[pageno] = items
            page.close()  # free pdfminer's per-page object cache

        pdf_type, confidence, needs_ocr = classify(stats)
        if mode == "fast" and pdf_type in ("Scanned", "ImageBased"):
            raise PDFOCRRequiredError(pdf_type)
        pages_lines, furniture = strip_furniture(pages_lines, heights)

        all_lines = [l for ls in pages_lines.values() for l in ls if l.kind == "text"]
        base = correct_base_size(all_lines, body_font_size(all_lines))
        tiers = heading_tiers(all_lines, base)
        text_widths = [l.x1 - l.x0 for l in all_lines if len(l.words) > 3]
        col_width = statistics.median(text_widths) if text_widths else 400

        def image_md(ref):
            alt = ref.alt or f"Image {ref.index} (page {ref.page})"
            if ref.data_uri:
                return f"![{alt}]({ref.data_uri})"
            if ref.path:
                path = os.path.relpath(os.path.abspath(ref.path), link_base) if link_base \
                    else ref.path
                return f"![{alt}]({path.replace(os.sep, '/')})"
            return ""

        # ---- assemble document markdown, per-page markdown and blocks ----
        doc = []          # fragments of the document markdown
        pos = 0
        pages_out, blocks_out = [], []
        table_pages, column_pages = [], []
        title_set = False
        title_text = None
        prev_list = False

        def emit(fragment, sep):
            nonlocal pos
            if doc:
                doc.append(sep)
                pos += len(sep)
            start = pos
            doc.append(fragment)
            pos += len(fragment)
            return [start, pos]

        for pageno in sorted(pages_lines):
            items = pages_lines[pageno]
            regs = page_regions.get(pageno, [])
            if len(regs) > 1:          # side-by-side regions: each read on its own, in order
                seq = []
                for k, rb in enumerate(regs):
                    sub = [l for l in items if l.region == k]
                    if any(l.kind == "text" for l in sub):
                        seq.extend(reading_order(sub, rb[0], rb[2])[0])
                    else:
                        seq.extend(sorted(sub, key=lambda l: (l.top, l.x0)))
                complex_layout = True
                column_pages.append(pageno)
            elif any(l.kind == "text" for l in items):
                seq, multi = reading_order(items, widths[pageno])
                complex_layout |= multi
                if multi:
                    column_pages.append(pageno)
            elif any(l.kind == "ocr" for l in items):
                seq = items          # OCR page: Tesseract order, figures already slotted in
            else:
                seq = sorted(items, key=lambda l: (l.top, l.x0))
            blks = render_blocks(seq, base, tiers, col_width, image_md)

            if page_markers == "every":       # internal (vision draft): every page, for splitting
                emit(f"<!-- page {pageno} -->", "\n\n")
                prev_list = False
            elif page_markers and doc:        # Firecrawl: between pages, none before the first
                emit(PAGE_MARKER.format(n=pageno), "\n\n")
                prev_list = False

            w, h = widths[pageno], heights[pageno]
            heads = [(r, l) for r, l in furniture.get(pageno, []) if l.top < h / 2]
            foots = [(r, l) for r, l in furniture.get(pageno, []) if l.top >= h / 2]
            page_items, page_md, page_prev_list = [], [], False

            def furniture_item(role, l):
                return {"type": role, "label": role, "bbox": norm_bbox(box_of(l), w, h),
                        "content": line_text(l).strip(), "markdownSpan": None,
                        "source": "ocr" if l.kind == "ocr" else "native_text",
                        "confidence": {"layout": None,
                                       "ocr": l.payload.get("conf") if l.kind == "ocr" else None}}

            for role, l in heads:
                page_items.append(furniture_item(role, l))
            for b in blks:
                frag = postprocess_fragment(b["md"])
                if not frag:
                    continue
                btype = b["type"]
                if btype == "section_header" and not title_set:
                    title_set = True           # only the document's first heading...
                    if b["level"] == 1:        # ...and only if it is an H1
                        btype = "title"
                        title_text = re.sub(r"^#+\s*", "", frag)
                tight = b["list"] and prev_list
                span = emit(frag, "\n" if tight else "\n\n")
                page_md.append(("\n" if b["list"] and page_prev_list else "\n\n") if page_md else "")
                page_md.append(frag)
                prev_list = page_prev_list = b["list"]
                page_items.append({
                    "type": btype, "label": btype,
                    "bbox": norm_bbox(union_box(b["boxes"]), w, h),
                    "content": frag, "markdownSpan": span, "source": b["source"],
                    "confidence": {"layout": None, "ocr": b["conf"]}})
            for role, l in foots:
                page_items.append(furniture_item(role, l))
            for k, it in enumerate(page_items):
                it["id"] = f"p{pageno}.b{k}"
                it["readingOrder"] = k
            page_items = [{"id": it.pop("id"), **it} for it in page_items]
            if any(it["type"] == "table" for it in page_items):
                table_pages.append(pageno)
            pages_out.append({"pageNumber": pageno, "markdown": "".join(page_md)})
            status = ("skipped" if pageno in skipped else
                      "ocr_unavailable" if pageno in ocr_missing and not page_md else "ok")
            blocks_out.append({"pageNumber": pageno, "width": round(w, 2), "height": round(h, 2),
                               "status": status, "items": page_items})

        md = "".join(doc) + "\n" if doc else ""

        meta_title = (pdf.metadata or {}).get("Title")
        if isinstance(meta_title, bytes):
            meta_title = meta_title.decode("utf-8", "ignore")
        title = (meta_title or "").strip() or title_text

    r = Result(md, pdf_type, n, confidence, needs_ocr, title,
               round((time.time() - t0) * 1000, 1), all_images, complex_layout)
    r.num_pages = len(idx)
    r.pages_with_tables, r.pages_with_columns = table_pages, column_pages
    r.pages = pages_out
    r.blocks = blocks_out
    r.source_name = name
    r.warnings = []
    if ocr_missing:
        r.warnings.append(f"pages {ocr_missing} need OCR but Tesseract is not available; "
                          f"install tesseract-ocr and pytesseract")
    if skipped:
        r.warnings.append(f"fast mode skipped scanned pages {skipped}")
    return r


# ───────────────────────────── 11. Firecrawl-style API ────────────────────

def is_pdf_buffer(data: bytes) -> bool:
    """Firecrawl's isPdfBuffer: `%PDF` anywhere in the first 1 KB (BOM, whitespace
    or junk may come first)."""
    return b"%PDF" in data[:1024]


PDF_OPTIONS = {"type", "mode", "maxPages", "pages", "blocks", "pageMarkers", "pageMarkdown", "images"}


def _pdf_options(opts: dict) -> dict:
    """Check one pdf parser object like Firecrawl's schema (strict object, typed fields) and
    fold the deprecated `pageMarkdown` into `pages` (an explicit `pages` wins)."""
    unknown = set(opts) - PDF_OPTIONS
    if unknown:
        raise ValueError(f"unknown pdf parser options: {sorted(unknown)}")
    if "mode" in opts and opts["mode"] not in ("fast", "auto", "ocr"):
        raise ValueError("mode must be 'fast', 'auto' or 'ocr'")
    for opt in ("pages", "blocks", "pageMarkers", "pageMarkdown"):       # z.boolean()
        if opt in opts and not isinstance(opts[opt], bool):
            raise ValueError(f"{opt} must be true or false")
    if "maxPages" in opts:                                               # z.int(), 1..10000
        mp = opts["maxPages"]
        if isinstance(mp, float) and mp.is_integer():
            mp = int(mp)                                                 # JSON 2.0 is the integer 2
        if isinstance(mp, bool) or not isinstance(mp, int) or not 1 <= mp <= 10000:
            raise ValueError("maxPages must be an integer from 1 to 10000")
        opts["maxPages"] = mp
    alias = opts.pop("pageMarkdown", None)
    if "pages" not in opts and alias is not None:
        opts["pages"] = alias
    return opts


def _parser_opts(parsers):
    """Normalize Firecrawl `parsers` -> (pdf_options or None, image_enabled).
    As in Firecrawl's schema: only "pdf" and "image" (string or object form), the
    image object takes no options, every pdf parser's options are checked for any
    input, and the first pdf parser is the one used."""
    if parsers is None:
        parsers = ["pdf", "image"]
    pdf_opts, image_on = None, False
    for p in parsers:
        ptype = p if isinstance(p, str) else p.get("type") if isinstance(p, dict) else None
        if ptype == "pdf":
            opts = _pdf_options({} if isinstance(p, str) else dict(p))
            pdf_opts = opts if pdf_opts is None else pdf_opts
        elif ptype == "image":
            extra = sorted(set(p) - {"type"}) if isinstance(p, dict) else []
            if extra:
                raise ValueError(f"image parser takes no options: {extra}")
            image_on = True
        else:
            raise ValueError(f'unknown parser {p!r}: use "pdf" or "image"')
    return pdf_opts, image_on


def scrape(url, formats=("markdown",), parsers=None, password=None,
           images: Optional[str] = None, image_dir: Optional[str] = None,
           link_base: Optional[str] = None, ocr_lang: str = "eng",
           page_range: Optional[str] = None, image_dpi: int = 150,
           include_hidden: bool = False, filename: Optional[str] = None,
           charts: bool = False, chunk=None) -> dict:
    """Firecrawl /scrape-compatible call for PDFs, images and spreadsheets.
    parsers: like Firecrawl — default ["pdf", "image"]; [] returns the file base64'd
    in `markdown`. PDF options: {"type": "pdf", "mode": "fast"|"auto"|"ocr",
    "maxPages": int, "pages": bool, "blocks": bool, "pageMarkers": bool}.
    Extras (not in Firecrawl): "images": "save"|"embed"|"none" (default: "save"
    when image_dir is given, otherwise "embed" — no files are written unasked),
    page_range "1-5,8", image_dpi, ocr_lang.
    Spreadsheets (xlsx, xlsm, csv) are found like Firecrawl does — URL path / file name,
    then Content-Type, then the bytes — and converted by sheet2md; PDF options don't apply.
    include_hidden keeps hidden sheets/rows/columns; charts adds each chart's saved numbers
    as a table (not in Firecrawl); filename names a bytes source.
    chunk: True or {"maxChars": N} adds `data.chunks`, the document cut into pieces for
    embeddings by chunk2md.py (not in Firecrawl).
    """
    if images is None:
        images = "save" if image_dir else "embed"
    chunk_size = None
    if chunk:
        import chunk2md
        opts = {} if chunk is True else chunk
        if not isinstance(opts, dict) or set(opts) - {"maxChars"}:
            raise ValueError('chunk must be true or {"maxChars": N}')
        chunk_size = chunk2md.check_size(opts.get("maxChars", chunk2md.CHUNK_MAX_CHARS))
    data, name, is_url, ctype = _fetch(url)
    if filename:
        name = filename
    pdf_opts, image_on = _parser_opts(parsers)
    meta = {"sourceURL" if is_url else "sourceFile": url if is_url else name}
    is_pdf = is_pdf_buffer(data)
    warnings = [f"format '{f}' is not supported for files; ignored"
                for f in formats if f != "markdown"]

    def raw():                  # Firecrawl: the file base64'd as the content (html = markdown)
        return {"success": True, "data": {"markdown": base64.b64encode(data).decode(),
                                          "metadata": meta}}

    sheet_ext = None
    if not is_pdf:
        import sheet2md
        sheet_ext = sheet2md.spreadsheet_ext(url if is_url else name, ctype, data)

    if sheet_ext:
        if parsers is not None and not parsers:
            return raw()
        r = sheet2md.convert(data, include_hidden=include_hidden, ext=sheet_ext, charts=charts)
        if r.warning:
            warnings.append(r.warning)
        meta["contentType"] = sheet2md.CONTENT_TYPE_OF[sheet_ext]
        if sheet_ext != ".csv":
            meta.update({"numSheets": len(r.sheets), "sheetNames": r.sheets})
        out = {"metadata": meta}
        if "markdown" in formats:
            out["markdown"] = r.markdown
        chunk_src = r
    elif is_pdf:
        if pdf_opts is None:
            return raw()
        r = convert(data, mode=pdf_opts.get("mode", "auto"), max_pages=pdf_opts.get("maxPages"),
                    page_range=page_range, page_markers=pdf_opts.get("pageMarkers", False),
                    images=pdf_opts.get("images", images), image_dir=image_dir,
                    link_base=link_base, ocr_lang=ocr_lang, image_dpi=image_dpi,
                    password=password, name=name)
        warnings += r.warnings
        meta.update({"title": r.title, "numPages": r.num_pages, "totalPages": r.page_count,
                     "pdfType": r.pdf_type, "confidence": r.confidence,
                     "pagesNeedingOcr": r.pages_needing_ocr,
                     "processingTimeMs": r.processing_time_ms})
        out = {"metadata": meta}
        if "markdown" in formats:
            out["markdown"] = r.markdown
        if pdf_opts.get("pages"):
            out["pages"] = r.pages
        if pdf_opts.get("blocks"):
            out["blocks"] = r.blocks
        chunk_src = r
    else:
        from PIL import Image
        try:
            img = Image.open(io.BytesIO(data))
            img.load()
        except Exception:
            raise UnsupportedFileError("not a PDF or a readable image")
        if not image_on:
            raise UnsupportedFileError('an image, and the parsers option leaves out "image"')
        paras = ocr_image(flatten_alpha(img), ocr_lang) if ocr_available() else None
        if paras is None:
            warnings.append("image needs OCR but Tesseract is not available; "
                            "install tesseract-ocr and pytesseract")
            paras = []
        out = {"metadata": meta}
        image_md = "\n\n".join(p["text"] for p in paras) + ("\n" if paras else "")
        if "markdown" in formats:
            out["markdown"] = image_md
        chunk_src = image_md
    if chunk_size is not None:
        out["chunks"] = chunk2md.chunk_markdown(chunk_src, chunk_size)
    if warnings:
        out["warning"] = "; ".join(warnings)
    return {"success": True, "data": out}


# ───────────────────────────── 12. vision step (charts, scans) ──────────

VISION_DOC = """Charts and scanned pages, read by the agent's own vision model.

Two steps around the model, so no API keys or network calls are needed here:

  python pdf2md.py prepare report.pdf work/   # convert + images + work/tasks.json
  (the agent opens each image in tasks.json, follows its prompt,
   and writes the answer to work/answers/<id>.md)
  python pdf2md.py merge work/ -o report.md   # check numbers, build final markdown

Every number in an answer is looked up in the page's own text (native text, or
Tesseract OCR for scans and pictures). Numbers that aren't found get a warning
sign; values the model read off the drawing are written with a leading ~.
"""

MIN_PICTURE_PT = (150, 100)     # embedded pictures smaller than this are logos/icons
SCAN_DPI = 200
MAX_SIDE_PX = 2000
CONTEXT_CHARS = 3000

LEGEND = ("~ = estimated from the drawing, not printed; "
          "\u26a0 = number not found in the page text, check it against the image.")

CHART_PROMPT = """This image is a chart from page {page} of a PDF.
Text found on that page, for the exact spelling of labels:
---
{context}
---
Write the chart's data as one Markdown table: first column the categories (axis
labels or slice names), then one column per series, named as in the legend.
Copy every number that is printed on the chart exactly as printed.
If a value is not printed and you read it from the bar length or the axis, write
it with a leading ~ (for example ~42). If you cannot read a value, leave the cell empty.
Put the chart title on one line above the table if the chart has one.
If the chart prints no numbers and has no numbered axis, do not make a table; write:
No values shown on this chart. Categories: <names>. Series: <names>.
Output only the title line and the table (or that one line)."""

PICTURE_PROMPT = """This image is a picture from page {page} of a PDF.
If it is a chart or a table, write its data as Markdown: a title line if it has one,
then a Markdown table. Copy printed numbers exactly; a value read from a bar length
or an axis gets a leading ~ (for example ~42); leave a cell empty if you cannot read it.
If it is not a chart or a table (a logo, a photo, a signature), output only: NONE"""

TABLES_PROMPT = """This image is page {page} of a PDF. It has {count} table(s).
Our text extraction of the page, for the exact spelling of words and numbers:
---
{context}
---
Write only the tables, in the order they appear on the page, each as one Markdown table
(a header row, then one row per row on the page). Put a blank line between tables.
Copy every word and number exactly as printed, including minus signs and brackets around
negative numbers. Keep every row and column; leave a cell empty only if it is empty on
the page. Do not add totals, notes, or anything that is not in the table.
Output only the tables."""

LAYOUT_PROMPT = """This image is page {page} of a PDF laid out in columns or side-by-side blocks.
Our text extraction of the page, for the exact spelling of words and numbers:
---
{context}
---
Write the whole page as Markdown in reading order: each column top to bottom before the
next one, each side-by-side block on its own. Headings as #, ##, ###; paragraphs as text;
lists as - or 1.; tables as Markdown tables, one row per row on the page.
Copy every word and number exactly as printed, including minus signs and brackets.
Do not summarize, reword, skip, or add anything that is not on the page.
Output only the Markdown."""

PAGE_PROMPT = """This image is page {page} of a scanned PDF.
Transcribe the whole page as Markdown, top to bottom:
- headings as #, ##, ###; paragraphs as text; lists as - or 1.
- anything laid out in rows and columns as a Markdown table, one row per row
- each chart as a title line followed by a Markdown table of its values
Copy every word and every printed number exactly. A chart value that is not printed
and that you read from a bar length or an axis gets a leading ~ (for example ~42).
Do not summarize, skip, or add anything that is not on the page.
Output only the Markdown."""


# ---- numbers ----
# A number in the model's answer must match the page in value, sign and row.

NUM_RE = re.compile(r"(~\s*)?(\(\s*)?([-−]\s*)?(?<![\w.])(\d[\d,]*(?:\.\d+)?)(\s*\))?")
WORD_RE = re.compile(r"[^\W\d_]{2,}")
GROUPED_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
ISSUES = (("unverified", "NOT FOUND in page text"), ("sign", "SIGN DIFFERS from page text"),
          ("wrong_row", "IN ANOTHER ROW"), ("row_unconfirmed", "ROW NOT CONFIRMED"))


def norm_number(s: str) -> str:
    """'1,200' -> '1200', '34.80' -> '34.8', '05' -> '5'; '12,00' (not thousands groups) stays."""
    s = s.rstrip(",")
    if not GROUPED_RE.fullmatch(s):
        return s
    s = s.replace(",", "")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s.lstrip("0") or "0"


def number_tokens(text: str) -> list:
    """[{n, neg, est, start, end}]: neg is True for -x or (x), None for a date part (07/2024)."""
    out = []
    for m in NUM_RE.finditer(text):
        est, lp, minus, raw, rp = m.groups()
        if minus and m.start(3) > 0 and text[m.start(3) - 1].isalnum():
            minus = None                                     # 2019-2020: a range dash
        date = text[m.start(4) - 1:m.start(4)] == "/" or text[m.end(4):m.end(4) + 1] == "/"
        out.append({"n": norm_number(raw), "neg": None if date else bool(minus or (lp and rp)),
                    "est": bool(est), "start": m.start(), "end": m.end(), "raw": raw.rstrip(",")})
    return out


def numbers_in(text: str) -> set:
    return {t["n"] for t in number_tokens(text) if not t["est"]}


def _keys(text) -> set:
    out = set()
    for t in number_tokens(text):
        if not t["est"]:
            out |= {t["n"], t["n"] + "-"} if t["neg"] is None else {t["n"] + "-" * t["neg"]}
    return out


ROW_CELL_MAX_WORDS = 3      # a data row's value cells are short; longer cells are prose


def _row_labels(answer) -> list:
    """[(start, end, label)] for answer data rows: first cell is text, value cells are short.
    Rows with prose cells (an equipment list) get no row check: their label words recur
    all over the page."""
    out, pos = [], 0
    for line in answer.splitlines(keepends=True):
        st = line.strip()
        if st.startswith("|") and not re.fullmatch(r"[|\-:\s]+", st):
            cells = [c.strip() for c in st.strip("|").split("|")]
            lab = cells[0]
            if len(lab) >= 2 and not number_tokens(lab) and \
                    all(len(WORD_RE.findall(c)) <= ROW_CELL_MAX_WORDS for c in cells[1:]):
                out.append((pos, pos + len(line), lab))
        pos += len(line)
    return out


def _label_values(source, labels) -> dict:
    """label -> number keys next to it on the page: on each line where the label occurs, from the
    label to the next other label on that line (nested labels share); if that stretch has no
    number (values wrapped below the label), the line below."""
    lines = source.splitlines()
    pats = {lab: re.compile(r"(?<!\w)" + re.escape(lab.lower()) + r"(?!\w)") for lab in labels}
    out = {}
    for i, line in enumerate(lines):
        low = line.lower()
        hits = sorted((m.start(), m.end(), lab) for lab, pat in pats.items() for m in pat.finditer(low))
        for st, en, lab in hits:
            stop = next((h[0] for h in hits if h[0] >= en and h[2] != lab), len(line))
            keys = _keys(line[en:stop])
            if not keys and i + 1 < len(lines):
                keys = _keys(lines[i + 1])
            out.setdefault(lab, set()).update(keys)
    return out


def check_answer(answer: str, source: str):
    """Mark numbers that don't match the page. Returns (marked text, issues, estimated):
    issues = {unverified, sign, wrong_row, row_unconfirmed} -> lists of numbers as written;
    the first three are marked with a warning sign in the text."""
    seen = {}
    for t in number_tokens(source):
        if not t["est"]:
            seen.setdefault(t["n"], []).append(t["neg"])
    rows = _row_labels(answer)
    near = _label_values(source, [r[2] for r in rows])
    issues = {k: [] for k, _ in ISSUES}
    estimated, out, last = 0, [], 0
    for t in number_tokens(answer):
        kind = None
        if t["est"]:
            estimated += 1
        elif t["n"] not in seen:
            kind = "unverified"
        elif not any(s is None or t["neg"] is None or s == t["neg"] for s in seen[t["n"]]):
            kind = "sign"
        else:
            row = next((r for r in rows if r[0] <= t["start"] < r[1]), None)
            if row and t["start"] - row[0] > len(row[2]):        # a value cell, not the label
                want = {t["n"], t["n"] + "-"} if t["neg"] is None else {t["n"] + "-" * t["neg"]}
                if not want & near.get(row[2], set()):
                    if any(want & near.get(r[2], set()) for r in rows if r[2] != row[2]):
                        kind = "wrong_row"
                    else:
                        issues["row_unconfirmed"].append(t["raw"])
        out.append(answer[last:t["end"]] + (" ⚠" if kind else ""))
        last = t["end"]
        if kind:
            issues[kind].append(t["raw"])
    out.append(answer[last:])
    return "".join(out), issues, estimated


# ---- prepare ----

def _upright(img):
    rot = detect_rotation(img) if ocr_available() else 0
    img = img.rotate(-rot, expand=True) if rot else img
    img.thumbnail((MAX_SIDE_PX, MAX_SIDE_PX))
    return img


def _ocr_text(img) -> str:
    if not ocr_available():
        return ""
    paras = ocr_image(flatten_alpha(img)) or []
    return "\n".join(p["text"] for p in paras)


def prepare(src, work, mode="auto", page_range=None, max_pages=None, password=None,
            hard_pages=False, max_hard_pages=None):
    if not ocr_available():                 # numbers on scans/pictures are checked against OCR
        raise RuntimeError("Tesseract is required for the vision step: install it "
                           "(macOS: brew install tesseract; Linux: apt install tesseract-ocr) "
                           "and pip install pytesseract")
    os.makedirs(os.path.join(work, "answers"), exist_ok=True)
    img_dir = os.path.join(work, "images")
    r = convert(src, mode=mode, page_range=page_range, max_pages=max_pages,
                       password=password, page_markers="every", images="save",
                       image_dir=img_dir, link_base=work)
    with open(os.path.join(work, "draft.md"), "w", encoding="utf-8") as f:
        f.write(r.markdown)
    page_md = {p["pageNumber"]: p["markdown"] for p in r.pages}
    have_ocr = ocr_available()
    tasks = []

    # charts and pictures already saved by convert()
    for ref in r.images:
        if not ref.path:
            continue
        w_, h_ = ref.bbox[2] - ref.bbox[0], ref.bbox[3] - ref.bbox[1]
        chart = ref.alt.startswith("Chart")
        if not chart and (w_ < MIN_PICTURE_PT[0] or h_ < MIN_PICTURE_PT[1]):
            continue
        link = os.path.relpath(os.path.abspath(ref.path), work).replace(os.sep, "/")
        context = page_md.get(ref.page, "")
        check = context
        if not chart:                       # numbers on a picture are pixels: OCR them
            from PIL import Image
            with Image.open(ref.path) as im:
                check += "\n" + _ocr_text(im)
        tid = f"p{ref.page}_{'chart' if chart else 'img'}{ref.index}"
        prompt = (CHART_PROMPT if chart else PICTURE_PROMPT).format(
            page=ref.page, context=context[:CONTEXT_CHARS])
        tasks.append({"id": tid, "kind": "chart" if chart else "picture", "page": ref.page,
                      "image": link, "link": link, "prompt": prompt, "check_text": check})

    # scanned pages: the whole page, upright
    scan_dir = os.path.join(work, "scans")
    data, _, _ = load_source(src)
    with open_pdf(data, password) as pdf:
        for pageno in sorted(page_md):
            page = pdf.pages[pageno - 1]
            if page_stats(page)["kind"] not in ("scan", "hybrid") and mode != "ocr":
                continue
            os.makedirs(scan_dir, exist_ok=True)
            img = _upright(page.to_image(resolution=SCAN_DPI).original)
            path = os.path.join(scan_dir, f"page_{pageno}.png")
            img.save(path)
            tasks.append({"id": f"p{pageno}_page", "kind": "page", "page": pageno,
                          "image": f"scans/page_{pageno}.png",
                          "prompt": PAGE_PROMPT.format(page=pageno),
                          "check_text": page_md.get(pageno, "")})
            page.close()

        # hard text pages: tables (tables only) or columns (whole page), checked against the text layer
        if hard_pages:
            scanned = {t["page"] for t in tasks if t["kind"] == "page"}
            hard = [p for p in sorted(page_md) if p not in scanned
                    and (p in r.pages_with_columns or p in r.pages_with_tables)]
            for pageno in hard[:max_hard_pages]:
                page = pdf.pages[pageno - 1]
                os.makedirs(os.path.join(work, "hard"), exist_ok=True)
                page.to_image(resolution=SCAN_DPI).original.save(
                    os.path.join(work, "hard", f"page_{pageno}.png"))
                native = page.extract_text() or ""
                context = page_md.get(pageno, "")[:CONTEXT_CHARS]
                if pageno in r.pages_with_columns:
                    task = {"id": f"p{pageno}_layout", "kind": "layout", "reason": "columns",
                            "prompt": LAYOUT_PROMPT.format(page=pageno, context=context)}
                else:
                    n = len(TABLE_BLOCK_RE.findall(page_md.get(pageno, "")))
                    task = {"id": f"p{pageno}_tables", "kind": "tables", "tables": n,
                            "reason": f"{n} table{'s' * (n != 1)}",
                            "prompt": TABLES_PROMPT.format(page=pageno, count=n, context=context)}
                task.update(page=pageno, image=f"hard/page_{pageno}.png", check_text=native)
                tasks.append(task)
                page.close()

    manifest = {"source": r.source_name, "pdf_type": r.pdf_type, "ocr_available": have_ocr,
                "pages": sorted(page_md), "tasks": tasks}
    with open(os.path.join(work, "tasks.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    return manifest


# ---- merge ----

TABLE_BLOCK_RE = re.compile(r"(?:^\|.*\|[ \t]*(?:\n|$))+", re.M)
# Provisional limits for a whole-page (layout) answer against the page's text layer: (at least,
# share of the page's words). Set from the evaluation on 10 real pages (tasks/hard-pages.md, D).
LAYOUT_MAX_NEW_WORDS = (2, 0.005)      # answer words that are not on the page
LAYOUT_MAX_MISSING_WORDS = (3, 0.01)   # page words left out of the answer
LAYOUT_MAX_NEW_PAIRS = (3, 0.01)       # answer word pairs found neither in our draft nor on a page line


def _words(text):
    return WORD_RE.findall(re.sub(r"(\w)-\n(\w)", r"\1\2", text).lower())


def _pairs(words):
    return list(zip(words, words[1:]))


def word_check(answer, native, ours):
    """Does a whole-page answer say what the page says? -> list of problems (empty = ok)."""
    page = Counter(_words(native))
    n = max(sum(page.values()), 1)
    left = page.copy()
    split = defaultdict(list)            # text-layer quirk: one word in two pieces ("do cument")
    for line in native.splitlines():
        ws = _words(line)
        for a, b in _pairs(ws):
            split[a + b].append((a, b))
    new, ws, i = [], _words(answer), 0
    while i < len(ws):
        w = ws[i]
        joined = w + ws[i + 1] if i + 1 < len(ws) else None
        if left[w]:
            left[w] -= 1
        elif joined and left[joined]:        # the other quirk: two words run together ("eachform")
            left[joined] -= 1
            i += 1
        elif any(left[a] and left[b] for a, b in split.get(w, ())):
            a, b = next((a, b) for a, b in split[w] if left[a] and left[b])
            left[a] -= 1
            left[b] -= 1
        else:
            new.append(w)
        i += 1
    missing = sum(left.values())
    known = set(_pairs(_words(ours))) | {p for line in native.splitlines() for p in _pairs(_words(line))}
    prose = [line for line in answer.splitlines() if not line.lstrip().startswith("|")]
    new_pairs = {p for line in prose for p in _pairs(_words(line)) if p not in known}   # tables: cells
    # wrap and the page's lines mix columns, so word order is only checked in prose
    limit = lambda rule: max(rule[0], rule[1] * n)
    problems = []
    if len(new) > limit(LAYOUT_MAX_NEW_WORDS):
        problems.append(f"words not on the page: {', '.join(new[:8])}")
    if missing > limit(LAYOUT_MAX_MISSING_WORDS):
        problems.append(f"page words missing from the answer: {missing} of {n}")
    if len(new_pairs) > limit(LAYOUT_MAX_NEW_PAIRS):
        problems.append(f"word order not on the page: {len(new_pairs)} pairs")
    return problems


def _merge_hard(t, answer, pages):
    """A tables/layout answer for a text page: used only if it matches the text layer, else ours stays.
    Returns the report fields."""
    out = {}
    page_md = pages.get(t["page"], "")

    def kept(why):
        out["status"] = f"kept rule-based output: {why}"
        return out
    if t["kind"] == "tables":
        theirs = [m.group().strip() for m in TABLE_BLOCK_RE.finditer(answer)]
        ours = list(TABLE_BLOCK_RE.finditer(page_md))
        if not len(theirs) == len(ours) == t["tables"]:
            return kept(f"table count differs (answer {len(theirs)}, page {t['tables']})")
        checked = "\n\n".join(theirs)
    else:
        checked = answer
    _, issues, estimated = check_answer(checked, t["check_text"])
    out.update(issues)
    if estimated:
        return kept(f"{estimated} estimated value(s) on a page with a text layer")
    if issues["unverified"] or issues["sign"] or issues["wrong_row"]:
        return kept("numbers don't match the page")
    if t["kind"] == "tables":
        for m, table in reversed(list(zip(ours, theirs))):
            end = m.start() + len(m.group().rstrip("\n"))
            page_md = page_md[:m.start()] + table + page_md[end:]
        pages[t["page"]] = page_md
        out["status"] = f"merged {len(theirs)} table{'s' * (len(theirs) != 1)}"
        return out
    problems = word_check(answer, t["check_text"], page_md)
    if problems:
        return kept("; ".join(problems))
    pages[t["page"]] = answer.strip()
    out["status"] = "merged page"
    return out

PAGE_SPLIT = re.compile(r"<!-- page (\d+) -->\n?")


def _split_pages(md: str) -> dict:
    """draft.md (a marker before every page) -> {page: markdown}."""
    parts = PAGE_SPLIT.split(md)
    return {int(parts[i]): parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}


def merge(work, page_markers=False):
    with open(os.path.join(work, "tasks.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    with open(os.path.join(work, "draft.md"), encoding="utf-8") as f:
        pages = _split_pages(f.read())
    report = []
    for t in manifest["tasks"]:
        ans_path = os.path.join(work, "answers", t["id"] + ".md")
        entry = {"id": t["id"], "kind": t["kind"], "page": t["page"], "image": t["image"]}
        if not os.path.exists(ans_path):
            entry["status"] = "no answer: original output kept"
            report.append(entry)
            continue
        with open(ans_path, encoding="utf-8") as f:
            answer = f.read().strip()
        answer = re.sub(r"^```\w*\n|\n```$", "", answer).strip()  # stray code fences
        if t["kind"] in ("tables", "layout"):
            entry.update(_merge_hard(t, answer, pages))
            report.append(entry)
            continue
        if t["kind"] == "picture" and answer.upper().startswith("NONE"):
            entry["status"] = "not a chart or table"
            report.append(entry)
            continue
        marked, issues, estimated = check_answer(answer, t["check_text"])
        entry.update(status="merged", estimated=estimated, **issues)
        if not t["check_text"].strip():
            entry["note"] = "no page text to check against (no OCR?): every number is unchecked"
        md = pages.get(t["page"], "")
        if t["kind"] == "page":
            note = f"*Page read from the scan by the vision model. {LEGEND}*"
            pages[t["page"]] = f"![Page {t['page']} (scan)]({t['image']})\n\n{note}\n\n{marked}"
        else:
            note = f"*Chart data read from the image by the vision model. {LEGEND}*"
            pos = md.find("](" + t["link"] + ")")
            if pos < 0:
                entry["status"] = "image link not found in draft: answer appended to page"
                pages[t["page"]] = md + f"\n\n{note}\n\n{marked}"
            else:
                end = md.find("\n", pos)
                end = len(md) if end < 0 else end
                pages[t["page"]] = md[:end] + f"\n\n{note}\n\n{marked}" + md[end:]
        report.append(entry)

    out = []
    for pn in manifest["pages"]:
        body = pages.get(pn, "")
        if page_markers and out:
            out.append(PAGE_MARKER.format(n=pn))
        if body:
            out.append(body)
    md = "\n\n".join(out) + "\n"
    with open(os.path.join(work, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    return md, report


def summary(report) -> str:
    lines = []
    for e in report:
        s = f"{e['id']}: {e['status']}"
        for key, label in ISSUES:
            if e.get(key):
                s += f"; {label}: {', '.join(e[key])}"
        if e.get("estimated"):
            s += f"; {e['estimated']} estimated value(s)"
        if e.get("note"):
            s += f"; {e['note']}"
        lines.append(s)
    return "\n".join(lines) or "no charts or scanned pages"


def vision_main(argv):
    ap = argparse.ArgumentParser(prog="pdf2md.py", description=VISION_DOC,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare", help="convert the PDF and list images for the model")
    p.add_argument("pdf")
    p.add_argument("work", help="work folder (created)")
    p.add_argument("--mode", default="auto", choices=["auto", "ocr"])
    p.add_argument("--page-range")
    p.add_argument("--max-pages", type=int)
    p.add_argument("--password")
    p.add_argument("--hard-pages", action="store_true",
                   help="also send text pages with tables (tables only) or columns (whole page)")
    p.add_argument("--max-hard-pages", type=int, help="at most N hard pages, in page order")
    m = sub.add_parser("merge", help="check the model's answers and write the final markdown")
    m.add_argument("work")
    m.add_argument("-o", "--output", help="markdown file (default: print)")
    m.add_argument("--page-markers", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "prepare":
            man = prepare(a.pdf, a.work, a.mode, a.page_range, a.max_pages, a.password,
                          a.hard_pages, a.max_hard_pages)
        else:
            md, report = merge(a.work, a.page_markers)
    except Exception as e:  # noqa: BLE001 -- one clean line, as for plain conversion
        sys.exit(f"error: {error_text(e, getattr(a, 'password', None))}")
    if a.cmd == "prepare":
        print(f"{len(man['tasks'])} image task(s) in {os.path.join(a.work, 'tasks.json')}")
        for t in man["tasks"]:
            why = f"   ({t['reason']})" if t.get("reason") else ""
            print(f"  {t['id']}: {t['kind']}, page {t['page']}, image {t['image']}{why}")
        return
    if a.output:
        with open(a.output, "w", encoding="utf-8") as f:
            f.write(md)
    else:
        sys.stdout.write(md)
    print(summary(report), file=sys.stderr)


def error_text(e, password=None) -> str:
    """One-line CLI message for an exception; Firecrawl error codes first."""
    msg = repr(e) + " " + repr(getattr(e, "__cause__", ""))
    if "PDFPasswordIncorrect" in msg:                     # pdfminer wraps password errors
        return "incorrect password" if password else "this PDF is encrypted — pass --password"
    if isinstance(e, FileNotFoundError):
        return f"file not found: {e.filename}"
    text = str(e) or type(e).__name__
    code = getattr(e, "code", None)
    return f"{code}: {text}" if isinstance(code, str) else text


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ("prepare", "merge"):
        return vision_main(argv)
    ap = argparse.ArgumentParser(
        description="PDF to Markdown with Firecrawl-style options "
                    "(headings, paragraphs, lists, tables, images, OCR).",
        epilog="vision step (charts, pictures, scanned pages; needs Tesseract):\n"
               "  pdf2md.py prepare <pdf> <work>   then the agent answers work/answers/<id>.md\n"
               "  pdf2md.py merge <work> -o out.md\n"
               "  see: pdf2md.py prepare --help, pdf2md.py merge --help",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="PDF, image or spreadsheet (xlsx, xlsm, csv): path or http(s) URL")
    ap.add_argument("-o", "--output", help="output file (default: stdout)")
    ap.add_argument("--mode", choices=["fast", "auto", "ocr"], default="auto",
                    help="fast: embedded text only; auto: OCR pages with no text (default); "
                         "ocr: OCR every page")
    ap.add_argument("--max-pages", type=int, help="parse at most N pages")
    ap.add_argument("--pages", action="store_true",
                    help="include per-page markdown (JSON output)")
    ap.add_argument("--blocks", action="store_true",
                    help="include typed layout blocks (JSON output)")
    ap.add_argument("--page-markers", action="store_true",
                    help="insert ---/<!-- page N --> between pages (Firecrawl's pageMarkers)")
    ap.add_argument("--json", action="store_true",
                    help="print the Firecrawl-style JSON response instead of markdown")
    ap.add_argument("--parsers", help='raw Firecrawl parsers JSON, e.g. '
                    '\'[{"type":"pdf","mode":"fast","maxPages":5}]\' (overrides the flags above)')
    ap.add_argument("--page-range", help="page selection, e.g. 1-5,8 (extra)")
    ap.add_argument("--images", choices=["save", "embed", "none"], default="save",
                    help="save PNGs next to output (default), embed as base64, or skip")
    ap.add_argument("--image-dir", help="folder for saved images")
    ap.add_argument("--image-dpi", type=int, default=150)
    ap.add_argument("--ocr-lang", default="eng", help="Tesseract language(s), e.g. eng+hin")
    ap.add_argument("--password", help="password for encrypted PDFs")
    ap.add_argument("--include-hidden", action="store_true",
                    help="spreadsheets: keep hidden sheets, rows and columns")
    ap.add_argument("--charts", action="store_true",
                    help="spreadsheets: add each chart's saved numbers as a table (not in Firecrawl)")
    ap.add_argument("--chunks", action="store_true",
                    help="add the document cut into pieces for embeddings (JSON output; not in Firecrawl)")
    ap.add_argument("--chunk-size", type=int, metavar="N",
                    help="largest chunk in characters (default 2000); implies --chunks")
    ap.add_argument("--info", action="store_true", help="print detection info to stderr")
    a = ap.parse_args(argv)

    want_chunks = a.chunks or a.chunk_size is not None
    want_json = a.json or a.pages or a.blocks or bool(a.parsers) or want_chunks
    out_dir = os.path.dirname(os.path.abspath(a.output)) if a.output else os.getcwd()
    image_dir = a.image_dir
    if a.images == "save" and not image_dir:
        stem = os.path.splitext(a.output)[0] if a.output else \
            re.sub(r"\W+", "_", os.path.splitext(os.path.basename(a.input.split("?")[0]))[0])
        image_dir = stem + "_images"

    try:
        if a.parsers:
            parsers = json.loads(a.parsers)
        else:
            opts = {"type": "pdf", "mode": a.mode, "images": a.images}
            if a.max_pages:
                opts["maxPages"] = a.max_pages
            for flag, key in ((a.pages, "pages"), (a.blocks, "blocks"),
                              (a.page_markers, "pageMarkers")):
                if flag:
                    opts[key] = True
            parsers = [opts, "image"]
        resp = scrape(a.input, parsers=parsers, password=a.password, images=a.images,
                      image_dir=image_dir, link_base=out_dir, ocr_lang=a.ocr_lang,
                      page_range=a.page_range, image_dpi=a.image_dpi,
                      include_hidden=a.include_hidden, charts=a.charts,
                      chunk=({"maxChars": a.chunk_size} if a.chunk_size is not None else want_chunks) or None)
    except Exception as e:  # noqa: BLE001 -- pdfminer wraps password / corruption errors
        text = error_text(e, a.password)
        if want_json:                                     # Firecrawl-style error body
            code = getattr(e, "code", None)
            print(json.dumps({"success": False, "code": code if isinstance(code, str) else None,
                              "error": text.split(": ", 1)[-1] if isinstance(code, str) else text},
                             ensure_ascii=False, indent=2))
        sys.exit(f"error: {text}")

    d = resp["data"]
    if want_json:
        text = json.dumps(resp, ensure_ascii=False, indent=2) + "\n"
    else:
        text = d.get("markdown", "")
    if a.output:
        with open(a.output, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        sys.stdout.write(text)
    if d.get("warning"):
        print(f"warning: {d['warning']}", file=sys.stderr)
    if a.info or a.output:
        m = d.get("metadata", {})
        if "contentType" in m:
            print(f"type=spreadsheet sheets={m.get('numSheets', 1)}", file=sys.stderr)
            return
        print(f"type={m.get('pdfType')} confidence={m.get('confidence')} "
              f"pages={m.get('numPages')}/{m.get('totalPages')} "
              f"needs_ocr={m.get('pagesNeedingOcr')} time={m.get('processingTimeMs')}ms",
              file=sys.stderr)


if __name__ == "__main__":
    main()
