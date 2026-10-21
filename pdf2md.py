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
  python pdf2md.py input.pdf --images none --pages 1-5 --ocr

Library:
  from pdf2md import convert
  result = convert("input.pdf", image_dir="images")
  print(result.markdown, result.pdf_type, result.confidence)
"""
from __future__ import annotations

import argparse
import base64
import io
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

BULLETS = "•●○◦▪▫■□‣⁃∙·◆◇►▸➢➤✓✔-–—*"
BULLET_RE = re.compile(r"^\s*([•●○◦▪▫■□‣⁃∙·◆◇►▸➢➤✓✔]|[-–—*](?=\s))\s*")
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
    kind: str = "text"        # text | table | image | ocr
    payload: object = None    # table rows / image ref / ocr text
    colw: float = 0.0         # width of the column this line sits in

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


def page_stats(page) -> dict:
    """Per-page routing (detector.rs per-page sampling).
    kind: "text"   - real text layer, use native extraction
          "hybrid" - scan image plus a little native text (e.g. a typed header):
                     native text is kept and the scan is OCR'd
          "scan"   - no usable text, needs OCR
          "blank"  - nothing on the page"""
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
        txt = r["text"].replace("­", "")  # soft hyphen
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

def ruled_tables(page) -> list:
    """Rectangle / ruling-line tables (tables/detect_rects.rs, detect_lines.rs)."""
    found = []
    try:
        tables = page.find_tables(table_settings={
            "vertical_strategy": "lines", "horizontal_strategy": "lines",
            "snap_tolerance": 3, "join_tolerance": 3, "intersection_tolerance": 3,
        })
    except Exception:
        return found
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
        ox, oy = page_origin(page)
        b = t.bbox
        found.append(((b[0] - ox, b[1] - oy, b[2] - ox, b[3] - oy), rows))
    return found


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


def heuristic_tables(lines, page_width) -> list:
    """Text-alignment tables (tables/detect_heuristic.rs, simplified):
    ≥3 consecutive rows whose cell segments leave consistent vertical
    gutters, with short cells (prose in two columns is rejected)."""
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
        if best_j is None:
            i += 1
            continue
        rows_lines = lines[i:best_j + 1]
        # trim trailing single-segment rows (likely prose after the table)
        while len(rows_lines) > 3 and len(segs_of(rows_lines[-1])) < 2:
            rows_lines.pop()
        seg_words = [len(s) for ln in rows_lines for s in segs_of(ln)]
        avg_words = sum(seg_words) / max(len(seg_words), 1)
        span = rows_lines[0].words and (max(l.x1 for l in rows_lines) -
                                        min(l.x0 for l in rows_lines))
        firsts = [segments(l)[0] for l in rows_lines]
        markerish = sum(1 for f in firsts if len(f) == 1 and (
            re.fullmatch(r"[•●○◦▪▫■□‣⁃∙·◆◇►▸➢➤✓✔*\-–—]|\(?\w{1,3}[.)]?", f[0].text)
            or f[0].text == CID_MARK))
        if markerish == len(firsts) and all(len(segments(l)) == 2 for l in rows_lines):
            i = best_j + 1
            continue  # a list with hanging indents, not a table
        if avg_words > 6 or len(rows_lines) < 3:
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
    return ocr_image(img, lang, 72.0 / dpi)


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


def ocr_image(img, lang="eng", k=1.0) -> Optional[list]:
    """OCR a PIL image; bboxes are scaled by k (pixels -> points for pages)."""
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
    return out


# ───────────────────────────── 6. layout / reading order ──────────────────

def find_gutter(lines, page_width) -> Optional[float]:
    """Detect a two-column gutter: an x in the middle band that many lines
    have text on both sides of, and almost none cross."""
    text_lines = [l for l in lines if l.kind == "text"]
    if len(text_lines) < 8:
        return None
    best, best_score = None, 0
    x = page_width * 0.3
    while x <= page_width * 0.7:
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


def reading_order(items, page_width) -> list:
    """Order lines + block items (tables/images). Two-column regions are read
    left column then right column; full-width items act as separators."""
    gutter = find_gutter(items, page_width)
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
        return "-", text[m.end():]
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
            if len(ln.words) > 1 and (re.fullmatch(r"[•●○◦▪▫■□‣⁃∙·◆◇►▸➢➤✓✔*\-–—]", first)
                                      or re.fullmatch(r"\(?\d{1,3}[.)]", first)):
                rest = styled_text(ln.words[1:])   # keep inline styling
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


def load_source(src):
    """path | URL | bytes -> (bytes, display name, is_url)."""
    if isinstance(src, (bytes, bytearray)):
        return bytes(src), "document.pdf", False
    if isinstance(src, str) and re.match(r"^https?://", src):
        req = urllib.request.Request(src, headers={"User-Agent": "pdf2md/1.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
        name = os.path.basename(src.split("?")[0].rstrip("/")) or "document.pdf"
        return data, name, True
    with open(src, "rb") as f:
        return f.read(), os.path.basename(src), False


def convert(src, mode: str = "auto", max_pages: Optional[int] = None,
            page_range: Optional[str] = None, page_markers: bool = False,
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

    with pdfplumber.open(io.BytesIO(data), password=password or "") as pdf:
        n = len(pdf.pages)
        idx = parse_pages(page_range, n)
        if max_pages:
            idx = idx[:max_pages]
        stats = []
        pages_lines, heights, widths = {}, {}, {}
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
                # tables (ruled first, then heuristic on the remaining text)
                for bbox, rows in ruled_tables(page):
                    words = [w for w in words
                             if not center_inside((w.x0, w.top, w.x1, w.bottom), bbox)]
                    items.append(Line(pageno, [], bbox[0], bbox[2], bbox[1], bbox[3],
                                      "table", rows))
                lines = group_lines(words, pageno)
                for a_, b_, rows in heuristic_tables(lines, page.width):
                    tl = lines[a_:b_ + 1]
                    items.append(Line(pageno, [], min(l.x0 for l in tl), max(l.x1 for l in tl),
                                      tl[0].top, tl[-1].bottom, "table", rows))
                    for l in tl:
                        l.kind = "consumed"
                items.extend(l for l in lines if l.kind == "text")
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
            if any(l.kind == "text" for l in items):
                seq, multi = reading_order(items, widths[pageno])
                complex_layout |= multi
            elif any(l.kind == "ocr" for l in items):
                seq = items          # OCR page: Tesseract order, figures already slotted in
            else:
                seq = sorted(items, key=lambda l: (l.top, l.x0))
            blks = render_blocks(seq, base, tiers, col_width, image_md)

            if page_markers:
                emit(f"<!-- page {pageno} -->", "\n\n")
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

def _parser_opts(parsers):
    """Normalize Firecrawl `parsers` -> (pdf_options or None, image_enabled)."""
    if parsers is None:
        parsers = ["pdf", "image"]
    pdf_opts, image_on = None, False
    for p in parsers:
        ptype = p if isinstance(p, str) else (p or {}).get("type")
        if ptype == "pdf":
            pdf_opts = {} if isinstance(p, str) else dict(p)
        elif ptype == "image":
            image_on = True
    return pdf_opts, image_on


def scrape(url, formats=("markdown",), parsers=None, password=None,
           images: Optional[str] = None, image_dir: Optional[str] = None,
           link_base: Optional[str] = None, ocr_lang: str = "eng",
           page_range: Optional[str] = None, image_dpi: int = 150) -> dict:
    """Firecrawl /scrape-compatible call for PDFs and images.

    parsers: like Firecrawl — default ["pdf", "image"]; [] returns the file as
    base64. PDF options: {"type": "pdf", "mode": "fast"|"auto"|"ocr",
    "maxPages": int, "pages": bool, "blocks": bool, "pageMarkers": bool}.
    Extras (not in Firecrawl): "images": "save"|"embed"|"none" (default: "save"
    when image_dir is given, otherwise "embed" — no files are written unasked),
    page_range "1-5,8", image_dpi, ocr_lang.
    """
    if images is None:
        images = "save" if image_dir else "embed"
    data, name, is_url = load_source(url)
    pdf_opts, image_on = _parser_opts(parsers)
    meta = {"sourceURL" if is_url else "sourceFile": url if is_url else name}
    is_pdf = b"%PDF-" in data[:1024]
    warnings = [f"format '{f}' is not supported for files; ignored"
                for f in formats if f != "markdown"]

    def raw():
        return {"success": True, "data": {"base64": base64.b64encode(data).decode(),
                                          "metadata": meta}}

    if is_pdf:
        if pdf_opts is None:
            return raw()
        unknown = set(pdf_opts) - {"type", "mode", "maxPages", "pages", "blocks",
                                   "pageMarkers", "images"}
        if unknown:
            raise ValueError(f"unknown pdf parser options: {sorted(unknown)}")
        r = convert(data, mode=pdf_opts.get("mode", "auto"), max_pages=pdf_opts.get("maxPages"),
                    page_range=page_range, page_markers=bool(pdf_opts.get("pageMarkers")),
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
    else:
        if not image_on:
            return raw()
        from PIL import Image
        try:
            img = Image.open(io.BytesIO(data))
            img.load()
        except Exception:
            raise ValueError("input is neither a PDF nor a readable image")
        paras = ocr_image(flatten_alpha(img), ocr_lang) if ocr_available() else None
        if paras is None:
            warnings.append("image needs OCR but Tesseract is not available; "
                            "install tesseract-ocr and pytesseract")
            paras = []
        out = {"metadata": meta}
        if "markdown" in formats:
            out["markdown"] = "\n\n".join(p["text"] for p in paras) + ("\n" if paras else "")
    if warnings:
        out["warning"] = "; ".join(warnings)
    return {"success": True, "data": out}


def main(argv=None):
    import json
    ap = argparse.ArgumentParser(
        description="PDF to Markdown with Firecrawl-style options "
                    "(headings, paragraphs, lists, tables, images, OCR).")
    ap.add_argument("input", help="PDF or image: path or http(s) URL")
    ap.add_argument("-o", "--output", help="output file (default: stdout)")
    ap.add_argument("--mode", choices=["fast", "auto", "ocr"], default="auto",
                    help="fast: embedded text only; auto: OCR pages with no text (default); "
                         "ocr: OCR every page")
    ap.add_argument("--max-pages", type=int, help="parse at most N pages")
    ap.add_argument("--pages", action="store_true",
                    help="include per-page markdown (JSON output)")
    ap.add_argument("--blocks", action="store_true",
                    help="include typed layout blocks (JSON output)")
    ap.add_argument("--page-markers", action="store_true", help="insert <!-- page N --> markers")
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
    ap.add_argument("--info", action="store_true", help="print detection info to stderr")
    a = ap.parse_args(argv)

    want_json = a.json or a.pages or a.blocks or bool(a.parsers)
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
                      page_range=a.page_range, image_dpi=a.image_dpi)
    except Exception as e:  # pdfminer wraps password / corruption errors
        msg = repr(e) + " " + repr(getattr(e, "__cause__", ""))
        if "PDFPasswordIncorrect" in msg:
            sys.exit("error: incorrect password" if a.password else
                     "error: this PDF is encrypted — pass --password")
        sys.exit(f"error: {e or type(e).__name__}")

    d = resp["data"]
    if want_json:
        text = json.dumps(resp, ensure_ascii=False, indent=2) + "\n"
    elif "markdown" in d:
        text = d["markdown"]
    else:
        text = d.get("base64", "")
    if a.output:
        with open(a.output, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        sys.stdout.write(text)
    if d.get("warning"):
        print(f"warning: {d['warning']}", file=sys.stderr)
    if a.info or a.output:
        m = d.get("metadata", {})
        print(f"type={m.get('pdfType')} confidence={m.get('confidence')} "
              f"pages={m.get('numPages')}/{m.get('totalPages')} "
              f"needs_ocr={m.get('pagesNeedingOcr')} time={m.get('processingTimeMs')}ms",
              file=sys.stderr)


if __name__ == "__main__":
    main()
