"""Tests for the vision step in pdf2md.py (prepare -> model answers -> merge).
The model is played by canned answers; all data is invented.
The vision step needs Tesseract; without it only the checks that don't run prepare() run."""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import pdf2md as pv  # noqa: E402
from test_layout import column_chart, font  # noqa: E402

fails = 0
HAVE_OCR = pv.ocr_available()
ANSWER = ("# Broker Review\n\nScores for the period are shown below.\n\n"
          "| Broker | Score | Rank |\n|---|---|---|\n| Alpha Securities | 34.8 | 1 |\n"
          "| Beta Capital | 28.3 | 2 |\n| Gamma Partners | 17.5 | 3 |\n\n"
          "Chart\n\n| Broker | Score |\n|---|---|\n| Alpha | 34.8 |\n| Beta | 28.3 |\n"
          "| Gamma | 17.5 |\n| Delta | 12.9 |\n")


def check(name, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {detail}"))
    fails += 0 if cond else 1


def entry(rep, tid):
    return next(e for e in rep if e["id"] == tid)


def scan_image(angle=0):
    """A scanned page: heading, paragraph, a 3-column table and a bar chart, all pixels."""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (1700, 2200), "white")
    d = ImageDraw.Draw(im)
    big, f = font(56, bold=True), font(34)
    d.text((150, 150), "Broker Review", font=big, fill="black")
    d.text((150, 280), "Scores for the period are shown below.", font=f, fill="black")
    rows = [("Broker", "Score", "Rank"), ("Alpha Securities", "34.8", "1"),
            ("Beta Capital", "28.3", "2"), ("Gamma Partners", "17.5", "3")]
    for i, (a, b, c) in enumerate(rows):
        y = 400 + i * 60
        d.text((150, y), a, font=f, fill="black")
        d.text((800, y), b, font=f, fill="black")
        d.text((1100, y), c, font=f, fill="black")
    for i, (lbl, v) in enumerate((("Alpha", 34.8), ("Beta", 28.3), ("Gamma", 17.5))):
        x = 250 + i * 300
        d.rectangle((x, 1500 - v * 15, x + 150, 1500), fill="#2F5597")
        d.text((x + 30, 1510), lbl, font=f, fill="black")
        d.text((x + 30, 1500 - v * 15 - 45), str(v), font=f, fill="black")
    return im.rotate(angle, expand=True)


def scanned_table_pdf(path, angle=0, jpeg=False):
    """The scan as a 200 dpi PDF page. Stored losslessly by default, so rotation checks test
    rotation; jpeg=True stores it the way most scanners do (JPEG noise, OCR misreads)."""
    from reportlab.pdfgen import canvas
    im = scan_image(angle)
    if jpeg:
        im.save(path, resolution=200)                 # Pillow writes RGB pages as JPEG
        return path
    png = path + ".png"
    im.save(png)
    w, h = im.width * 72 / 200, im.height * 72 / 200
    c = canvas.Canvas(path, pagesize=(w, h))
    c.drawImage(png, 0, 0, w, h)
    c.save()
    return path


def text_then_scan_pdf(path):
    """Page 1 native text, page 2 the scan."""
    from reportlab.pdfgen import canvas
    png = path + ".png"
    scan_image().save(png)
    c = canvas.Canvas(path, pagesize=(612, 792))
    c.setFont("Helvetica", 11)
    c.drawString(72, 720, "Cover note: the broker review follows on the next page.")
    c.showPage()
    c.drawImage(png, 0, 0, 612, 792)
    c.save()
    return path


def write_answer(work, tid, text):
    with open(os.path.join(work, "answers", tid + ".md"), "w") as fh:
        fh.write(text)


def test_numbers():
    check("norm 1,200", pv.norm_number("1,200") == "1200")
    check("norm 34.80", pv.norm_number("34.80") == "34.8")
    check("norm 5.0", pv.norm_number("5.0") == "5")
    marked, unv, est = pv.check_answer("| A | 34.8 | ~42 | 99 |", "value 34.80 here")
    check("verified number unmarked", "34.8 |" in marked, marked)
    check("estimate kept, not flagged", "~42 |" in marked and est == 1, marked)
    check("unknown number flagged", "99 ⚠" in marked and unv == ["99"], (marked, unv))


