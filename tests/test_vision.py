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


def hard_pages_pdf(path):
    """page 1: one column with a ruled table; page 2: two text columns; page 3: plain prose."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import (BaseDocTemplate, Frame, NextPageTemplate, PageBreak, PageTemplate,
                                    Paragraph, Table, TableStyle)
    ss = getSampleStyleSheet()
    W, H = letter
    doc = BaseDocTemplate(path, pagesize=letter)
    one = Frame(72, 72, W - 144, H - 144, id="one")
    left = Frame(72, 72, (W - 144) / 2 - 12, H - 144, id="l")
    right = Frame(72 + (W - 144) / 2 + 12, 72, (W - 144) / 2 - 12, H - 144, id="r")
    doc.addPageTemplates([PageTemplate("one", [one]), PageTemplate("two", [left, right]),
                          PageTemplate("one2", [one])])
    body = ("Desk volumes moved with the market over the quarter while costs held flat "
            "against the plan approved by the committee in the spring review. ")
    rows = [["Desk", "Q1", "Q2"], ["Rates", "1,250", "(300)"], ["Credit", "980", "1,410"],
            ["Equities", "2,045", "1,990"]]
    t = Table(rows)
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black)]))
    story = [Paragraph("Quarterly Desk Summary", ss["Heading1"]), Paragraph(body, ss["BodyText"]), t,
             NextPageTemplate("two"), PageBreak()]
    story += [Paragraph(f"Column paragraph {i + 1}. " + body * 3, ss["BodyText"]) for i in range(8)]
    story += [NextPageTemplate("one2"), PageBreak(), Paragraph("Closing note. " + body * 2, ss["BodyText"])]
    doc.build(story)
    return path


def test_hard_pages_prepare():
    with tempfile.TemporaryDirectory() as tmp:
        pdf = hard_pages_pdf(os.path.join(tmp, "hard.pdf"))
        man = pv.prepare(pdf, os.path.join(tmp, "off"))
        check("hard pages off by default: no tables/layout tasks",
              not [t for t in man["tasks"] if t["kind"] in ("tables", "layout")], man["tasks"])
        work = os.path.join(tmp, "w")
        man = pv.prepare(pdf, work, hard_pages=True)
        kinds = {t["page"]: t["kind"] for t in man["tasks"]}
        check("hard pages: page 1 (one column + table) -> tables task", kinds.get(1) == "tables", man["tasks"])
        check("hard pages: page 2 (two columns) -> layout task", kinds.get(2) == "layout", man["tasks"])
        check("hard pages: page 3 (plain prose) -> no task", 3 not in kinds, man["tasks"])
        t1 = next(t for t in man["tasks"] if t["page"] == 1)
        check("tables task: counts the page's tables", t1.get("tables") == 1, t1)
        check("tables task: page image saved", os.path.exists(os.path.join(work, t1["image"])), t1["image"])
        check("tables task: prompt asks for the tables only, with our draft as context",
              "only the tables" in t1["prompt"] and "Rates" in t1["prompt"], t1["prompt"][:300])
        check("tables task: checked against the native text", "(300)" in t1["check_text"] and "1,410" in t1["check_text"],
              t1["check_text"][:300])
        t2 = next(t for t in man["tasks"] if t["page"] == 2)
        check("layout task: whole page in reading order", "reading order" in t2["prompt"], t2["prompt"][:200])
        man = pv.prepare(pdf, os.path.join(tmp, "cap"), hard_pages=True, max_hard_pages=1)
        check("max_hard_pages=1: one hard task, the first", [t["page"] for t in man["tasks"]
                                                             if t["kind"] in ("tables", "layout")] == [1], man["tasks"])
        md, rep = pv.merge(work)
        check("hard-page task without an answer: rule-based output kept",
              "| Rates | 1,250 | (300) |" in md and entry(rep, "p1_tables")["status"].startswith("no answer"), rep)

        scan = scanned_table_pdf(os.path.join(tmp, "scan.pdf"))
        man = pv.prepare(scan, os.path.join(tmp, "ws"), hard_pages=True)
        check("scanned page: only its page task, not a hard-page task too",
              [t["kind"] for t in man["tasks"]] == ["page"], man["tasks"])

        out = os.path.join(tmp, "wcli")
        import subprocess
        r = subprocess.run([sys.executable, os.path.join(os.path.dirname(HERE), "pdf2md.py"), "prepare", pdf, out,
                            "--hard-pages", "--max-hard-pages", "5"], capture_output=True, text=True)
        check("cli prepare --hard-pages lists them with a reason",
              r.returncode == 0 and "p1_tables: tables, page 1" in r.stdout and "1 table" in r.stdout
              and "p2_layout: layout, page 2" in r.stdout, r.stdout + r.stderr[-300:])


GOOD_TABLE = ("| Desk | Q1 | Q2 |\n|:---|---:|---:|\n| Rates | 1,250 | (300) |\n"
              "| Credit | 980 | 1,410 |\n| Equities | 2,045 | 1,990 |")


def test_hard_pages_merge():
    with tempfile.TemporaryDirectory() as tmp:
        pdf = hard_pages_pdf(os.path.join(tmp, "hard.pdf"))
        work = os.path.join(tmp, "w")
        pv.prepare(pdf, work, hard_pages=True)
        with open(os.path.join(work, "draft.md")) as fh:
            ours = pv._split_pages(fh.read())

        def run(tid, answer):
            write_answer(work, tid, answer)
            md, rep = pv.merge(work)
            return md, entry(rep, tid)

        md, e = run("p1_tables", GOOD_TABLE)
        check("tables: correct answer replaces our table", "|:---|---:|---:|" in md and e["status"] == "merged 1 table", e)
        check("tables: text around the table kept", "Desk volumes moved" in md and "# Quarterly Desk Summary" in md)
        check("tables: no warning marks in an accepted table", "⚠" not in md)
        for name, ans, why in (
                ("wrong number", GOOD_TABLE.replace("1,250", "1,520"), "NOT FOUND"),
                ("sign dropped", GOOD_TABLE.replace("(300)", "300"), "SIGN DIFFERS"),
                ("rows swapped", GOOD_TABLE.replace("| 980 | 1,410 |", "| 2,045 | 1,990 |", 1)
                 .replace("| Equities | 2,045 | 1,990 |", "| Equities | 980 | 1,410 |"), "IN ANOTHER ROW"),
                ("two tables for one", GOOD_TABLE + "\n\n| A | B |\n|---|---|\n| x | 1 |", "table count"),
                ("estimated value", GOOD_TABLE.replace("1,410", "~1,410"), "estimate"),
                ("no table at all", "The table shows desk volumes.", "table count")):
            md, e = run("p1_tables", ans)
            check(f"tables, {name}: ours kept", "|:---|" not in md and "| Rates | 1,250 | (300) |" in md, md[:400])
            check(f"tables, {name}: reason given", e["status"].startswith("kept rule-based") and why in pv.summary([e]),
                  pv.summary([e]))
            check(f"tables, {name}: our table unmarked", "⚠" not in md)
        write_answer(work, "p1_tables", GOOD_TABLE)

        good = ours[2]
        md, e = run("p2_layout", good)
        check("layout: faithful answer replaces the page", e["status"] == "merged page", e)
        para = "while costs held flat against the plan approved by the committee in the spring review."
        for name, ans, why in (
                ("paraphrased sentence", good.replace(para, "and costs stayed flat as planned.", 1), "words"),
                ("paragraph dropped", "\n\n".join(good.split("\n\n")[1:]), "missing"),
                ("sentence invented", good + "\n\nRevenue rose sharply after the merger closed.", "words"),
                ("number changed", good.replace("Column paragraph 3.", "Column paragraph 9."), "NOT FOUND")):
            md, e = run("p2_layout", ans)
            check(f"layout, {name}: ours kept", e["status"].startswith("kept rule-based") and good.split("\n\n")[0] in md, e)
            check(f"layout, {name}: reason given", why in pv.summary([e]), pv.summary([e]))


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
        test_hard_pages_prepare()
        test_hard_pages_merge()
        test_missing_answer()
        test_cli()
    else:
        print("SKIP prepare/merge checks: the vision step needs Tesseract")
    print("all passed" if not fails else f"{fails} FAILED")
    sys.exit(1 if fails else 0)
