"""Firecrawl contract tests for scrape(): parser options and input detection.
Cases follow firecrawl/firecrawl's pdf-parser-options.test.ts, isPdfBuffer.test.ts and
parsers-pdf-page-markdown.test.ts. All documents are generated here with invented data.
Run: python tests/test_contract.py            (exit code 0 = all passed)"""
import base64
import os
import sys
import tempfile

from reportlab.pdfgen import canvas

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import pdf2md  # noqa: E402
from fonts import font  # noqa: E402

TMP = tempfile.mkdtemp(prefix="pdf2md_contract_")
fails = 0


def check(name, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {detail}"))
    fails += 0 if cond else 1


def check_ocr(name, cond, detail=""):
    if pdf2md.ocr_available():
        check(name, cond, detail)
    else:
        print(f"SKIP {name}  (needs Tesseract)")


def raises(fn, *args, **kw):
    """The exception fn raises, or None."""
    try:
        fn(*args, **kw)
    except Exception as e:  # noqa: BLE001
        return e
    return None


def three_page_pdf():
    path = os.path.join(TMP, "three.pdf")
    c = canvas.Canvas(path)
    for n, word in enumerate(("Alpha", "Bravo", "Charlie"), 1):
        c.setFont("Helvetica-Bold", 18)
        c.drawString(72, 740, f"{word} Section")
        c.setFont("Helvetica", 11)
        c.drawString(72, 700, f"Body text on page {n} talks about the {word.lower()} desk.")
        c.drawString(72, 684, "A second line keeps the paragraph long enough to read.")
        c.showPage()
    c.save()
    return path


def text_png():
    from PIL import Image, ImageDraw
    path = os.path.join(TMP, "note.png")
    im = Image.new("RGB", (1400, 400), "white")
    ImageDraw.Draw(im).text((60, 150), "Delivery note 4471 for Harbor Supplies", font=font(48), fill="black")
    im.save(path)
    return path


def scan_png(lines, name, size=(1700, 2200), color="white"):
    """A page of text drawn as pixels, like a scanner would produce."""
    from PIL import Image, ImageDraw
    path = os.path.join(TMP, name)
    im = Image.new("RGB", size, color)
    d = ImageDraw.Draw(im)
    for i, ln in enumerate(lines):
        d.text((150, 300 + i * 70), ln, font=font(40), fill="black")
    im.save(path)
    return path


SCAN_LINES = ["Shipment Summary", "Crates received at the north dock: 118",
              "Inspector on duty signed every manifest."]


def make_pdf(name, pages):
    """pages: list of callables(canvas) drawing one page each."""
    path = os.path.join(TMP, name)
    c = canvas.Canvas(path)
    for draw in pages:
        draw(c)
        c.showPage()
    c.save()
    return path


def full_image(png):
    return lambda c: c.drawImage(png, 0, 0, *c._pagesize)


def text_body(c, lines=12):
    c.setFont("Helvetica", 11)
    for i in range(lines):
        c.drawString(72, 740 - i * 16, f"Line {i + 1} of the operations memo covers staffing, "
                                       "shifts and the weekly schedule.")


def scrape(src, **kw):
    return pdf2md.scrape(src, images="none", **kw)


# ---- parsers list ----

def test_parsers(pdf, png):
    r = scrape(pdf)
    check("default parsers: PDF parsed to markdown", r["success"] and "Alpha Section" in r["data"]["markdown"])
    r = scrape(png)
    check_ocr("default parsers: image OCR'd", "Harbor" in r["data"].get("markdown", ""), r)

    data = open(pdf, "rb").read()
    r = scrape(pdf, parsers=[])
    check("parsers=[]: base64 of the input, no markdown",
          base64.b64decode(r["data"].get("base64", "")) == data and "markdown" not in r["data"])
    r = scrape(pdf, parsers=["image"])
    check("parsers=['image'] + PDF: pdf parser off -> base64", "base64" in r["data"], list(r["data"]))

    a = scrape(pdf, parsers=["pdf"])["data"]["markdown"]
    b = scrape(pdf, parsers=[{"type": "pdf"}])["data"]["markdown"]
    c = scrape(pdf, parsers=[{"type": "image"}, {"type": "pdf", "mode": "auto"}])["data"]["markdown"]
    check("string and object forms give the same markdown", a == b == c)

    for bad in (["images"], [{"type": "image", "mode": "ocr"}], ["html"], [{"type": "pdfs"}], [42]):
        e = raises(scrape, pdf, parsers=bad)
        check(f"parsers={bad!r} rejected", isinstance(e, ValueError), repr(e))


# ---- pdf parser options ----

def test_pdf_options(pdf):
    e = raises(scrape, pdf, parsers=[{"type": "pdf", "mode": "turbo"}])
    check("bad mode rejected", isinstance(e, ValueError), repr(e))
    e = raises(scrape, pdf, parsers=[{"type": "pdf", "foo": 1}])
    check("unknown pdf option rejected, named", isinstance(e, ValueError) and "foo" in str(e), repr(e))

    m = scrape(pdf, parsers=[{"type": "pdf", "maxPages": 2}])["data"]["metadata"]
    check("maxPages=2: numPages 2, totalPages 3", (m.get("numPages"), m.get("totalPages")) == (2, 3), m)
    for bad in (0, -1, "2", True, 2.5):
        e = raises(scrape, pdf, parsers=[{"type": "pdf", "maxPages": bad}])
        check(f"maxPages={bad!r} rejected", isinstance(e, ValueError), repr(e))

    base = scrape(pdf)["data"]["markdown"]
    d = scrape(pdf, parsers=[{"type": "pdf", "pages": True}])["data"]
    pages = d.get("pages") or []
    check("pages: one entry per page, numbered 1..3", [p.get("pageNumber") for p in pages] == [1, 2, 3], pages)
    check("pages: document markdown unchanged", d["markdown"] == base)
    check("pages off: no pages field", "pages" not in scrape(pdf)["data"])

    d = scrape(pdf, parsers=[{"type": "pdf", "blocks": True}])["data"]
    md, spans_ok, n = d["markdown"], True, 0
    for pg in d.get("blocks") or []:
        for it in pg["items"]:
            sp = it.get("markdownSpan")
            if sp:
                n += 1
                spans_ok &= md[sp[0]:sp[1]] == it["content"]
    check("blocks: present, every markdownSpan exact", n > 0 and spans_ok, n)
    check("blocks: document markdown unchanged", md == base)

    md = scrape(pdf, parsers=[{"type": "pdf", "pageMarkers": True}])["data"]["markdown"]
    p2, p3 = md.find("<!-- page 2 -->"), md.find("<!-- page 3 -->")
    check("pageMarkers: markers for pages 2 and 3, in order",
          0 < p2 < p3 and md.index("Alpha") < p2 < md.index("Bravo") < p3 < md.index("Charlie"), md)
    check("pageMarkers off: no markers", "<!-- page" not in base)

    # deprecated alias, folded into pages; explicit pages wins
    d = scrape(pdf, parsers=[{"type": "pdf", "pageMarkdown": True}])["data"]
    check("pageMarkdown alias -> pages", len(d.get("pages") or []) == 3, list(d))
    d = scrape(pdf, parsers=[{"type": "pdf", "pages": False, "pageMarkdown": True}])["data"]
    check("explicit pages=False beats pageMarkdown=True", "pages" not in d, list(d))
    d = scrape(pdf, parsers=[{"type": "pdf", "pages": True, "pageMarkdown": False}])["data"]
    check("explicit pages=True beats pageMarkdown=False", len(d.get("pages") or []) == 3, list(d))
    d = scrape(pdf, parsers=[{"type": "pdf", "pageMarkers": True, "pageMarkdown": True}])["data"]
    check("pageMarkers still applied next to the alias", "<!-- page 2 -->" in d["markdown"] and d.get("pages"))

    # Firecrawl's `refresh` skips its hosted cache; there is no service or cache here
    e = raises(scrape, pdf, parsers=[{"type": "pdf", "refresh": True}])
    check("refresh rejected like any unknown option", isinstance(e, ValueError) and "refresh" in str(e), repr(e))


def test_response(pdf):
    r = scrape(pdf, formats=["markdown", "html"])
    check("unsupported format: warning, still success", r["success"] and "html" in r["data"].get("warning", ""), r)
    m = scrape(pdf)["data"]["metadata"]
    check("metadata: sourceFile, numPages, totalPages, pdfType",
          m.get("sourceFile") == "three.pdf" and m.get("numPages") == 3 and m.get("totalPages") == 3
          and m.get("pdfType") == "TextBased", m)
    m = scrape(open(pdf, "rb").read())["data"]["metadata"]
    check("bytes input: sourceFile document.pdf", m.get("sourceFile") == "document.pdf", m)


# ---- scan / OCR routing (parse-fire-pdf.test.ts, pdf-inspector OCR routing) ----

def test_routing():
    png = scan_png(SCAN_LINES, "scan.png")
    scanned = make_pdf("scanned.pdf", [full_image(png)])
    mixed = make_pdf("mixed.pdf", [text_body, full_image(png)])
    pale = scan_png([], "pale.png", color="#f2f2f2")
    watermark = make_pdf("watermark.pdf", [lambda c: (full_image(pale)(c), text_body(c))])
    cover = make_pdf("cover.pdf", [lambda c: (full_image(pale)(c), c.setFont("Helvetica-Bold", 28),
                                              c.drawString(72, 600, "Quarterly Operations Review"))])
    from PIL import Image
    grad = os.path.join(TMP, "gradient.png")
    Image.linear_gradient("L").resize((850, 1100)).point(lambda v: 255 - v // 8).convert("RGB").save(grad)
    gradient = make_pdf("gradient.pdf", [lambda c: (full_image(grad)(c), c.setFont("Helvetica-Bold", 28),
                                                    c.drawString(72, 600, "Quarterly Operations Review"))])
    sparse_png = scan_png(["Received 118 crates"], "sparse.png")
    sparse = make_pdf("sparse_scan.pdf", [lambda c: (full_image(sparse_png)(c), c.setFont("Helvetica", 9),
                                                     c.drawString(72, 812, "Northwind Freight - Internal"))])
    header = make_pdf("header_scan.pdf", [lambda c: (full_image(png)(c), c.setFont("Helvetica", 9),
                                                     c.drawString(72, 812, "Northwind Freight - Internal"))])

    e = raises(scrape, scanned, parsers=[{"type": "pdf", "mode": "fast"}])
    check("fast + scanned PDF: SCRAPE_PDF_OCR_REQUIRED",
          isinstance(e, pdf2md.PDFOCRRequiredError) and e.code == "SCRAPE_PDF_OCR_REQUIRED", repr(e))
    check("fast + scanned PDF: message names auto/ocr", e is not None and '"auto"' in str(e) and '"ocr"' in str(e))

    r = scrape(mixed, parsers=[{"type": "pdf", "mode": "fast"}])
    d = r["data"]
    check("fast + mixed PDF: no error, text page kept", "Line 1 of the operations memo" in d["markdown"])
    check("fast + mixed PDF: scan page skipped, not OCR'd", "Crates" not in d["markdown"])
    check("fast + mixed PDF: warning names the skipped page", "[2]" in d.get("warning", ""), d.get("warning"))

    r = scrape(scanned)
    d = r["data"]
    check("auto + scanned: pdfType Scanned", d["metadata"].get("pdfType") == "Scanned", d["metadata"])
    check_ocr("auto + scanned: OCR text", "Crates received" in d.get("markdown", ""), d.get("markdown"))
    check_ocr("auto + scanned: OCR'd page listed in pagesNeedingOcr",
              d["metadata"].get("pagesNeedingOcr") == [1], d["metadata"])

    real = pdf2md.ocr_available
    pdf2md.ocr_available = lambda: False
    try:
        d = scrape(scanned)["data"]
    finally:
        pdf2md.ocr_available = real
    check("auto + scanned, no Tesseract: success with a warning",
          "Tesseract" in d.get("warning", ""), d.get("warning"))

    r = scrape(mixed)
    md = r["data"]["markdown"]
    check("auto + mixed: pdfType Mixed", r["data"]["metadata"].get("pdfType") == "Mixed", r["data"]["metadata"])
    check_ocr("auto + mixed: text page native, scan page OCR'd",
              "Line 1 of the operations memo" in md and "Crates received" in md, md)

    d = scrape(watermark)["data"]
    check("text page with a background image: TextBased, not OCR'd",
          d["metadata"].get("pdfType") == "TextBased" and not d["metadata"].get("pagesNeedingOcr"), d["metadata"])
    check("text page with a background image: text once",
          d["markdown"].count("Line 1 of the operations memo") == 1, d["markdown"])

    d = scrape(cover)["data"]
    check("short cover page on a background image: not OCR'd",
          not d["metadata"].get("pagesNeedingOcr"), d["metadata"])
    check("short cover page on a background image: title once",
          d["markdown"].count("Quarterly Operations Review") == 1, d["markdown"])

    d = scrape(gradient, parsers=[{"type": "pdf", "mode": "fast"}])["data"]
    check("title on a faint gradient background: text page, fast mode works",
          d["metadata"].get("pdfType") == "TextBased" and "Quarterly Operations Review" in d["markdown"], d["metadata"])
    e = raises(scrape, cover, parsers=[{"type": "pdf", "mode": "fast"}])
    check("short cover page on a background image: fast mode works", e is None, repr(e))

    d = scrape(sparse)["data"]
    check("sparse scan (one line) with a native header: still needs OCR",
          d["metadata"].get("pagesNeedingOcr") == [1], d["metadata"])
    check_ocr("sparse scan with a native header: line OCR'd", "118 crates" in d["markdown"], d["markdown"])

    d = scrape(header)["data"]
    check("scan with a native header: page needs OCR", d["metadata"].get("pagesNeedingOcr") == [1], d["metadata"])
    check("scan with a native header: header kept", "Northwind Freight" in d["markdown"], d["markdown"])
    check_ocr("scan with a native header: body OCR'd", "Crates received" in d["markdown"], d["markdown"])


# ---- PDF header detection (isPdfBuffer.test.ts) ----

def test_header():
    is_pdf = getattr(pdf2md, "is_pdf_buffer", None)
    check("is_pdf_buffer exists", is_pdf is not None)
    if is_pdf is None:
        return
    cases = [
        ("PDF-1.4 header", b"%PDF-1.4\n...", True),
        ("PDF-2.0 header", b"%PDF-2.0\n...", True),
        ("UTF-8 BOM prefix", b"\xef\xbb\xbf%PDF-1.7\n", True),
        ("whitespace/newlines first", b"\n\n  \r\n%PDF-1.5\n", True),
        ("junk within 1KB", b"\x00" * 500 + b"%PDF-1.4", True),
        ("%PDF after the 1KB window", b" " * 1024 + b"%PDF-1.4", False),
        ("HTML", b"<!DOCTYPE html><html><body>Not Found</body></html>", False),
        ("plain text", b"just some text", False),
        ("empty", b"", False),
        ("shorter than magic", b"%PD", False),
        ("part of the magic", b"%PD\x00\x00\x00", False),
        ("404 page", b"<html><head><title>404 Not Found</title></head></html>", False),
        ("JSON", b'{"error": "not found"}', False),
        ("exactly %PDF", b"%PDF", True),
        ("%PDF at the 1KB boundary", b"A" * 1020 + b"%PDF", True),
    ]
    for name, data, want in cases:
        check(f"header: {name}", is_pdf(data) is want, is_pdf(data))


if __name__ == "__main__":
    pdf, png = three_page_pdf(), text_png()
    test_parsers(pdf, png)
    test_pdf_options(pdf)
    test_response(pdf)
    test_routing()
    test_header()
    if not pdf2md.ocr_available():
        print("Tesseract not installed: OCR checks skipped")
    print("all passed" if not fails else f"{fails} FAILED")
    sys.exit(1 if fails else 0)
