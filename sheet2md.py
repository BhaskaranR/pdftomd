#!/usr/bin/env python3
"""Spreadsheet (xlsx, xlsm, csv) -> Markdown, matching Firecrawl's output.

A port of the sheet and CSV readers in Firecrawl's MIT-licensed `anydoc` crate
(https://github.com/firecrawl/anydoc: src/formats/sheet, src/formats/csv.rs,
src/shared/header.rs, src/render/markdown), which Firecrawl's document engine uses.

Rules (same as Firecrawl):
- every visible sheet in workbook order; `## <name>` before each table only when
  more than one sheet is visible; a sheet with no visible cells is skipped
- one table per sheet over its populated area; hidden rows/columns dropped;
  a merged cell keeps its value top-left and the cells it covers are blank
- saved formula results; number formats applied; dates ISO (2026-03-15)
- the first row is a header only when it reads as labels over typed columns,
  otherwise the table gets an empty header row

Additions: `include_hidden` keeps hidden sheets/rows/columns, formula cells with no
saved value are reported in `Result.warning`, and `charts` (off by default, so the output
stays Firecrawl's) prints each chart's saved numbers as a table under its sheet.
"""
import argparse
import bisect
import codecs
import csv
import io
import math
import posixpath
import sys
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from decimal import Decimal
from fractions import Fraction
from typing import Optional

# ---------------------------------------------------------------- limits (anydoc package/limits.rs)
MAX_ENTRY_BYTES = 128 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_ENTRY_COUNT = 100_000
MAX_GRID_SLOTS = 4_000_000
MAX_EXPANSION = 4_000_000
MAX_ROWS = 1_048_576
MAX_COLS = 16_384

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
SML_NS = ("http://schemas.openxmlformats.org/spreadsheetml/2006/main",
          "http://purl.oclc.org/ooxml/spreadsheetml/main")
REL_NS = ("http://schemas.openxmlformats.org/officeDocument/2006/relationships",
          "http://purl.oclc.org/ooxml/officeDocument/relationships")
VML_NS = "urn:schemas-microsoft-com:vml"
CHART_NS = ("http://schemas.openxmlformats.org/drawingml/2006/chart",
            "http://purl.oclc.org/ooxml/drawingml/chart")
DML_NS = ("http://schemas.openxmlformats.org/drawingml/2006/main",
          "http://purl.oclc.org/ooxml/drawingml/main")
XDR_NS = ("http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
          "http://purl.oclc.org/ooxml/drawingml/spreadsheetDrawing")
X_VML_NS = "urn:schemas-microsoft-com:office:excel"


