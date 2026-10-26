"""Tests for the vision step in pdf2md.py (prepare -> model answers -> merge).
The model is played by canned answers; all data is invented."""
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


def check(name, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {detail}"))
    fails += 0 if cond else 1


def check_ocr(name, cond, detail=""):
    """A check that needs Tesseract: skipped, not failed, when it isn't installed."""
    if HAVE_OCR:
        check(name, cond, detail)
    else:
        print(f"SKIP {name}  (needs Tesseract)")


def entry(rep, tid):
    return next(e for e in rep if e["id"] == tid)


def scanned_table_pdf(path, angle=0):
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
    im.rotate(angle, expand=True).save(path, resolution=200)
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
        check(f"scan {angle}: one page task", len(pages) == 1, man["tasks"])
        t = pages[0]
        from PIL import Image
        with Image.open(os.path.join(work, t["image"])) as im:
            (check if angle == 0 else check_ocr)(
                f"scan {angle}: page image upright (portrait)", im.height > im.width, im.size)
        check_ocr(f"scan {angle}: OCR text available to check against", "34.8" in t["check_text"],
              t["check_text"][:300])
        answer = ("# Broker Review\n\nScores for the period are shown below.\n\n"
                  "| Broker | Score | Rank |\n|---|---|---|\n| Alpha Securities | 34.8 | 1 |\n"
                  "| Beta Capital | 28.3 | 2 |\n| Gamma Partners | 17.5 | 3 |\n\n"
                  "Chart\n\n| Broker | Score |\n|---|---|\n| Alpha | 34.8 |\n| Beta | 28.3 |\n"
                  "| Gamma | 17.5 |\n| Delta | 12.9 |\n")
        write_answer(work, t["id"], answer)
        md, rep = pv.merge(work, page_markers=True)
        check(f"scan {angle}: table in output", "| Alpha Securities | 34.8" in md, md)
        check_ocr(f"scan {angle}: printed values not flagged", "| Alpha Securities | 34.8 | 1 |" in md, md)
        check_ocr(f"scan {angle}: invented row flagged",
                  "12.9 ⚠" in md and entry(rep, t["id"])["unverified"] == ["12.9"], rep)
        check(f"scan {angle}: scan image linked", "![Page 1 (scan)](scans/page_1.png)" in md)
        check(f"scan {angle}: page marker kept on request", "<!-- page 1 -->" in md)


def test_missing_answer():
    with tempfile.TemporaryDirectory() as tmp:
        pdf = scanned_table_pdf(os.path.join(tmp, "scan.pdf"))
        work = os.path.join(tmp, "work")
        pv.prepare(pdf, work)
        with open(os.path.join(work, "draft.md")) as fh:
            draft = fh.read()
        md, rep = pv.merge(work)
        check_ocr("no answer: OCR output kept", "Broker Review" in md, md)
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
    test_vector_chart()
    for a in (0, 90, 180):
        test_scanned(a)
    test_missing_answer()
    test_cli()
    if not HAVE_OCR:
        print("Tesseract not installed: OCR checks skipped")
    print("all passed" if not fails else f"{fails} FAILED")
    sys.exit(1 if fails else 0)
