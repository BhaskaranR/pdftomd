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
    marked, iss, est = pv.check_answer("| A | 34.8 | ~42 | 99 |", "value 34.80 here")
    check("verified number unmarked", "34.8 |" in marked, marked)
    check("estimate kept, not flagged", "~42 |" in marked and est == 1, marked)
    check("unknown number flagged", "99 ⚠" in marked and iss["unverified"] == ["99"], (marked, iss))


def flagged(iss):
    return iss["unverified"] + iss["sign"] + iss["wrong_row"]


def test_sign_and_position():
    """Numbers must match the page in value, sign and row."""
    src = "Net income (1,200) Margin -4.5% Q1 2023 revenue 310 Q2 2023 revenue 295"
    bad = [("| Net income | 1,200 |", "sign", ["1,200"]),
           ("| Margin | 4.5% |", "sign", ["4.5"]),
           ("| Q1 | 295 |\n| Q2 | 310 |", "wrong_row", ["295", "310"]),
           ("| x | 12,00 |", "unverified", ["12,00"])]
    for ans, kind, vals in bad:
        marked, iss, _ = pv.check_answer(ans, src)
        check(f"flagged as {kind}: {ans!r}", iss[kind] == vals and all(v + " ⚠" in marked for v in vals), iss)
    for ans in ("| Net income | (1,200) |", "| Net income | -1,200 |", "| Margin | -4.5% |",
                "| Q1 | 310 |\n| Q2 | 295 |", "| Year | 2023 |", "| Q1 | ~300 |"):
        marked, iss, _ = pv.check_answer(ans, src)
        check(f"clean: {ans!r}", "⚠" not in marked and not flagged(iss), iss)
    _, iss, _ = pv.check_answer("| Period | 2019-2020 |", "Period 2019-2020")
    check("range dash is not a minus", not any(iss.values()), iss)
    _, iss, _ = pv.check_answer("| Due | 07/2024 |", "Period 07/2023 -07/2024")
    check("date parts match either sign", not flagged(iss), iss)
    _, iss, _ = pv.check_answer("| Alpha | 34.8 |", "Scores 34.8 28.3\nunrelated line")
    check("row label not on the page: row not confirmed, no ⚠",
          iss["row_unconfirmed"] == ["34.8"] and not iss["wrong_row"], iss)
    _, iss, _ = pv.check_answer("| Alpha | 34.8 |\n| Alpha Securities | 34.8 |", "Alpha Securities 34.8 1")
    check("nested labels share their line", not any(iss.values()), iss)
    _, iss, _ = pv.check_answer("| Alpha | 34.8 |", "Alpha Beta\n34.8 28.3")
    check("values on the line below their label", not any(iss.values()), iss)


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
        check(f"scan {angle}: nothing else flagged",
              not any(entry(rep, t["id"])[k] for k in ("sign", "wrong_row")), entry(rep, t["id"]))
        swapped = ANSWER.replace("| Alpha Securities | 34.8 | 1 |", "| Alpha Securities | 28.3 | 1 |") \
                        .replace("| Beta Capital | 28.3 | 2 |", "| Beta Capital | 34.8 | 2 |")
        write_answer(work, t["id"], swapped)
        md2, rep2 = pv.merge(work)
        check(f"scan {angle}: swapped scores flagged as in another row",
              entry(rep2, t["id"])["wrong_row"] == ["28.3", "34.8"] and "| 28.3 ⚠ | 1 |" in md2, entry(rep2, t["id"]))
        check(f"scan {angle}: summary names it", "IN ANOTHER ROW: 28.3, 34.8" in pv.summary(rep2), pv.summary(rep2))
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
            e = entry(rep, t["id"])
            unverified = set(e["unverified"]) | set(e["sign"]) | set(e["wrong_row"])
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


def text_blank_pdf(path, last_is_scan):
    """page 1 text, page 2 blank, page 3 text or the scan."""
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(path, pagesize=(612, 792))
    c.setFont("Helvetica", 11)
    c.drawString(72, 720, "Page one text with value 100.")
    c.showPage()
    c.showPage()
    if last_is_scan:
        png = path + ".png"
        scan_image().save(png)
        c.drawImage(png, 0, 0, 612, 792)
    else:
        c.setFont("Helvetica", 11)
        c.drawString(72, 720, "Page three text with value 300.")
    c.showPage()
    c.save()
    return path


def test_blank_page():
    with tempfile.TemporaryDirectory() as tmp:
        work = os.path.join(tmp, "w1")
        pv.prepare(text_blank_pdf(os.path.join(tmp, "a.pdf"), False), work)
        md, _ = pv.merge(work)
        check("text/blank/text: no marker leaks without page markers", "<!--" not in md and "---" not in md, md)
        check("text/blank/text: each page once", md.count("Page one") == 1 and md.count("Page three") == 1, md)
        md, _ = pv.merge(work, page_markers=True)
        check("text/blank/text: one marker per later page",
              md.count("<!-- page 2 -->") == 1 and md.count("<!-- page 3 -->") == 1 and "<!-- page 1 -->" not in md, md)
        work = os.path.join(tmp, "w2")
        man = pv.prepare(text_blank_pdf(os.path.join(tmp, "b.pdf"), True), work)
        t = [t for t in man["tasks"] if t["kind"] == "page"]
        check("text/blank/scan: scan task is page 3", [x["page"] for x in t] == [3], man["tasks"])
        write_answer(work, "p3_page", ANSWER)
        md, rep = pv.merge(work)
        check("text/blank/scan: answer merged once, OCR draft not repeated",
              md.count("Broker Review") == 1 and md.count("![Page 3 (scan)]") == 1, md)
        check("text/blank/scan: page 1 kept", "Page one text" in md, md[:200])


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
        check("no answer: same text as draft", md.strip() == draft.replace("<!-- page 1 -->", "").strip())


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
    test_sign_and_position()
    test_requires_tesseract()
    if HAVE_OCR:
        test_vector_chart()
        for a in (0, 90, 180, 270):
            test_scanned(a)
        test_jpeg_scan_safety_net()
        test_page_markers()
        test_blank_page()
        test_missing_answer()
        test_cli()
    else:
        print("SKIP prepare/merge checks: the vision step needs Tesseract")
    print("all passed" if not fails else f"{fails} FAILED")
    sys.exit(1 if fails else 0)
