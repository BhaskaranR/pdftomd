#!/usr/bin/env python3
"""Charts and scanned pages for pdf2md, read by the agent's own vision model.

Two steps around the model, so no API keys or network calls are needed here:

  python pdf2md_vision.py prepare report.pdf work/   # convert + images + work/tasks.json
  (the agent opens each image in tasks.json, follows its prompt,
   and writes the answer to work/answers/<id>.md)
  python pdf2md_vision.py merge work/ -o report.md   # check numbers, build final markdown

Every number in an answer is looked up in the page's own text (native text, or
Tesseract OCR for scans and pictures). Numbers that aren't found get a warning
sign; values the model read off the drawing are written with a leading ~.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

import pdfplumber

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdf2md  # noqa: E402

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

NUM_RE = re.compile(r"(~\s*)?(?<![\w.])(\d[\d,]*(?:\.\d+)?)")


def norm_number(s: str) -> str:
    """'1,200' -> '1200', '34.80' -> '34.8', '05' -> '5'."""
    s = s.replace(",", "")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s.lstrip("0") or "0"


def numbers_in(text: str) -> set:
    return {norm_number(m.group(2)) for m in NUM_RE.finditer(text) if not m.group(1)}


def check_answer(answer: str, source: str):
    """Mark unverified numbers in the answer. Returns (marked text, unverified, estimated)."""
    known = numbers_in(source)
    unverified, estimated = [], 0

    def mark(m):
        nonlocal estimated
        if m.group(1):
            estimated += 1
            return m.group(0)
        if norm_number(m.group(2)) in known:
            return m.group(0)
        unverified.append(m.group(2))
        return m.group(0) + " \u26a0"
    marked = NUM_RE.sub(mark, answer)
    return marked, unverified, estimated


# ---- prepare ----

def _upright(img):
    rot = pdf2md.detect_rotation(img) if pdf2md.ocr_available() else 0
    img = img.rotate(-rot, expand=True) if rot else img
    img.thumbnail((MAX_SIDE_PX, MAX_SIDE_PX))
    return img


def _ocr_text(img) -> str:
    if not pdf2md.ocr_available():
        return ""
    paras = pdf2md.ocr_image(pdf2md.flatten_alpha(img)) or []
    return "\n".join(p["text"] for p in paras)


def prepare(src, work, mode="auto", page_range=None, max_pages=None, password=None):
    os.makedirs(os.path.join(work, "answers"), exist_ok=True)
    img_dir = os.path.join(work, "images")
    r = pdf2md.convert(src, mode=mode, page_range=page_range, max_pages=max_pages,
                       password=password, page_markers=True, images="save",
                       image_dir=img_dir, link_base=work)
    with open(os.path.join(work, "draft.md"), "w", encoding="utf-8") as f:
        f.write(r.markdown)
    page_md = {p["pageNumber"]: p["markdown"] for p in r.pages}
    have_ocr = pdf2md.ocr_available()
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
    data, _, _ = pdf2md.load_source(src)
    import io
    with pdfplumber.open(io.BytesIO(data), password=password or "") as pdf:
        for pageno in sorted(page_md):
            page = pdf.pages[pageno - 1]
            if pdf2md.page_stats(page)["kind"] not in ("scan", "hybrid") and mode != "ocr":
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

    manifest = {"source": r.source_name, "pdf_type": r.pdf_type, "ocr_available": have_ocr,
                "pages": sorted(page_md), "tasks": tasks}
    with open(os.path.join(work, "tasks.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    return manifest


# ---- merge ----

PAGE_SPLIT = re.compile(r"<!-- page (\d+) -->\n?")


def _split_pages(md: str) -> dict:
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
        if t["kind"] == "picture" and answer.upper().startswith("NONE"):
            entry["status"] = "not a chart or table"
            report.append(entry)
            continue
        marked, unverified, estimated = check_answer(answer, t["check_text"])
        entry.update(status="merged", unverified=unverified, estimated=estimated)
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
        if page_markers:
            out.append(f"<!-- page {pn} -->")
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
        if e.get("unverified"):
            s += f"; NOT FOUND in page text: {', '.join(e['unverified'])}"
        if e.get("estimated"):
            s += f"; {e['estimated']} estimated value(s)"
        if e.get("note"):
            s += f"; {e['note']}"
        lines.append(s)
    return "\n".join(lines) or "no charts or scanned pages"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare", help="convert the PDF and list images for the model")
    p.add_argument("pdf")
    p.add_argument("work", help="work folder (created)")
    p.add_argument("--mode", default="auto", choices=["auto", "ocr"])
    p.add_argument("--page-range")
    p.add_argument("--max-pages", type=int)
    p.add_argument("--password")
    m = sub.add_parser("merge", help="check the model's answers and write the final markdown")
    m.add_argument("work")
    m.add_argument("-o", "--output", help="markdown file (default: print)")
    m.add_argument("--page-markers", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "prepare":
        man = prepare(a.pdf, a.work, a.mode, a.page_range, a.max_pages, a.password)
        print(f"{len(man['tasks'])} image task(s) in {os.path.join(a.work, 'tasks.json')}")
        for t in man["tasks"]:
            print(f"  {t['id']}: {t['kind']}, page {t['page']}, image {t['image']}")
        if not man["ocr_available"]:
            print("warning: Tesseract not available; numbers on scans/pictures can't be checked")
        return
    md, report = merge(a.work, a.page_markers)
    if a.output:
        with open(a.output, "w", encoding="utf-8") as f:
            f.write(md)
    else:
        sys.stdout.write(md)
    print(summary(report), file=sys.stderr)


if __name__ == "__main__":
    main()
