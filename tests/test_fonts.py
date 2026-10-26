"""Symbol fonts: Symbol / ZapfDingbats built-in encodings and Word's private-use symbol codes.
tests/fixtures/symbol_builtin_encoding.pdf (+ .expected.md) are pdf-inspector's fixture and snapshot
(MIT, see NOTICE); the Word-style PDF is built here with invented content.
Run: python tests/test_fonts.py            (exit code 0 = all passed)"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import pdf2md  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def eq(name, got, want):
    check(name, got == want, f"\n  got:  {got!r}\n  want: {want!r}")


def lines(md):
    return [ln.lstrip("#").strip() for ln in md.splitlines() if ln.strip()]


def builtin_fixture():
    """Symbol/ZapfDingbats with no /Encoding, with /Differences, with WinAnsiEncoding (which
    replaces the built-in map) and with the name SymbolEncoding (which keeps it)."""
    md = pdf2md.convert(os.path.join(HERE, "fixtures", "symbol_builtin_encoding.pdf"), images="none").markdown
    with open(os.path.join(HERE, "fixtures", "symbol_builtin_encoding.expected.md"), encoding="utf-8") as f:
        want = f.read()
    eq("pdf-inspector symbol fixture: same lines as its snapshot", lines(md), lines(want))


# ---------------------------------------------------------------- a Word-style PDF, built here
def _tounicode(pairs):
    body = "".join(f"<{cid:04X}> <{u:04X}>\n" for cid, u in pairs)
    return ("/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapName /T def /CMapType 2 def\n"
            "1 begincodespacerange <0000> <FFFF> endcodespacerange\n"
            f"{len(pairs)} beginbfchar\n{body}endbfchar\nendcmap CMapName currentdict /CMap defineresource pop end end")


def word_style_pdf(path):
    """Type0 fonts whose ToUnicode maps glyphs into U+F0xx (as Word writes symbol fonts), a plain
    TrueType Wingdings with no ToUnicode, and an ordinary Helvetica line."""
    objs = {}
    descriptor = ("<< /Type /FontDescriptor /FontName /X /Flags 4 /FontBBox [0 -200 1000 800] /ItalicAngle 0 "
                  "/Ascent 800 /Descent -200 /CapHeight 700 /StemV 80 >>")
    objs[10] = descriptor
    type0 = {  # resource name: (BaseFont, [(cid, unicode)])
        "F1": ("BCDIEE+Wingdings-Regular", [(1, 0xF06F), (2, 0xF06E), (3, 0xF0FC), (4, 0xF0E9), (5, 0x20)]),
        "F2": ("ABCDEE+SymbolMT", [(1, 0xF0B7), (2, 0xF061), (5, 0x20)]),
        "F3": ("ABCDEE+Calibri", [(1, 0xF041), (2, 0xF042), (5, 0x20)]),
        "F4": ("ABCDEE+Wingdings2", [(1, 0xF052), (2, 0xF054), (5, 0x20)]),
    }
    fonts, n = {}, 20
    for res, (base, pairs) in type0.items():
        cmap = _tounicode(pairs)
        objs[n] = f"<< /Length {len(cmap)} >>\nstream\n{cmap}\nendstream"
        objs[n + 1] = (f"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /{base} /CIDSystemInfo << /Registry "
                       f"(Adobe) /Ordering (Identity) /Supplement 0 >> /FontDescriptor 10 0 R /DW 600 >>")
        objs[n + 2] = (f"<< /Type /Font /Subtype /Type0 /BaseFont /{base} /Encoding /Identity-H "
                       f"/DescendantFonts [{n + 1} 0 R] /ToUnicode {n} 0 R >>")
        fonts[res] = n + 2
        n += 3
    objs[n] = ("<< /Type /Font /Subtype /TrueType /BaseFont /Wingdings /FirstChar 32 /LastChar 126 "
               f"/Widths [{' '.join(['600'] * 95)}] /FontDescriptor 10 0 R >>")
    fonts["F5"] = n
    objs[n + 1] = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    fonts["F6"] = n + 1
    rows = [("F1", "<00010005000200050003000500040005>"), ("F2", "<000100050002>"), ("F3", "<00010002>"),
            ("F4", "<000100050002>"), ("F5", "(o n)"), ("F6", "(Plain text stays.)")]
    content = "".join(f"BT /{f} 14 Tf 72 {700 - 40 * i} Td {s} Tj ET\n" for i, (f, s) in enumerate(rows))
    objs[4] = f"<< /Length {len(content)} >>\nstream\n{content}endstream"
    font_res = " ".join(f"/{k} {v} 0 R" for k, v in fonts.items())
    objs[3] = (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
               f"/Resources << /Font << {font_res} >> >> >>")
    objs[2] = "<< /Type /Pages /Kids [3 0 R] /Count 1 >>"
    objs[1] = "<< /Type /Catalog /Pages 2 0 R >>"
    out, offsets = b"%PDF-1.7\n", {}
    for k in sorted(objs):
        offsets[k] = len(out)
        out += f"{k} 0 obj\n{objs[k]}\nendobj\n".encode("latin-1")
    xref_at, size = len(out), max(objs) + 1
    out += f"xref\n0 {size}\n0000000000 65535 f \n".encode()
    for k in range(1, size):
        out += (f"{offsets[k]:010d} 00000 n \n" if k in offsets else "0000000000 65535 f \n").encode()
    out += f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode()
    with open(path, "wb") as f:
        f.write(out)
    return path


def word_style():
    path = word_style_pdf(os.path.join(tempfile.mkdtemp(prefix="fonts_"), "word.pdf"))
    md = pdf2md.convert(path, images="none").markdown
    got = lines(md)
    check("no private-use characters left", not any(0xE000 <= ord(c) <= 0xF8FF for c in md), md)
    eq("Wingdings (Word, private use): box, square, check, up arrow", got[0], "\U0001F78F ◼ ✓ \U0001F879")
    check("SymbolMT: bullet and Greek alpha", got[1] in ("- α", "• α"), got[1])
    eq("text font with letters in private use: plain letters (pdf-inspector rule)", got[2], "AB")
    eq("Wingdings 2: ballot boxes", got[3], "☑ ☒")
    eq("plain TrueType Wingdings, no ToUnicode: same table", got[4], "\U0001F78F ◼")
    eq("ordinary font untouched", got[5], "Plain text stays.")


def names():
    t = pdf2md._symbol_table_for
    eq("Wingdings subset name", t("ABCDEF+Wingdings-Regular"), "wingdings")
    eq("Wingdings 2 spellings", [t("Wingdings2"), t("Wingdings-2"), t("Wingdings 2")], ["wingdings2"] * 3)
    eq("Wingdings 3", t("XYZABC+Wingdings3"), "wingdings3")
    eq("Symbol aliases", [t("Symbol"), t("SymbolMT"), t("ABCDEF+SymbolMT")], ["symbol"] * 3)
    eq("ZapfDingbats aliases", [t("ZapfDingbats"), t("ITCZapfDingbats"), t("Dingbats")], ["zapfdingbats"] * 3)
    eq("a font that only mentions Symbol is not Symbol", t("SymbolicSans"), None)
    eq("ordinary fonts", [t("Helvetica"), t("ABCDEF+Calibri")], [None, None])
    clean = pdf2md._clean_private_use
    eq("pdf-inspector rule: bullets", [clean("", None), clean("", None)], ["•", "•"])
    eq("pdf-inspector rule: check", clean("", None), "✓")
    eq("pdf-inspector rule: other codes -> plain letter", clean("", None), "Ao")
    eq("outside F000-F0FF untouched", clean("x", None), "x")


if __name__ == "__main__":
    for t in (builtin_fixture, word_style, names):
        try:
            t()
        except Exception:
            import traceback
            RESULTS.append((f"{t.__name__} crashed", False, traceback.format_exc()))
    failed = [r for r in RESULTS if not r[1]]
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"FAIL {name}{detail}")
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    if not failed:
        print("all passed")
    sys.exit(1 if failed else 0)