def test_requires_tesseract():
    real = pv.ocr_available
    pv.ocr_available = lambda: False
    try:
        with tempfile.TemporaryDirectory() as tmp:
            pdf = column_chart(os.path.join(tmp, "chart.pdf"), data_labels=True)
            try:
                pv.prepare(pdf, os.path.join(tmp, "w"))
                e = None
            except Exception as ex:  # noqa: BLE001
                e = ex
            check("prepare without Tesseract: stops with an install hint",
                  isinstance(e, RuntimeError) and "Tesseract" in str(e) and "install" in str(e), repr(e))
            try:
                pv.main(["prepare", pdf, os.path.join(tmp, "w2")])
                code = None
            except SystemExit as ex:
                code = ex.code
            check("cli prepare without Tesseract: error message, no traceback",
                  isinstance(code, str) and code.startswith("error:") and "Tesseract" in code, code)
    finally:
        pv.ocr_available = real


def test_vector_chart():
    with tempfile.TemporaryDirectory() as tmp:
        pdf = column_chart(os.path.join(tmp, "chart.pdf"), data_labels=True)
        work = os.path.join(tmp, "work")
        man = pv.prepare(pdf, work)
        charts = [t for t in man["tasks"] if t["kind"] == "chart"]
        check("chart: one chart task", len(charts) == 1, man["tasks"])
        check("chart: no page task on a text PDF", not any(t["kind"] == "page" for t in man["tasks"]))
        t = charts[0]
        check("chart: image file saved", os.path.exists(os.path.join(work, t["image"])))
        check("chart: prompt carries page text", "Quarterly Volume" in t["prompt"])
        write_answer(work, t["id"], "```markdown\nQuarterly Volume\n\n| Quarter | Desk A | Desk B |\n"
                     "|---|---|---|\n| Q1 | 30 | 25 |\n| Q2 | 45 | 35 |\n| Q3 | 60 | 40 |\n"
                     "| Q4 | 52 | 77 |\n```")
        md, rep = pv.merge(work)
        check("chart: table placed right after the chart image",
              md.index("](images/") < md.index("| Quarter |") < md.index("Volumes rose"), md)
        check("chart: misread value flagged", "77 ⚠" in md and rep[0]["unverified"] == ["77"], rep)
        check("chart: printed values not flagged", "| 52 |" in md and "| 30 |" in md)
        check("chart: code fence stripped", "```" not in md)
        check("chart: no page markers by default", "<!-- page" not in md)
        check("chart: report written", os.path.exists(os.path.join(work, "report.json")))


def test_scanned(angle):
    with tempfile.TemporaryDirectory() as tmp:
        pdf = scanned_table_pdf(os.path.join(tmp, "scan.pdf"), angle)
        work = os.path.join(tmp, "work")
        man = pv.prepare(pdf, work)
        pages = [t for t in man["tasks"] if t["kind"] == "page"]
        check(f"scan {angle}: one task, the page", len(man["tasks"]) == 1 and len(pages) == 1, man["tasks"])
        t = pages[0]
        from PIL import Image
        with Image.open(os.path.join(work, t["image"])) as im:
            check(f"scan {angle}: page image upright (portrait)", im.height > im.width, im.size)
        check(f"scan {angle}: OCR text available to check against", "34.8" in t["check_text"],
              t["check_text"][:300])
        write_answer(work, t["id"], ANSWER)
        md, rep = pv.merge(work, page_markers=True)
        check(f"scan {angle}: printed values not flagged", "| Alpha Securities | 34.8 | 1 |" in md, md)
        check(f"scan {angle}: invented row flagged",
              "12.9 ⚠" in md and entry(rep, t["id"])["unverified"] == ["12.9"], rep)
        check(f"scan {angle}: scan image linked", "![Page 1 (scan)](scans/page_1.png)" in md)
        check(f"scan {angle}: one-page document has no page marker", "<!-- page" not in md, md[:200])