class SheetError(Exception):
    """code: SPREADSHEET_TOO_LARGE | SPREADSHEET_ENCRYPTED | SPREADSHEET_UNREADABLE |
    SPREADSHEET_UNSUPPORTED."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class Result:
    def __init__(self, markdown, sheets, warning=None):
        self.markdown = markdown
        self.sheets = sheets          # names of the sheets read, in order
        self.warning = warning


# ---------------------------------------------------------------- text
def rust_round(x: float) -> float:
    """f64::round: half away from zero (Python's round() is half-even)."""
    return math.copysign(math.floor(abs(x) + 0.5), x)


def clean_text(text: str) -> str:
    """NBSP -> space, soft hyphen / ZWSP / BOM dropped, line breaks -> one space,
    other control characters dropped; ZWNJ/ZWJ kept."""
    out = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "\xa0":
            out.append(" ")
        elif c in "\xad​﻿":
            pass
        elif c == "\t":
            out.append("\t")
        elif c == "\r":
            if i + 1 < n and text[i + 1] == "\n":
                i += 1
            out.append(" ")
        elif c == "\n":
            out.append(" ")
        elif unicodedata.category(c) == "Cc":
            pass
        else:
            out.append(c)
        i += 1
    return "".join(out)


def collapse_ws(text: str) -> str:
    out, prev = [], False
    for c in text:
        if c.isspace():
            if not prev:
                out.append(" ")
            prev = True
        else:
            out.append(c)
            prev = False
    return "".join(out)


# ---------------------------------------------------------------- numbers (sheet/mod.rs)
def rust_display(f: float) -> str:
    """f64 Display: shortest round-trip digits, never an exponent."""
    if math.isnan(f):
        return "NaN"
    if math.isinf(f):
        return "inf" if f > 0 else "-inf"
    s = format(Decimal(repr(f)), "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def format_float(f: float) -> str:
    """At the 15 significant digits a spreadsheet stores and shows."""
    if not math.isfinite(f):
        return rust_display(f)
    return rust_display(float(f"{f:.14e}"))


def format_time_of_day(days: float) -> str:
    total = int(rust_round(abs(days) * 86_400.0)) % 86_400
    return f"{total // 3600:02}:{total % 3600 // 60:02}:{total % 60:02}"


def format_duration_days(days: float) -> str:
    sign = "-" if days < 0 else ""
    total = int(rust_round(abs(days) * 86_400.0))
    return f"{sign}{total // 3600}:{total % 3600 // 60:02}:{total % 60:02}"


# ---------------------------------------------------------------- number formats (sheet/numfmt.rs)
BUILTIN = {
    1: "0", 2: "0.00", 3: "#,##0", 4: "#,##0.00", 9: "0%", 10: "0.00%", 11: "0.00E+00",
    12: "# ?/?", 13: "# ??/??", 14: "mm-dd-yy", 15: "d-mmm-yy", 16: "d-mmm", 17: "mmm-yy",
    18: "h:mm AM/PM", 19: "h:mm:ss AM/PM", 20: "h:mm", 21: "h:mm:ss", 22: "m/d/yy h:mm",
    37: "#,##0 ;(#,##0)", 38: "#,##0 ;[Red](#,##0)", 39: "#,##0.00;(#,##0.00)",
    40: "#,##0.00;[Red](#,##0.00)", 45: "mm:ss", 46: "[h]:mm:ss", 47: "mmss.0",
    48: "##0.0E+0", 49: "@",
}
COLORS = ("black", "blue", "cyan", "green", "magenta", "red", "white", "yellow")

# Tokens are tuples: ("digit", place, region) ("dec",) ("pct",) ("lit", s) ("bare", s)
# ("comma",) ("exp", plus) ("slash",) ("at",) ("skip",). Regions: int frac exp num den.


def _push_literal(toks, c):
    if toks and toks[-1][0] == "lit":
        toks[-1] = ("lit", toks[-1][1] + c)
    else:
        toks.append(("lit", c))


def _decoration(toks) -> str:
    return "".join(t[1] if t[0] == "lit" else " " for t in toks)


def _split_sections(code):
    parts = [""]
    i, n = 0, len(code)
    while i < n:
        c = code[i]
        if c == ";":
            parts.append("")
            i += 1
        elif c in '"[':
            close = '"' if c == '"' else "]"
            j = code.find(close, i + 1)
            if j < 0:
                return None
            parts[-1] += code[i:j + 1]
            i = j + 1
        elif c in "\\_*":
            if i + 1 >= n:
                return None
            parts[-1] += code[i:i + 2]
            i += 2
        else:
            parts[-1] += c
            i += 1
    return parts


def _date_parts(runs, elapsed):
    date = time = False
    for i, run in enumerate(runs):
        if run in "yd":
            date = True
        elif run in "hsa":
            time = True
        elif run == "m":
            if (i > 0 and runs[i - 1] == "h") or (i + 1 < len(runs) and runs[i + 1] == "s"):
                time = True
            else:
                date = True
    if elapsed or (not date and not time):
        date, time = False, True
    return (date, time, elapsed)


class _Spec:
    def __init__(self):
        self.toks = []
        self.grouping = False
        self.scale = 0
        self.percents = 0
        self.int_places = self.frac_places = self.num_places = self.den_places = 0
        self.exp = False
        self.fixed_den = None


def _bracket(inner, raw, st):
    if not inner:
        return False
    c = inner[0]
    if c in "<>=":
        if st["cond"] is not None:
            return False
        for prefix, op in ((">=", "ge"), ("<=", "le"), ("<>", "ne"), (">", "gt"), ("<", "lt"), ("=", "eq")):
            if inner.startswith(prefix):
                rest = inner[len(prefix):]
                break
        try:
            st["cond"] = (op, float(rest.strip()))
        except ValueError:
            return False
    elif c == "$":
        sym = inner[1:].split("-")[0]
        if sym:
            raw.append(("lit", sym))
    elif c in "hHmMsS" and all(x.lower() == c.lower() for x in inner):
        st["date"] = True
        st["elapsed"] = True
        st["runs"].append(c.lower())
    else:
        lower = inner.lower()
        is_color = lower in COLORS
        if not is_color and lower.startswith("color"):
            try:
                is_color = 1 <= int(lower[5:].strip()) <= 56
            except ValueError:
                is_color = False
        if not is_color:
            return False
    return True


def _parse_section(s):
    chars = s
    i, n = 0, len(s)
    raw = []
    st = {"cond": None, "date": False, "elapsed": False, "runs": []}
    general_at, has_general = 0, False
    while i < n:
        c = chars[i]
        if c == "[":
            end = s.find("]", i)
            if end < 0:
                return None
            inner = s[i + 1:end]
            i = end + 1
            if not _bracket(inner, raw, st):
                return None
        elif c == '"':
            end = s.find('"', i + 1)
            if end < 0:
                return None
            for ch in s[i + 1:end]:
                _push_literal(raw, ch)
            i = end + 1
        elif c == "\\":
            if i + 1 >= n:
                return None
            _push_literal(raw, s[i + 1])
            i += 2
        elif c == "_":
            if i + 1 >= n:
                return None
            raw.append(("skip",))
            i += 2
        elif c == "*":
            if i + 1 >= n:
                return None
            i += 2
        elif c in "0#?":
            raw.append(("digit", c, "int"))
            i += 1
        elif c == ".":
            raw.append(("dec",))
            i += 1
        elif c == ",":
            raw.append(("comma",))
            i += 1
        elif c == "%":
            raw.append(("pct",))
            i += 1
        elif c == "@":
            raw.append(("at",))
            i += 1
        elif c in "Ee" and i + 1 < n and s[i + 1] in "+-":
            raw.append(("exp", s[i + 1] == "+"))
            i += 2
        elif c in "yYdDhHsSmM":
            st["date"] = True
            st["runs"].append(c.lower())
            while i < n and s[i].lower() == c.lower():
                i += 1
        elif c in "gG":
            if s[i:i + 7].lower() != "general":
                return None
            has_general = True
            general_at = len(_decoration(raw))
            i += 7
        elif c in "aA":
            for t in ("AM/PM", "A/P"):
                if s[i:i + len(t)].upper() == t:
                    break
            else:
                return None
            st["date"] = True
            st["runs"].append("a")
            i += len(t)
        elif "1" <= c <= "9":
            j = i
            while j < n and s[j].isascii() and s[j].isdigit():
                j += 1
            raw.append(("bare", s[i:j]))
            i = j
        elif c in "$-+(): ":
            _push_literal(raw, c)
            i += 1
        elif c == "/":
            raw.append(("slash",))
            i += 1
        else:
            return None
    if has_general:
        if any(t[0] not in ("lit", "skip") for t in raw):
            return None
        text = _decoration(raw)
        body = ("general", text[:general_at], text[general_at:])
    elif st["date"]:
        if any(t[0] in ("at", "exp", "bare") for t in raw):
            return None
        body = ("datetime", _date_parts(st["runs"], st["elapsed"]))
    elif any(t[0] == "at" for t in raw):
        if any(t[0] in ("digit", "dec", "exp", "slash", "bare") for t in raw):
            return None
        toks = [("lit", "%") if t[0] == "pct" else ("lit", ",") if t[0] == "comma" else t for t in raw]
        body = ("text", toks)
    else:
        spec = _resolve_number(raw)
        if spec is None:
            return None
        body = ("number", spec)
    return (st["cond"], body)


def _resolve_number(raw):
    spec = _Spec()
    toks = []
    region = "int"
    k = 0
    while k < len(raw):
        tok = raw[k]
        k += 1
        kind = tok[0]
        if kind == "digit":
            if spec.fixed_den is not None and region == "den":
                return None
            toks.append(("digit", tok[1], region))
        elif kind == "dec":
            if region != "int":
                return None
            region = "frac"
            toks.append(tok)
        elif kind == "exp":
            if spec.exp or region in ("num", "den"):
                return None
            spec.exp = True
            region = "exp"
            toks.append(tok)
        elif kind == "slash":
            run = 0
            for t in reversed(toks):
                if t[0] == "digit" and t[2] == "int":
                    run += 1
                else:
                    break
            if run == 0 or region != "int" or spec.fixed_den is not None:
                _push_literal(toks, "/")
                continue
            for j in range(len(toks) - run, len(toks)):
                toks[j] = ("digit", toks[j][1], "num")
            toks.append(tok)
            region = "den"
            if k < len(raw) and raw[k][0] == "bare":
                d = raw[k][1]
                k += 1
                spec.fixed_den = int(d)
                toks.append(("lit", d))
        elif kind == "pct":
            spec.percents += 1
            toks.append(tok)
        elif kind == "bare":
            return None
        else:
            toks.append(tok)
    digit_at = [t[0] == "digit" for t in toks]
    first = digit_at.index(True) if True in digit_at else None
    last = len(digit_at) - 1 - digit_at[::-1].index(True) if True in digit_at else None
    out = []
    for i, tok in enumerate(toks):
        if tok[0] != "comma":
            out.append(tok)
            continue
        if first is not None and i > last:
            spec.scale += 1
        elif first is not None and first < i < last:
            spec.grouping = True
        else:
            _push_literal(out, ",")
    for t in out:
        if t[0] == "digit":
            r = t[2]
            if r == "int":
                spec.int_places += 1
            elif r == "frac":
                spec.frac_places += 1
            elif r == "num":
                spec.num_places += 1
            elif r == "den":
                spec.den_places += 1
    if spec.exp and not any(t[0] == "digit" and t[2] == "exp" for t in out):
        return None
    if spec.num_places > 0 and spec.den_places == 0 and spec.fixed_den is None:
        return None
    spec.toks = out
    return spec


def _cond_matches(cond, v):
    op, x = cond
    return {"lt": v < x, "le": v <= x, "gt": v > x, "ge": v >= x, "eq": v == x, "ne": v != x}[op]


def _select(sections, v):
    if not sections:
        return None
    if any(s[0] is not None for s in sections):
        for s in sections:
            if s[0] is None or _cond_matches(s[0], v):
                return s, v, True
        return None
    if len(sections) == 1:
        idx = 0
    elif len(sections) == 2:
        idx = 0 if v >= 0 else 1
    else:
        idx = 0 if v > 0 else 1 if v < 0 else 2
    neg = idx == 1
    return sections[idx], (abs(v) if neg else v), not neg


def _split_digits(v, dp):
    """Digits of round(v * 10^dp) on the 15-significant-digit form, half away from zero:
    (integer part, empty when zero; exactly dp fractional digits)."""
    if not math.isfinite(v) or v < 0 or dp > 512:
        return None
    if v == 0:
        return "", "0" * dp
    mantissa, e = f"{v:.14e}".split("e")
    e = int(e)
    digits = "".join(ch for ch in mantissa if ch.isdigit())
    shift = e - 14 + dp
    if shift >= 0:
        scaled = digits + "0" * shift
    else:
        drop = -shift
        if drop > len(digits):
            scaled = ""
        else:
            kept, rest = digits[:len(digits) - drop], digits[len(digits) - drop:]
            if rest and rest[0] >= "5":
                kept = str(int(kept or "0") + 1).zfill(len(kept)) if kept else "1"
            scaled = kept
    s = scaled.lstrip("0")
    if len(s) < dp:
        s = "0" * (dp - len(s)) + s
    return s[:len(s) - dp], s[len(s) - dp:]


def _places(toks, region):
    return [t[1] for t in toks if t[0] == "digit" and t[2] == region]


def _assign(digits, places):
    k = len(places)
    if k == 0:
        return []
    n = len(digits)
    if n > k:
        return [digits[:n - k + 1]] + list(digits[n - k + 1:])
    out = [{"0": "0", "?": " "}.get(p, "") for p in places[:k - n]]
    return out + list(digits)


def _emit(spec, int_digits, frac_digits, exp_digits, exp_sign):
    int_assigned = _assign(int_digits, _places(spec.toks, "int"))
    exp_assigned = _assign(exp_digits, _places(spec.toks, "exp"))
    total_int = sum(sum(ch.isdigit() for ch in s) for s in int_assigned)
    frac_places = _places(spec.toks, "frac")
    min_frac = max((i + 1 for i, p in enumerate(frac_places) if p == "0"), default=0)
    keep_frac = max(len(frac_digits.rstrip("0")), min_frac)
    show_decimal = keep_frac > 0 or "?" in frac_places or not frac_places
    out = []
    int_i = frac_i = exp_i = 0
    remaining = total_int
    for tok in spec.toks:
        kind = tok[0]
        if kind == "digit":
            region = tok[2]
            if region == "int":
                for ch in int_assigned[int_i]:
                    out.append(ch)
                    if ch.isdigit():
                        remaining -= 1
                        if spec.grouping and remaining > 0 and remaining % 3 == 0:
                            out.append(",")
                int_i += 1
            elif region == "frac":
                if frac_i < keep_frac:
                    out.append(frac_digits[frac_i])
                elif tok[1] == "?":
                    out.append(" ")
                frac_i += 1
            elif region == "exp":
                out.append(exp_assigned[exp_i])
                exp_i += 1
        elif kind == "dec":
            if show_decimal:
                out.append(".")
        elif kind == "pct":
            out.append("%")
        elif kind == "lit":
            out.append(tok[1])
        elif kind == "exp":
            out.append("E")
            if exp_sign < 0:
                out.append("-")
            elif tok[1]:
                out.append("+")
        elif kind == "skip":
            out.append(" ")
    return "".join(out)


def _render_scientific(spec, v):
    n_int = max(spec.int_places, 1)
    if v == 0:
        i, f = _split_digits(0.0, spec.frac_places)
        e = 0
    else:
        e = Decimal(repr(v)).adjusted()
        e = (e // n_int) * n_int
        r = _split_digits(v / 10.0 ** e, spec.frac_places)
        if r is None:
            return None
        i, f = r
        if len(i) > n_int:
            e += n_int
            r = _split_digits(v / 10.0 ** e, spec.frac_places)
            if r is None:
                return None
            i, f = r
    bare = str(abs(e))
    pad = max(len(_places(spec.toks, "exp")) - len(bare), 0)
    return _emit(spec, i, f, "0" * pad + bare, -1 if e < 0 else 1)


def _best_fraction(x, fixed, den_places):
    if x < 0 or not math.isfinite(x):
        return None
    if fixed is not None:
        n = rust_round(x * fixed)
        return (int(n), fixed) if n < 1e18 else None
    max_den = 10 ** den_places - 1 if den_places <= 19 else 2 ** 64 - 1
    fr = Fraction(x).limit_denominator(max(max_den, 1))
    return fr.numerator, fr.denominator


def _render_fraction(spec, v):
    if v >= 1e15:
        return None
    has_int = spec.int_places > 0
    whole, target = (math.trunc(v), v - math.trunc(v)) if has_int else (0, v)
    r = _best_fraction(target, spec.fixed_den, spec.den_places)
    if r is None:
        return None
    num, den = r
    if has_int and num == den and den > 0:
        whole += 1
        num = 0
    if whole == 0:
        int_digits = "0" if num == 0 else ""
    else:
        int_digits = _split_digits(float(whole), 0)[0]
    hide = has_int and num == 0
    ia = iter(_assign(int_digits, _places(spec.toks, "int")))
    na = iter(_assign(str(num), _places(spec.toks, "num")))
    da = iter(_assign(str(den), _places(spec.toks, "den")))
    out = []
    for tok in spec.toks:
        kind = tok[0]
        if kind == "digit":
            if tok[2] == "int":
                out.append(next(ia, ""))
            elif tok[2] == "num" and not hide:
                out.append(next(na, ""))
            elif tok[2] == "den" and not hide:
                out.append(next(da, ""))
        elif kind == "slash":
            if not hide:
                out.append("/")
        elif kind == "lit":
            if not (hide and spec.fixed_den is not None and str(spec.fixed_den) == tok[1]):
                out.append(tok[1])
        elif kind == "pct":
            out.append("%")
        elif kind == "skip":
            out.append(" ")
    return "".join(out).rstrip()


def _render_number(spec, v_abs, minus):
    v = v_abs
    for _ in range(spec.percents):
        v *= 100.0
    for _ in range(spec.scale):
        v /= 1000.0
    if not math.isfinite(v):
        return None
    if spec.exp:
        body = _render_scientific(spec, v)
    elif spec.num_places > 0:
        body = _render_fraction(spec, v)
    else:
        r = _split_digits(v, spec.frac_places)
        body = None if r is None else _emit(spec, r[0], r[1], "", 0)
    if body is None:
        return None
    return "-" + body if minus else body


class NumberFormat:
    """A parsed format code. format_number returns ("general", value, prefix, suffix),
    ("datetime", (date, time, elapsed)) or ("text", s)."""

    def __init__(self, sections):
        self.sections = sections

    @classmethod
    def parse(cls, code: str) -> Optional["NumberFormat"]:
        if not code:
            return None
        parts = _split_sections(code)
        if parts is None or not parts or len(parts) > 4:
            return None
        sections = [_parse_section(p) for p in parts]
        if any(s is None for s in sections):
            return None
        if sum(s[0] is not None for s in sections) > 2:
            return None
        last = len(sections) - 1
        for i, (cond, body) in enumerate(sections):
            is_text = body[0] == "text"
            if is_text and (i != last or cond is not None):
                return None
            empty = body[0] == "number" and not body[1].toks
            if len(sections) == 4 and i == 3 and not is_text and not empty:
                return None
        return cls(sections)

    def _numeric(self):
        if self.sections[-1][1][0] == "text":
            return self.sections[:-1]
        if len(self.sections) == 4:
            return self.sections[:3]
        return self.sections

    def format_number(self, v: float):
        if not math.isfinite(v):
            return ("general", v, "", "")
        picked = _select(self._numeric(), v)
        if picked is None:
            return ("general", v, "", "")
        (_, body), value, auto_minus = picked
        if body[0] == "general":
            return ("general", value, body[1], body[2])
        if body[0] == "datetime":
            return ("datetime", body[1])
        if body[0] == "number":
            s = _render_number(body[1], abs(value), auto_minus and value < 0)
            return ("text", s) if s is not None else ("general", v, "", "")
        return ("general", v, "", "")

    def format_text(self, text: str) -> Optional[str]:
        if self.sections[-1][1][0] == "text":
            body = self.sections[-1][1]
        elif len(self.sections) == 4:
            body = self.sections[3][1]
        else:
            return None
        if body[0] != "text":
            return ""
        out = []
        for t in body[1]:
            if t[0] == "lit":
                out.append(t[1])
            elif t[0] == "skip":
                out.append(" ")
            elif t[0] == "at":
                out.append(text)
        return "".join(out)


# ---------------------------------------------------------------- dates (sheet/xlsx.rs)
def _days_from_civil(y, m, d):
    y -= m <= 2
    era = y // 400
    yoe = y - era * 400
    mp = (m + 9) % 12
    doy = (153 * mp + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146_097 + doe - 719_468


def _civil_from_days(z):
    z += 719_468
    era = z // 146_097
    doe = z - era * 146_097
    yoe = (doe - doe // 1460 + doe // 36_524 - doe // 146_096) // 365
    y = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    m = mp + 3 if mp < 10 else mp - 9
    return y + (m <= 2), m, d


def render_serial(serial: float, parts, date1904: bool) -> str:
    """Elapsed formats as a duration, sub-day serials as a time of day, everything
    else as an ISO date with a midnight time omitted."""
    date, time, elapsed = parts
    if not math.isfinite(serial):
        return format_float(serial)
    if elapsed:
        return format_duration_days(serial)
    if not date:
        return format_time_of_day(math.fmod(serial, 1.0))
    if abs(serial) < 1.0:
        return format_time_of_day(serial) if time else format_float(serial)
    if not 0.0 <= serial < 2_958_466.0:
        return format_float(serial)
    days = math.trunc(serial)
    if not date1904 and days == 60:
        return format_float(serial)
    secs = int(rust_round(math.fmod(serial, 1.0) * 86_400.0))
    if secs >= 86_400:
        secs = 0
        days += 1
    if date1904:
        civil = days + _days_from_civil(1904, 1, 1)
    else:
        civil = days - (days >= 60) + _days_from_civil(1899, 12, 31)
    y, m, d = _civil_from_days(civil)
    if not 1 <= y <= 9999:
        return format_float(serial)
    out = f"{y:04}-{m:02}-{d:02}"
    if time and secs != 0:
        out += f" {secs // 3600:02}:{secs % 3600 // 60:02}:{secs % 60:02}"
    return out


def render_number(fmt, n: float, date1904: bool) -> str:
    if fmt is None:
        text = format_float(n)
    else:
        r = fmt.format_number(n)
        if r[0] == "general":
            text = f"{r[2]}{format_float(r[1])}{r[3]}"
        elif r[0] == "text":
            text = r[1]
        else:
            text = render_serial(n, r[1], date1904)
    return clean_text(text)


def format_as_text(fmt, text: str) -> str:
    if fmt is None:
        return text
    s = fmt.format_text(text)
    return text if s is None else clean_text(s)


# ---------------------------------------------------------------- header guess (shared/header.rs)
SAMPLE_ROWS = 50
MAX_LABEL = 64


def _is_number(value):
    if value.endswith("%"):
        value = value[:-1]
    cleaned = "".join(c for c in value if c not in ", _\xa0")
    if not any(c.isascii() and c.isdigit() for c in cleaned):
        return False
    try:
        float(cleaned)
    except ValueError:
        return False
    return True


def _digit_groups(value, seps):
    sep = next((s for s in seps if s in value), None)
    if sep is None:
        return 0
    parts = value.split(sep)
    ok = all(1 <= len(p) <= 4 and all(c in "0123456789" for c in p) for p in parts)
    return len(parts) if ok else 0


def _is_temporal(value):
    cut = min((i for i in (value.find("T"), value.find(" ")) if i >= 0), default=-1)
    date, time = (value[:cut], value[cut + 1:]) if cut >= 0 else (value, "")
    time = time.rstrip("Z").split(".")[0]

    def is_clock(t):
        return _digit_groups(t, ":") in (2, 3)
    if _digit_groups(date, "-/.") == 3:
        return time == "" or is_clock(time)
    return time == "" and is_clock(date)


def classify(value: str) -> Optional[str]:
    value = value.strip()
    if not value:
        return None
    if _is_number(value):
        return "number"
    if value.lower() in ("true", "false", "yes", "no"):
        return "bool"
    if _is_temporal(value):
        return "date"
    return "text"


def _dominant(values):
    for kind in ("number", "bool", "date", "text"):
        if sum(classify(v) == kind for v in values) * 10 >= len(values) * 9:
            return kind
    return None


def _plain(slot) -> str:
    """Text of a grid slot: covered positions read as empty."""
    if slot is None:
        return ""
    return "".join(p[1] if p[0] == "text" else ("[x]" if p[1] else "[ ]") for p in slot)


def header_rows(grid, spans=None) -> int:
    """1 when the first row labels the columns, else 0. grid: rows of slots, a slot is a
    list of ("text", s) / ("box", checked) parts or None (covered); spans: set of first-row
    columns that start or sit inside a merge."""
    if len(grid) < 2:
        return 0
    if spans or any(s is None for s in grid[0]):
        return 0
    sample = grid[1:SAMPLE_ROWS + 1]
    tally = {}
    for row in sample:
        tally[len(row)] = tally.get(len(row), 0) + 1
    modal = max(tally.items(), key=lambda kv: (kv[1], kv[0]))[0] if tally else 0
    if len(grid[0]) != modal:
        return 0
    head = [_plain(s) for s in grid[0]]
    body = [[_plain(s) for s in row] for row in sample]

    def content_width(row):
        for i in range(len(row) - 1, -1, -1):
            if row[i].strip():
                return i + 1
        return 0
    width = max(content_width(r) for r in [head] + body)
    if width == 0 or width > len(head):
        return 0
    seen = set()
    for c, value in enumerate(head[:width]):
        if not value.strip():
            if c == 0:
                continue
            return 0
        if "\n" in value:
            return 0
        if value.strip().lower() in seen:
            return 0
        seen.add(value.strip().lower())
    # anydoc 0.2.4, the release Firecrawl runs: a first-row value that recurs below is a data
    # vote like any other (later anydoc counts it separately, as weaker evidence).
    header_votes = data_votes = 0
    for c, label in enumerate(head[:width]):
        values = [row[c].strip() for row in body if c < len(row) and row[c].strip()]
        if not values:
            continue
        label = label.strip()
        kind = _dominant(values)
        if kind is not None and kind != "text":
            if classify(label) == "text":
                header_votes += 1
            else:
                data_votes += 1
        elif any(v.lower() == label.lower() for v in values):
            data_votes += 1
    if header_votes == 0 and data_votes == 0:
        return int(all(len(v) <= MAX_LABEL for v in head[:width]))
    return int(header_votes > data_votes)


# ---------------------------------------------------------------- markdown (render/markdown)
def _run_end(chars, j):
    end = j + 1
    while end < len(chars) and chars[end] == chars[j]:
        end += 1
    return end


DELIM_SLOT = {"*": 0, "_": 1, "~": 2, "`": 3, "]": 4, "$": 5}
ASCII_PUNCT = set("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")


def _can_close(chars, j, end):
    prev = chars[j - 1] if j > 0 else None
    nxt = chars[end] if end < len(chars) else None
    if prev is not None and prev.isspace():
        return False
    if prev is not None and prev in ASCII_PUNCT and nxt is not None and nxt.isalnum():
        return False
    return chars[j] != "_" or not (prev is not None and prev.isalnum() and nxt is not None and nxt.isalnum())


def _can_close_math(chars, j, end):
    prev = chars[j - 1] if j > 0 else None
    nxt = chars[end] if end < len(chars) else None
    return not (prev is not None and prev.isspace()) and not (nxt is not None and nxt.isascii() and nxt.isdigit())


def _partner_slot(chars, j, end):
    slot = DELIM_SLOT.get(chars[j])
    if slot is None:
        return None
    c = chars[j]
    closes = True if c in "`]" else _can_close_math(chars, j, end) if c == "$" else _can_close(chars, j, end)
    return slot if closes else None


def _closers(text):
    found = set()
    chars = list(text)
    j = 0
    while j < len(chars):
        end = _run_end(chars, j)
        slot = _partner_slot(chars, j, end)
        if slot is not None:
            found.add(slot)
        j = end
    return found


def _entity_ahead(chars, i):
    k = i + 1
    if k < len(chars) and chars[k] == "#":
        return True
    seen = 0
    while k < len(chars) and chars[k].isascii() and chars[k].isalnum():
        k += 1
        seen += 1
    return seen > 0 and k < len(chars) and chars[k] == ";"


def escape_text(text, cell, trailing_nonspace=False, trailing_delims=frozenset()):
    """anydoc's escape_text for table cells (cell=True) and headings: escape only what
    could parse as Markdown there."""
    chars = list(text)
    last = [None] * 6
    j = 0
    while j < len(chars):
        end = _run_end(chars, j)
        slot = _partner_slot(chars, j, end)
        if slot is not None:
            last[slot] = end - 1
        j = end
    out = []
    i = 0
    while i < len(chars):
        c = chars[i]
        nxt = chars[i + 1] if i + 1 < len(chars) else None
        next_nonspace = trailing_nonspace if nxt is None else not nxt.isspace()

        def paired(slot):
            return slot in trailing_delims or (last[slot] is not None and last[slot] > i)
        if c == "\\":
            esc = True
        elif c == "$":
            esc = next_nonspace and paired(5)
        elif c == "`":
            esc = paired(3)
        elif c == "*":
            esc = next_nonspace and paired(0)
        elif c == "_":
            prev_alnum = i > 0 and chars[i - 1].isalnum()
            next_alnum = nxt is not None and nxt.isalnum()
            esc = next_nonspace and not (prev_alnum and next_alnum) and paired(1)
        elif c == "~":
            esc = next_nonspace and paired(2)
        elif c == "[":
            esc = paired(4)
        elif c == "<":
            esc = nxt is not None and ((nxt.isascii() and nxt.isalpha()) or nxt in "/!?")
        elif c == "|":
            esc = cell
        elif c == "&" and _entity_ahead(chars, i):
            out.append("&amp;")
            i += 1
            continue
        else:
            esc = False
        if esc:
            out.append("\\")
        out.append(c)
        i += 1
    return "".join(out)


def render_parts(parts, cell=True) -> str:
    """Inline parts -> text: adjacent text runs merge; a checkbox is [x]/[ ] and stands
    apart from a caption that follows it."""
    runs = []
    for p in parts:
        if p[0] == "text":
            if not p[1]:
                continue
            if runs and runs[-1][0] == "text":
                runs[-1] = ("text", runs[-1][1] + p[1])
            else:
                runs.append(p)
        else:
            runs.append(p)
    suffix = [set() for _ in range(len(runs) + 1)]
    for k in range(len(runs) - 1, -1, -1):
        suffix[k] = set(suffix[k + 1])
        if runs[k][0] == "text":
            suffix[k] |= _closers(runs[k][1])
    out = []
    for k, run in enumerate(runs):
        nxt = runs[k + 1] if k + 1 < len(runs) else None
        if run[0] == "text":
            out.append(escape_text(run[1], cell, trailing_nonspace=nxt is not None and nxt[0] == "box",
                                   trailing_delims=suffix[k + 1]))
        else:
            out.append("[x]" if run[1] else "[ ]")
            if nxt is not None and not (nxt[0] == "text" and nxt[1][:1].isspace()):
                out.append(" ")
    return "".join(out)


def render_cell(parts) -> str:
    text = render_parts(parts)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return "<br>".join(lines)


def _is_empty(parts) -> bool:
    return all(p[0] == "text" and not p[1].strip() for p in parts)


def render_table(grid, n_header) -> Optional[str]:
    """grid: rows of slots (parts list, or None for a merge-covered position)."""
    if not grid:
        return None
    width = max(len(r) for r in grid)
    rendered = []
    for row in grid:
        cells = [("", True) if s is None else (render_cell(s), False) for s in row]
        cells += [("", False)] * (width - len(cells))
        rendered.append(cells)
    while len(rendered) > 1 and all(t == "" and not cov for t, cov in rendered[-1]):
        rendered.pop()
    width = 0
    for row in rendered:
        for i in range(len(row) - 1, -1, -1):
            if row[i][0] or row[i][1]:
                width = max(width, i + 1)
                break
    if width == 0:
        return None
    rendered = [r[:width] for r in rendered]
    if n_header >= 1:
        header = [t for t, _ in rendered.pop(0)]
    else:
        header = [""] * width

    def fmt_row(cells):
        return "|" + "".join(f" {c} |" for c in cells)
    lines = [fmt_row(header), fmt_row(["---"] * width)]
    lines += [fmt_row([t for t, _ in r]) for r in rendered]
    return "\n".join(lines)


def document(blocks) -> str:
    """blocks: ("heading", name) / ("table", grid, n_header) / ("raw", markdown)."""
    parts = []
    for b in blocks:
        if b[0] == "raw":
            parts.append(b[1])
        elif b[0] == "heading":
            text = render_parts([("text", b[1])], cell=False).strip()
            if text:
                parts.append(f"## {text}")
        else:
            t = render_table(b[1], b[2])
            if t is not None:
                parts.append(t)
    out = "\n\n".join(parts)
    return out + "\n" if out else out


# ---------------------------------------------------------------- package (zip + xml)
def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _ns(tag):
    return tag[1:].split("}", 1)[0] if tag.startswith("{") else ""


def _is(el, name, nss=SML_NS):
    return _local(el.tag) == name and _ns(el.tag) in nss


def _children(el, name, nss=SML_NS):
    return [c for c in el if _is(c, name, nss)]


def _child(el, name, nss=SML_NS):
    for c in el:
        if _is(c, name, nss):
            return c
    return None


def _descendants(el, name, nss=SML_NS):
    return [c for c in el.iter() if _is(c, name, nss)]


def _attr(el, name):
    """Unqualified attribute."""
    return el.attrib.get(name)


def _rel_id(el):
    for k, v in el.attrib.items():
        if _local(k) == "id" and _ns(k) in REL_NS:
            return v
    return None


def _text(el) -> str:
    return "".join(el.itertext())


class Package:
    def __init__(self, data: bytes):
        try:
            self.zip = zipfile.ZipFile(io.BytesIO(data))
            infos = self.zip.infolist()
        except (zipfile.BadZipFile, ValueError, OSError):
            raise SheetError("SPREADSHEET_UNREADABLE", "not a readable workbook container")
        if len(infos) > MAX_ENTRY_COUNT:
            raise SheetError("SPREADSHEET_TOO_LARGE", f"archive holds {len(infos)} entries")
        self.names = {i.filename.lstrip("/"): i for i in infos}
        self.budget = MAX_TOTAL_BYTES

    def has(self, name):
        return name in self.names

    def part(self, name) -> Optional[bytes]:
        info = self.names.get(name)
        if info is None:
            return None
        if info.file_size > MAX_ENTRY_BYTES or info.file_size > self.budget:
            raise SheetError("SPREADSHEET_TOO_LARGE", f"part {name} is {info.file_size} bytes")
        with self.zip.open(info) as f:
            data = f.read(MAX_ENTRY_BYTES + 1)
        if len(data) > MAX_ENTRY_BYTES or len(data) > self.budget:
            raise SheetError("SPREADSHEET_TOO_LARGE", f"part {name} is too large")
        self.budget -= len(data)
        return data

    def xml(self, name):
        data = self.part(name)
        if data is None:
            return None
        try:
            return ET.fromstring(data)
        except ET.ParseError:
            return None

    def rels(self, name) -> list:
        """[(id, type, target, external)] from a .rels part."""
        root = self.xml(name)
        if root is None:
            return []
        out = []
        for r in root.iter():
            if _local(r.tag) == "Relationship":
                out.append((r.get("Id"), r.get("Type") or "", r.get("Target") or "",
                            (r.get("TargetMode") or "").lower() == "external"))
        return out


def rels_part_for(part):
    d, f = posixpath.split(part)
    return posixpath.join(d, "_rels", f + ".rels")


def resolve(base, target) -> Optional[str]:
    if target.startswith("/"):
        p = target
    else:
        p = posixpath.join(posixpath.dirname(base), target)
    p = posixpath.normpath(p).lstrip("/")
    return None if p.startswith("..") else p


def _rel_of_type(rels, suffix):
    for rid, typ, target, ext in rels:
        if typ.endswith("/" + suffix) and not ext:
            return target
    return None


# ---------------------------------------------------------------- workbook reading (sheet/xlsx.rs)
def parse_ref(r):
    i = 0
    while i < len(r) and r[i].isascii() and r[i].isalpha():
        i += 1
    letters, digits = r[:i], r[i:]
    if not letters or not digits or not digits.isascii() or not digits.isdigit():
        return None
    col = 0
    for ch in letters.upper():
        col = col * 26 + ord(ch) - ord("A") + 1
        if col > MAX_COLS:
            return None
    row = int(digits)
    if not 1 <= row <= MAX_ROWS:
        return None
    return row - 1, col - 1


def parse_region(r):
    a, _, b = r.partition(":")
    p, q = parse_ref(a.strip()), parse_ref((b or a).strip())
    if p is None or q is None:
        return None
    return min(p[0], q[0]), min(p[1], q[1]), max(p[0], q[0]), max(p[1], q[1])


def bool_attr(v):
    return v is not None and v.strip() in ("1", "true")


def rich_text(item) -> str:
    out = []
    for child in item:
        if _is(child, "t"):
            out.append(_text(child))
        elif _is(child, "r"):
            t = _child(child, "t")
            if t is not None:
                out.append(_text(t))
    return "".join(out)


class Styles:
    def __init__(self, root):
        self.xfs = []
        if root is None:
            return
        custom = {}
        for fmts in _descendants(root, "numFmts"):
            for nf in _children(fmts, "numFmt"):
                try:
                    custom[int(_attr(nf, "numFmtId"))] = _attr(nf, "formatCode")
                except (TypeError, ValueError):
                    continue
        cache = {}
        xfs = next(iter(_descendants(root, "cellXfs")), None)
        if xfs is None:
            return
        for xf in _children(xfs, "xf"):
            try:
                fid = int(_attr(xf, "numFmtId") or 0)
            except ValueError:
                fid = 0
            if fid not in cache:
                code = custom.get(fid) if custom.get(fid) is not None else BUILTIN.get(fid)
                cache[fid] = NumberFormat.parse(code) if code is not None else None
            self.xfs.append(cache[fid])

    def for_cell(self, s):
        try:
            i = int(s) if s is not None else 0
        except ValueError:
            i = 0
        return self.xfs[i] if 0 <= i < len(self.xfs) else None


def cell_text(c, shared, styles, date1904) -> str:
    fmt = styles.for_cell(_attr(c, "s"))
    v_el = _child(c, "v")
    value = _text(v_el) if v_el is not None else ""
    t = _attr(c, "t") or "n"
    if t == "s":
        try:
            return format_as_text(fmt, shared[int(value.strip())])
        except (ValueError, IndexError):
            return ""
    if t == "str":
        return format_as_text(fmt, clean_text(value))
    if t == "inlineStr":
        is_el = _child(c, "is")
        return format_as_text(fmt, clean_text(rich_text(is_el)) if is_el is not None else "")
    if t == "b":
        v = value.strip()
        return "TRUE" if v in ("1", "true") else "FALSE" if v in ("0", "false") else ""
    if t in ("e", "d"):
        return clean_text(value)
    v = value.strip()
    if not v or "_" in v:
        return ""
    try:
        n = float(v)
    except ValueError:
        return ""
    return render_number(fmt, n, date1904)


class SheetContent:
    def __init__(self):
        self.cells = {}           # (row, col) -> text
        self.checkboxes = {}      # (row, col) -> [(checked, caption)]
        self.hidden_rows = set()
        self.hidden_cols = []     # inclusive (min, max)
        self.merges = []          # inclusive (r1, c1, r2, c2)
        self.unsaved = set()      # formula cells with no saved value


def read_sheet(ws, shared, styles, date1904) -> SheetContent:
    out = SheetContent()
    for cols in _children(ws, "cols"):
        for col in _children(cols, "col"):
            if not bool_attr(_attr(col, "hidden")):
                continue
            try:
                lo, hi = int(_attr(col, "min")) - 1, int(_attr(col, "max")) - 1
            except (TypeError, ValueError):
                continue
            if lo >= 0 and lo <= hi:
                out.hidden_cols.append((lo, min(hi, MAX_COLS - 1)))
    next_row = 0
    for sd in _children(ws, "sheetData"):
        for row in _children(sd, "row"):
            try:
                r = int(_attr(row, "r")) - 1
                if r < 0:
                    r = next_row
            except (TypeError, ValueError):
                r = next_row
            if r >= MAX_ROWS:
                continue
            next_row = r + 1
            if bool_attr(_attr(row, "hidden")):
                out.hidden_rows.add(r)
            next_col = 0
            for c in _children(row, "c"):
                ref = _attr(c, "r")
                if ref is not None:
                    rc = parse_ref(ref)
                    if rc is None:
                        continue
                    cr, cc = rc
                else:
                    cr, cc = r, next_col
                next_col = cc + 1
                if cr >= MAX_ROWS or cc >= MAX_COLS:
                    continue
                text = cell_text(c, shared, styles, date1904)
                if text:
                    out.cells[(cr, cc)] = text
                elif _child(c, "f") is not None:
                    v = _child(c, "v")
                    if v is None or not _text(v).strip():
                        out.unsaved.add((cr, cc))
    for mc in _children(ws, "mergeCells"):
        for m in _children(mc, "mergeCell"):
            region = parse_region(_attr(m, "ref") or "")
            if region is not None and (region[0] != region[2] or region[1] != region[3]):
                out.merges.append(region)
    return out


def read_vml_checkboxes(pkg, sheet_part, include_hidden) -> dict:
    out = {}
    targets = sorted({resolve(sheet_part, t) for _, typ, t, ext in pkg.rels(rels_part_for(sheet_part))
                      if typ.endswith("/vmlDrawing") and not ext} - {None})
    for target in targets:
        root = pkg.xml(target)
        if root is None:
            continue
        for shape in _descendants(root, "shape", (VML_NS,)):
            data = _child(shape, "ClientData", (X_VML_NS,))
            if data is None or _attr(data, "ObjectType") != "Checkbox":
                continue
            if not include_hidden and "visibility:hidden" in (_attr(shape, "style") or "").replace(" ", ""):
                continue
            anchor = _child(data, "Anchor", (X_VML_NS,))
            try:
                nums = [int(p.strip()) for p in _text(anchor).split(",")[:3]]
                at = (nums[2], nums[0])
            except (AttributeError, ValueError, IndexError):
                continue
            checked_el = _child(data, "Checked", (X_VML_NS,))
            if checked_el is None:
                checked = False
            else:
                v = _text(checked_el).strip()
                if v not in ("0", "1"):
                    continue
                checked = v == "1"
            tb = _child(shape, "textbox", (VML_NS,))
            caption = collapse_ws(clean_text(_text(tb))).strip() if tb is not None else ""
            out.setdefault(at, []).append((checked, caption))
    return out


def _expand_ranges(ranges):
    out, nxt = [], 0
    for a, b in sorted(ranges):
        out.extend(range(max(a, nxt), b + 1))
        nxt = max(nxt, b + 1)
    return out


def _first_visible(hidden, lo, hi):
    """First coordinate in lo..=hi not in the sorted hidden list."""
    k = bisect.bisect_left(hidden, lo)
    x = lo
    while k < len(hidden) and hidden[k] == x:
        k += 1
        x += 1
    return x if x <= hi else None


def build_table(sheet: SheetContent, budget: list):
    """-> (grid, n_header) or None. budget: [slots, expansion] for the whole workbook."""
    hidden_rows = sorted(sheet.hidden_rows)
    hidden_cols = _expand_ranges(sheet.hidden_cols)
    hr, hc = set(hidden_rows), set(hidden_cols)

    cells = {}
    for at, boxes in sheet.checkboxes.items():
        if at[0] < MAX_ROWS and at[1] < MAX_COLS:
            text = sheet.cells.pop(at, None)
            parts = [("text", text)] if text is not None else []
            for checked, caption in boxes:
                if parts:
                    parts.append(("text", " "))
                parts.append(("box", checked))
                if caption:
                    parts.append(("text", " " + caption))
            cells[at] = parts
    for at, text in sheet.cells.items():
        cells[at] = [("text", text)]

    merges = []
    for r1, c1, r2, c2 in sheet.merges:
        vr = _first_visible(hidden_rows, r1, r2)
        vc = _first_visible(hidden_cols, c1, c2)
        if vr is None or vc is None:
            continue
        if (vr, vc) != (r1, c1) and (r1, c1) in cells:
            cells[(vr, vc)] = cells.pop((r1, c1))
        merges.append((r1, c1, r2, c2))

    bounds = None
    for r, c in cells:
        if r in hr or c in hc:
            continue
        if bounds is None:
            bounds = [r, c, r, c]
        else:
            bounds = [min(bounds[0], r), min(bounds[1], c), max(bounds[2], r), max(bounds[3], c)]
    if bounds is None:
        return None
    r1, c1, r2, c2 = bounds
    merges = [m for m in merges if m[0] <= r2 and m[2] >= r1 and m[1] <= c2 and m[3] >= c1]
    for m in merges:
        r1, c1, r2, c2 = min(r1, m[0]), min(c1, m[1]), max(r2, m[2]), max(c2, m[3])

    n_rows = (r2 - r1 + 1) - sum(1 for r in hidden_rows if r1 <= r <= r2)
    n_cols = (c2 - c1 + 1) - sum(1 for c in hidden_cols if c1 <= c <= c2)
    if n_rows <= 0 or n_cols <= 0:
        return None
    budget[0] += n_rows * n_cols
    if budget[0] > MAX_GRID_SLOTS:
        raise SheetError("SPREADSHEET_TOO_LARGE",
                         f"workbook covers {budget[0]} cells; the limit is {MAX_GRID_SLOTS}")
    row_map = [r for r in range(r1, r2 + 1) if r not in hr]
    col_map = [c for c in range(c1, c2 + 1) if c not in hc]

    origins, covered = set(), set()
    for mr1, mc1, mr2, mc2 in merges:
        r0 = bisect.bisect_left(row_map, mr1)
        rn = bisect.bisect_right(row_map, mr2) - r0
        c0 = bisect.bisect_left(col_map, mc1)
        cn = bisect.bisect_right(col_map, mc2) - c0
        if rn * cn <= 1:
            continue
        budget[1] += rn * cn - 1
        if budget[1] > MAX_EXPANSION:
            raise SheetError("SPREADSHEET_TOO_LARGE", "merged cells cover too many positions")
        origins.add((r0, c0))
        for r in range(r0, r0 + rn):
            for c in range(c0, c0 + cn):
                if (r, c) != (r0, c0):
                    covered.add((r, c))

    grid = []
    for ri, row in enumerate(row_map):
        out_row = []
        for ci, col in enumerate(col_map):
            if (ri, ci) in covered:
                out_row.append(None)
            else:
                out_row.append(cells.pop((row, col), []))
        grid.append(out_row)
    # Trailing all-empty rows go; a merge is real extent, so covered positions stay.
    while grid and all(s is not None and _is_empty(s) for s in grid[-1]):
        grid.pop()
    if not grid:
        return None
    first_row_spans = {c for (r, c) in origins if r == 0}
    return grid, header_rows(grid, first_row_spans)


def parse_xlsx(data: bytes, include_hidden=False, charts=False):
    pkg = Package(data)
    target = _rel_of_type(pkg.rels("_rels/.rels"), "officeDocument")
    wb_part = resolve("", target) if target else None
    if wb_part is None or not pkg.has(wb_part):
        wb_part = next((n for n in ("xl/workbook.xml", "xl/workbook.bin") if pkg.has(n)), None)
    if wb_part is None:
        raise SheetError("SPREADSHEET_UNREADABLE", "not a readable workbook container")
    body = (pkg.part(wb_part) or b"").lstrip(b"\xef\xbb\xbf").lstrip()
    if body[:1] not in (b"<", b"\xff", b"\xfe"):
        raise SheetError("SPREADSHEET_UNSUPPORTED", "binary workbooks (.xlsb) are not supported yet")
    try:
        workbook = ET.fromstring(pkg.part(wb_part))
    except ET.ParseError:
        raise SheetError("SPREADSHEET_UNREADABLE", "workbook part is not valid XML")
    if not _is(workbook, "workbook"):
        raise SheetError("SPREADSHEET_UNREADABLE", "main part is not a workbook")
    wb_rels = pkg.rels(rels_part_for(wb_part))
    pr = next(iter(_descendants(workbook, "workbookPr")), None)
    date1904 = pr is not None and bool_attr(_attr(pr, "date1904"))

    def sibling(suffix, conventional):
        t = _rel_of_type(wb_rels, suffix)
        name = resolve(wb_part, t) if t else None
        return pkg.xml(name or posixpath.join(posixpath.dirname(wb_part), conventional))
    sst = sibling("sharedStrings", "sharedStrings.xml")
    shared = [clean_text(rich_text(si)) for si in _children(sst, "si")] if sst is not None and _is(sst, "sst") else []
    styles = Styles(sibling("styles", "styles.xml"))

    by_id = {rid: t for rid, _, t, ext in wb_rels if not ext}
    sheets = []
    for sh in (_descendants(workbook, "sheet")):
        if not include_hidden and _attr(sh, "state") in ("hidden", "veryHidden"):
            continue
        rid = _rel_id(sh)
        target = by_id.get(rid) if rid else None
        part = resolve(wb_part, target) if target else None
        if part is not None:
            sheets.append((_attr(sh, "name") or "", part))

    multi = len(sheets) > 1
    blocks, names, failed, unsaved, warnings = [], [], 0, 0, []
    budget = [0, 0]

    def chart_blocks(name, part):
        out = []
        for k, ch in enumerate(sheet_charts(pkg, part, date1904), 1):
            if not ch["has_values"]:
                label = ch["title"] or f"Chart {k}"
                warnings.append(f"chart '{label}' on sheet '{name}' has no saved values")
            out.append(("raw", chart_block(ch, k)))
        return out

    for name, part in sheets:
        ws = pkg.xml(part)
        if charts and ws is not None and _is(ws, "chartsheet"):
            drawn = chart_blocks(name, part)
            if drawn:
                if multi:
                    blocks.append(("heading", name))
                blocks.extend(drawn)
                names.append(name)
            continue
        if ws is None or not _is(ws, "worksheet"):
            failed += 1
            continue
        content = read_sheet(ws, shared, styles, date1904)
        if include_hidden:
            content.hidden_rows, content.hidden_cols = set(), []
        content.checkboxes = read_vml_checkboxes(pkg, part, include_hidden)
        # Any visible formula with no saved value, even where it leaves a column or the
        # whole sheet empty: that is data the reader is missing.
        hidden_cols = set(_expand_ranges(content.hidden_cols))
        unsaved += sum(1 for r, c in content.unsaved
                       if r not in content.hidden_rows and c not in hidden_cols)
        table = build_table(content, budget)
        drawn = chart_blocks(name, part) if charts else []
        if table is None and not drawn:
            continue
        if multi:
            blocks.append(("heading", name))
        if table is not None:
            blocks.append(("table", table[0], table[1]))
        blocks.extend(drawn)
        names.append(name)
    if sheets and failed == len(sheets):
        raise SheetError("SPREADSHEET_UNREADABLE", "no sheet in the workbook could be read")
    if unsaved:
        s = "" if unsaved == 1 else "s"
        warnings.insert(0, f"{unsaved} formula cell{s} had no saved value and {'is' if unsaved == 1 else 'are'} "
                        "blank; open and re-save the file in Excel to fill them")
    return blocks, names, warnings


# ---------------------------------------------------------------- charts (opt-in; not in anydoc)
# An Excel chart keeps its title, series names, categories and the numbers it last drew
# (strCache / numCache) in xl/charts/chartN.xml. Those saved values are copied as they are.
CHART_KIND = {"bar": None, "bar3D": None, "line": "line", "line3D": "line", "pie": "pie", "pie3D": "pie",
              "ofPie": "pie", "doughnut": "doughnut", "area": "area", "area3D": "area", "scatter": "scatter",
              "bubble": "bubble", "radar": "radar", "stock": "stock", "surface": "surface",
              "surface3D": "surface"}


def _cval(el, name="val"):
    """The val attribute of a c:<name> child."""
    child = _child(el, name, CHART_NS) if el is not None else None
    return _attr(child, "val") if child is not None else None


def _chart_text(tx) -> str:
    """Text of a c:tx: rich paragraphs, a cached cell reference, or a literal."""
    if tx is None:
        return ""
    rich = _child(tx, "rich", CHART_NS)
    if rich is not None:
        paras = ["".join(_text(t) for t in _descendants(p, "t", DML_NS)) for p in _children(rich, "p", DML_NS)]
        return collapse_ws(clean_text(" ".join(paras))).strip()
    ref = _child(tx, "strRef", CHART_NS)
    pts = _cache_points(ref) if ref is not None else {}
    if pts:
        return collapse_ws(clean_text(pts[min(pts)][0])).strip()
    v = _child(tx, "v", CHART_NS)
    return collapse_ws(clean_text(_text(v))).strip() if v is not None else ""


def _cache_points(holder):
    """{idx: (text, format code)} from a strRef/numRef (its cache) or a strLit/numLit."""
    cache = None
    for name in ("strCache", "numCache"):
        cache = _child(holder, name, CHART_NS)
        if cache is not None:
            break
    if cache is None and _local(holder.tag) in ("strLit", "numLit"):
        cache = holder
    if cache is None:
        return {}
    fc = _child(cache, "formatCode", CHART_NS)
    code = _text(fc) if fc is not None else None
    out = {}
    for pt in _children(cache, "pt", CHART_NS):
        v = _child(pt, "v", CHART_NS)
        try:
            idx = int(_attr(pt, "idx"))
        except (TypeError, ValueError):
            continue
        if v is not None:
            out[idx] = (_text(v), _attr(pt, "formatCode") or code)
    return out


def _point_count(holder):
    for el in holder.iter():
        if _is(el, "ptCount", CHART_NS):
            try:
                return int(_attr(el, "val"))
            except (TypeError, ValueError):
                return 0
    return 0


def _axis_values(axis, date1904):
    """A series' c:cat / c:val / c:xVal / c:yVal -> ({idx: text}, count, numeric)."""
    if axis is None:
        return {}, 0, False
    for holder in axis:
        kind = _local(holder.tag)
        if kind == "multiLvlStrRef":
            cache = _child(holder, "multiLvlStrCache", CHART_NS)
            if cache is None:
                return {}, 0, False
            levels = [{int(_attr(pt, "idx")): _text(_child(pt, "v", CHART_NS))
                       for pt in _children(lvl, "pt", CHART_NS) if _child(pt, "v", CHART_NS) is not None}
                      for lvl in _children(cache, "lvl", CHART_NS)]
            count = _point_count(cache)
            out = {}
            for i in range(count):
                labels = []
                for lvl in reversed(levels[1:]):      # outer levels label the first point of a group
                    k = max((j for j in lvl if j <= i), default=None)
                    if k is not None:
                        labels.append(lvl[k])
                if levels and i in levels[0]:
                    labels.append(levels[0][i])
                out[i] = " / ".join(clean_text(x) for x in labels)
            return out, count, False
        if kind in ("strRef", "numRef", "strLit", "numLit"):
            pts = _cache_points(holder)
            numeric = kind in ("numRef", "numLit")
            out = {}
            for i, (v, code) in pts.items():
                if numeric:
                    try:
                        n = float(v)
                    except ValueError:
                        out[i] = clean_text(v)
                        continue
                    fmt = NumberFormat.parse(code) if code and code != "General" else None
                    out[i] = render_number(fmt, n, date1904)
                else:
                    out[i] = clean_text(v)
            return out, max(_point_count(holder), max(pts, default=-1) + 1), numeric
    return {}, 0, False


def read_chart(root, date1904):
    """-> dict(title, kind, header, series=[(name, [(label, value)])], has_values) or None."""
    chart = _child(root, "chart", CHART_NS) if root is not None else None
    plot = _child(chart, "plotArea", CHART_NS) if chart is not None else None
    if plot is None:
        return None
    title_el = _child(chart, "title", CHART_NS)
    title = _chart_text(_child(title_el, "tx", CHART_NS)) if title_el is not None else ""
    kinds, series, scatter = [], [], False
    for group in plot:
        local = _local(group.tag)
        if _ns(group.tag) not in CHART_NS or not local.endswith("Chart"):
            continue
        base = local[:-5]
        kind = CHART_KIND.get(base, base.lower())
        if kind is None:
            kind = "bar" if _cval(group, "barDir") == "bar" else "column"
        if kind not in kinds:
            kinds.append(kind)
        scatter = scatter or base in ("scatter", "bubble")
        for k, ser in enumerate(_children(group, "ser", CHART_NS)):
            try:
                order = int(_cval(ser, "order"))
            except (TypeError, ValueError):
                order = len(series) + k
            xy = base in ("scatter", "bubble")
            cats, n_cat, _ = _axis_values(_child(ser, "xVal" if xy else "cat", CHART_NS), date1904)
            vals, n_val, _ = _axis_values(_child(ser, "yVal" if xy else "val", CHART_NS), date1904)
            name = _chart_text(_child(ser, "tx", CHART_NS))
            n = max(n_cat, n_val)
            points = [(cats.get(i, "") if n_cat else str(i + 1), vals.get(i, "")) for i in range(n)]
            series.append((order, name, points, bool(vals)))
    series.sort(key=lambda t: t[0])
    header = ""
    for ax in plot:
        if _local(ax.tag) in ("catAx", "dateAx") and not scatter or (
                scatter and _local(ax.tag) == "valAx" and _cval(ax, "axPos") == "b"):
            t = _child(ax, "title", CHART_NS)
            header = _chart_text(_child(t, "tx", CHART_NS)) if t is not None else ""
            break
    auto_deleted = _cval(chart, "autoTitleDeleted") in ("1", "true")
    if not title and not auto_deleted and len(series) == 1 and series[0][1]:
        title = series[0][1]                  # Excel's automatic title for a one-series chart
    return {"title": title, "kind": " + ".join(kinds) or "chart", "header": header,
            "series": [(name or f"Series {i + 1}", pts) for i, (_, name, pts, _) in enumerate(series)],
            "has_values": any(t[3] for t in series)}


def chart_block(ch, number) -> str:
    """One chart as Markdown: a bold title line, then categories x series."""
    head = f"Chart: {ch['title']}" if ch["title"] else f"Chart {number}"
    line = f"**{render_parts([('text', head)], cell=False)}** ({ch['kind']})"
    if not ch["has_values"]:
        return line + ": no saved values"
    keys, rows = [], {}
    for col, (_, pts) in enumerate(ch["series"]):
        seen = {}
        for label, value in pts:
            k = (label, seen.get(label, 0))
            seen[label] = k[1] + 1
            if k not in rows:
                keys.append(k)
                rows[k] = [""] * len(ch["series"])
            rows[k][col] = value
    grid = [[[("text", ch["header"])]] + [[("text", name)] for name, _ in ch["series"]]]
    grid += [[[("text", k[0])]] + [[("text", v)] for v in rows[k]] for k in keys]
    table = render_table(grid, 1)
    return line if table is None else f"{line}\n\n{table}"


def sheet_charts(pkg, sheet_part, date1904):
    """Charts drawn on a sheet, top to bottom then left to right: [chart dict]."""
    found = []
    for _, typ, target, ext in pkg.rels(rels_part_for(sheet_part)):
        if ext or not typ.endswith("/drawing"):
            continue
        dpart = resolve(sheet_part, target)
        root = pkg.xml(dpart) if dpart else None
        if root is None:
            continue
        chart_parts = {rid: resolve(dpart, t) for rid, ty, t, x in pkg.rels(rels_part_for(dpart))
                       if not x and ty.endswith("/chart")}
        for seq, anchor in enumerate(root):
            if _local(anchor.tag) not in ("twoCellAnchor", "oneCellAnchor", "absoluteAnchor"):
                continue
            frm = _child(anchor, "from", XDR_NS)
            try:
                pos = (int(_text(_child(frm, "row", XDR_NS))), int(_text(_child(frm, "col", XDR_NS))))
            except (TypeError, ValueError, AttributeError):
                pos = (-1, -1)
            for el in anchor.iter():
                if _is(el, "chart", CHART_NS):
                    part = chart_parts.get(_rel_id(el))
                    ch = read_chart(pkg.xml(part), date1904) if part else None
                    if ch is not None:
                        found.append((pos, seq, ch))
    found.sort(key=lambda t: (t[0], t[1]))
    return [ch for _, _, ch in found]


# ---------------------------------------------------------------- CSV (formats/csv.rs)
def _decode(data: bytes) -> str:
    if data[:2] == b"\xff\xfe":
        return data[2:].decode("utf-16-le", errors="replace")
    if data[:2] == b"\xfe\xff":
        return data[2:].decode("utf-16-be", errors="replace")
    if data[:3] == b"\xef\xbb\xbf":
        data = data[3:]
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        # Windows-1252; its five undefined bytes map to the C1 controls (as encoding_rs does).
        return data.decode("cp1252", errors="cp1252_c1")


codecs.register_error("cp1252_c1", lambda e: (e.object[e.start:e.end].decode("latin-1"), e.end))
csv.field_size_limit(sys.maxsize)


def _records(text, delimiter):
    for rec in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter):
        if rec:                      # the csv crate skips blank lines
            yield rec


def sniff_delimiter(text: str) -> str:
    best = (",", 0)
    for d in ",;\t|":
        counts = []
        try:
            for rec in _records(text, d):
                counts.append(len(rec))
                if len(counts) == 20:
                    break
        except csv.Error:
            pass
        if not counts:
            continue
        tally = {}
        for c in counts:
            tally[c] = tally.get(c, 0) + 1
        modal, freq = max(tally.items(), key=lambda kv: (kv[1], kv[0]))
        if modal < 2:
            continue
        score = freq * 1000 + min(modal, 500)
        if score > best[1]:
            best = (d, score)
    return best[0]


def parse_csv(data: bytes):
    text = _decode(data)
    rows = []
    try:
        for rec in _records(text, sniff_delimiter(text)):
            rows.append([[("text", clean_text(f))] for f in rec])
    except csv.Error:
        pass
    while rows and all(_is_empty(s) for s in rows[-1]):
        rows.pop()
    if not rows:
        return []
    return [("table", rows, header_rows(rows))]


# ---------------------------------------------------------------- entry points
# Firecrawl's document-formats.ts, spreadsheet rows. .xls/.xlsb route here too, so they get
# a clear "not supported yet" instead of being mistaken for an image.
CONTENT_TYPE_OF = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.ms-excel.sheet.macroenabled.12",
    ".xlsb": "application/vnd.ms-excel.sheet.binary.macroenabled.12",
    ".xls": "application/vnd.ms-excel",
    ".csv": "text/csv",
}
EXT_OF = {ct: ext for ext, ct in CONTENT_TYPE_OF.items()}


def spreadsheet_ext(name: str = "", content_type: Optional[str] = None, data: bytes = b"") -> Optional[str]:
    """The spreadsheet extension for a source, decided like Firecrawl: the URL path or file
    name first (also mid-path, e.g. file.xlsx/download), then the content type, then the
    bytes. None when it is not a spreadsheet."""
    path = (name or "").split("?")[0].split("#")[0].lower()
    for ext in sorted(CONTENT_TYPE_OF, key=len, reverse=True):
        if path.endswith(ext) or ext + "/" in path:
            return ext
    if content_type:
        ext = EXT_OF.get(content_type.split(";")[0].strip().lower())
        if ext:
            return ext
    if data[:4] == b"PK\x03\x04":
        try:
            names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        except (zipfile.BadZipFile, ValueError, OSError):
            return None
        if any(n.lstrip("/").startswith("xl/workbook.") for n in names):
            return ".xlsx"
    return None


def convert(src, include_hidden: bool = False, ext: Optional[str] = None, charts: bool = False) -> Result:
    """src: path or bytes. ext (".xlsx", ".csv", ...) is needed for bytes that are CSV,
    which has no signature. charts: add each chart's saved numbers as a table (not in Firecrawl)."""
    if isinstance(src, (bytes, bytearray)):
        data = bytes(src)
    else:
        with open(src, "rb") as f:
            data = f.read()
        if ext is None:
            ext = posixpath.splitext(str(src))[1]
    ext = (ext or "").lower()
    if data.startswith(OLE_MAGIC):
        if "EncryptionInfo".encode("utf-16-le") in data or "EncryptedPackage".encode("utf-16-le") in data:
            raise SheetError("SPREADSHEET_ENCRYPTED", "the workbook is password-protected")
        raise SheetError("SPREADSHEET_UNSUPPORTED", "legacy .xls workbooks are not supported yet")
    if data[:2] == b"PK":
        blocks, names, warnings = parse_xlsx(data, include_hidden, charts)
    elif ext == ".csv":
        blocks, names, warnings = parse_csv(data), [], []
    else:
        raise SheetError("SPREADSHEET_UNREADABLE", "not a spreadsheet")
    return Result(document(blocks), names, "; ".join(warnings) or None)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Spreadsheet (xlsx, xlsm, csv) -> Markdown, like Firecrawl.")
    ap.add_argument("src")
    ap.add_argument("-o", "--output")
    ap.add_argument("--include-hidden", action="store_true", help="keep hidden sheets, rows and columns")
    ap.add_argument("--charts", action="store_true", help="add each chart's saved numbers as a table")
    a = ap.parse_args(argv)
    try:
        r = convert(a.src, include_hidden=a.include_hidden, charts=a.charts)
    except SheetError as e:
        print(f"{e.code}: {e}", file=sys.stderr)
        return 1
    if r.warning:
        print(f"warning: {r.warning}", file=sys.stderr)
    if a.output:
        with open(a.output, "w", encoding="utf-8") as f:
            f.write(r.markdown)
    else:
        sys.stdout.write(r.markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