def test_jpeg_scan_safety_net():
    """JPEG noise makes OCR misread digits on turned scans. Whatever it misreads, a correct
    value from the model is flagged, never silently accepted or dropped."""
    for angle in (90, 180):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = scanned_table_pdf(os.path.join(tmp, "scan.pdf"), angle, jpeg=True)
            work = os.path.join(tmp, "work")
            t = [t for t in pv.prepare(pdf, work)["tasks"] if t["kind"] == "page"][0]
            write_answer(work, t["id"], ANSWER)
            md, rep = pv.merge(work)
            seen = pv.numbers_in(t["check_text"])
            missing = [n for n in ("34.8", "28.3", "17.5", "1", "2", "3") if n not in seen]
            unverified = set(entry(rep, t["id"])["unverified"])
            check(f"jpeg scan {angle}: every value OCR missed is flagged",
                  all(n in unverified for n in missing), (missing, unverified))
            check(f"jpeg scan {angle}: invented row flagged", "12.9 ⚠" in md, md)
            check(f"jpeg scan {angle}: no value lost", all(v in md for v in ("34.8", "28.3", "17.5")), md)


def test_page_markers():
    with tempfile.TemporaryDirectory() as tmp:
        pdf = text_then_scan_pdf(os.path.join(tmp, "two.pdf"))
        work = os.path.join(tmp, "work")
        man = pv.prepare(pdf, work)
        t = [t for t in man["tasks"] if t["kind"] == "page"]
        check("two pages: scan task is page 2", [x["page"] for x in t] == [2], man["tasks"])
        write_answer(work, "p2_page", ANSWER)
        md, _ = pv.merge(work)
        check("two pages, no markers: page 1 text kept", "Cover note" in md, md[:300])
        check("two pages, no markers: page 2 answer merged", "| Gamma Partners | 17.5 | 3 |" in md)
        check("two pages, no markers: none written", "<!-- page" not in md and "---\n" not in md, md)
        md, _ = pv.merge(work, page_markers=True)
        check("two pages, markers: Firecrawl format between pages only",
              "<!-- page 1 -->" not in md and "\n\n---\n\n<!-- page 2 -->\n\n" in md
              and md.index("Cover note") < md.index("<!-- page 2 -->") < md.index("Broker Review"), md)


def test_missing_answer():
    with tempfile.TemporaryDirectory() as tmp:
        pdf = scanned_table_pdf(os.path.join(tmp, "scan.pdf"))
        work = os.path.join(tmp, "work")
        pv.prepare(pdf, work)
        with open(os.path.join(work, "draft.md")) as fh:
            draft = fh.read()
        md, rep = pv.merge(work)
        check("no answer: OCR output kept", "Broker Review" in md, md)
        check("no answer: scan not linked", "scan)" not in md, md)
        check("no answer: reported", entry(rep, "p1_page")["status"].startswith("no answer"), rep)
        check("no answer: same text as draft", md.strip() == draft.strip())


def test_cli():
    with tempfile.TemporaryDirectory() as tmp:
        pdf = column_chart(os.path.join(tmp, "chart.pdf"), data_labels=True)
        work = os.path.join(tmp, "w")
        pv.main(["prepare", pdf, work])
        man = json.load(open(os.path.join(work, "tasks.json")))
        write_answer(work, man["tasks"][0]["id"], "| Q | A |\n|---|---|\n| Q1 | 30 |")
        out = os.path.join(tmp, "out.md")
        pv.main(["merge", work, "-o", out])
        check("cli: output written", "| Q1 | 30 |" in open(out).read())


if __name__ == "__main__":
    test_numbers()
    test_requires_tesseract()
    if HAVE_OCR:
        test_vector_chart()
        for a in (0, 90, 180, 270):
            test_scanned(a)
        test_jpeg_scan_safety_net()
        test_page_markers()
        test_missing_answer()
        test_cli()
    else:
        print("SKIP prepare/merge checks: the vision step needs Tesseract")
    print("all passed" if not fails else f"{fails} FAILED")
    sys.exit(1 if fails else 0)
